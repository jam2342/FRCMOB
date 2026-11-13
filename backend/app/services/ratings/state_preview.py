# Read-only current-state inspection, never retrospective prediction evidence.
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import math

from sqlalchemy import func

from app.db import models
from app.services.ratings import calibrations as layers
from app.services.ratings.component_spec import get_spec
from app.services.ratings.constants import SEASON_BASELINES
from app.services.ratings.state_bootstrap import BOOTSTRAP_RECIPE, RECIPE, validate_bootstrap, validate_params
from app.services.ratings.state_inputs import digest, inputs_for
from app.services.ratings.strength import curve_for_anchors, points_to_rating
from app.services.ratings.total_adapter import rating_confidence, replay_adapter


def build_preview(db, calibration_id, event_key, *, now=None):
    now = layers.utc(now or datetime.now(timezone.utc))
    version = db.get(models.RatingCalibration, calibration_id)
    if version is None or version.recipe not in (RECIPE, BOOTSTRAP_RECIPE) or version.status == "rejected":
        raise ValueError("unknown or rejected total-state calibration")
    event = db.get(models.Event, event_key)
    if event is None or event.year != version.season:
        raise ValueError("preview event must belong to the calibration season")
    spec = get_spec(version.season)
    if spec.tier != "A":
        raise ValueError("the total-state adapter requires a declared Tier A spec")
    bootstrap = version if version.recipe == BOOTSTRAP_RECIPE else db.get(models.RatingCalibration, version.params["bootstrap_id"])
    if bootstrap is None or bootstrap.season != version.season or bootstrap.recipe != BOOTSTRAP_RECIPE or bootstrap.status == "rejected":
        raise ValueError("missing or invalid state bootstrap")
    if any(max(layers.utc(v.created_at), layers.utc(v.fitted_through)) > now for v in (version, bootstrap)):
        raise ValueError("preview calibration is from the future")
    artifact = bootstrap.params["artifact"]
    validate_bootstrap(artifact, version.season)
    params = artifact["params"] if version.recipe == BOOTSTRAP_RECIPE else version.params["state"]
    if version.recipe == RECIPE and version.params.get("bootstrap_sha256") != digest(bootstrap.params):
        raise ValueError("state bootstrap provenance mismatch")
    inputs, data_sha, skipped = inputs_for(db, version.season, now.timestamp(), bootstrap.params["context"])
    run = replay_adapter(inputs, validate_params(params), spec)
    observed = sum(value is not None for r in run.results for value in (r.red_points, r.blue_points))
    ready = observed >= 24
    baseline = SEASON_BASELINES.get(version.season)
    scale = {"source": "live_season_baseline", "median_points": baseline["median_robot_points"],
             "p99_points": baseline["p99_robot_points"]} if baseline else None
    curve = curve_for_anchors(scale["median_points"], scale["p99_points"]) if scale else None
    def rating(points):
        return points_to_rating(points, *curve) if ready and curve else None

    # Union stored roster spelling with observed participants, including B teams.
    teams = {t.lower(): t for (t,) in db.query(models.EventTeam.team_key).filter_by(event_key=event_key)}
    for result in run.results:
        if result.card.event_key == event_key.lower():
            for team in result.card.red+result.card.blue:
                teams.setdefault(team, team)
    profiles = {t.team_key.lower(): t for t in db.query(models.Team).filter(func.lower(models.Team.team_key).in_(teams))}
    rows = []
    for key, stored_key in sorted(teams.items()):
        profile = profiles.get(key)
        belief = run.model.belief(key, event_key.lower())["total"]
        sd = math.sqrt(belief.var)
        here = run.played_here.get((key, event_key.lower()), 0)
        before = run.played_total.get(key, 0)-here
        basis = "this_event" if here else "earlier_events" if before else "last_season" if key in inputs.z_previous else "rookie" if key in inputs.rookies else "season_typical"
        rows.append({"event_key": event_key, "team_key": profile.team_key if profile else stored_key,
            "team_number": profile.team_number if profile else None, "nickname": profile.nickname if profile else None,
            "rating_0_100": rating(belief.mean), "confidence_0_1": rating_confidence(run.model,key,event_key.lower()),
            "expected_points": {"total": belief.mean} if ready else None, "basis": basis,
            "matches_here": here, "matches_before": before,
            "rating_range_80": [rating(belief.mean-1.2815515655*sd), rating(belief.mean+1.2815515655*sd)] if ready and curve else None,
            "details": {"components": ["total"], "total_points_sd": sd if ready else None,
                        "uncertainty": "latent team contribution; excludes alliance match noise"},
            "model_version": RECIPE})
    return {"ok": True, "event_key": event_key, "event_name": event.name, "model_version": RECIPE,
        "source": "state_candidate_preview", "count": len(rows), "ratings": rows, "calibration_id": version.id,
        "as_of": now.isoformat(), "last_updated_at": now.isoformat(), "serving_eligible": False,
        "component_spec": {**asdict(spec), "sha256": spec.fingerprint}, "points_ready": ready,
        "provisional": version.recipe == BOOTSTRAP_RECIPE or not ready, "rating_scale": scale,
        "provenance": {"calibration_sha256": digest(version.params), "bootstrap_id": bootstrap.id,
            "bootstrap_sha256": digest(bootstrap.params), "data_sha256": data_sha,
            "fitted_through": layers.utc(version.fitted_through).isoformat(), "observed_alliances": observed,
            "skipped_inputs": skipped},
        "limitations": ["Current-state preview, not prospective accuracy evidence",
            "Auto, teleop, climb, driver and ranking-point estimates are unavailable",
            "Total points exclude fouls; no alliance score calibration is allocated to individual teams",
            "Points require 24 observed alliance scores; 0–100 ratings also require declared season anchors",
            "Serving integration, rating-impact gate and full rehearsal remain required"]}
