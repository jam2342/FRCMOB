# The Monday job for a state-served season, as rehearsed (PRs #153/#154/#160/#161): a safety
# check against the frozen bootstrap and the gradual early-fast fit at 09:00 UTC, publication at
# 10:00 if the rating-impact check passes under the candidate's own limits. It runs outside web
# workers (scripts/fit_rating_calibrations.py) and only with RATING_AUTOTUNE on.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db import models
from app.services.ratings import calibrations as layers, gradual_rehearsal as gradual, state_serving as serving
from app.services.ratings import weekly_rehearsal as weekly
from app.services.ratings.research_eval import paired_cluster_bootstrap
from app.services.ratings.state_bootstrap import BOOTSTRAP_RECIPE, validate_params
from app.services.ratings.state_inputs import inputs_for

SAFETY_DAYS = 14
SAFETY_MATCHES = 150


class ProductionReplay:
    # What the rehearsed fit reads from its replay, built from results posted before a cutoff.
    def __init__(self, db, season, bootstrap, cutoff):
        context = bootstrap.params["context"]
        self.inputs, self.data_sha256, _ = inputs_for(db, season, int(cutoff), context, updated_before=int(cutoff))
        self.ordered = [{"result": r} for r in self.inputs.research.season.results]
        self.total = {}
        for record in self.ordered:
            for team in record["result"].card.red+record["result"].card.blue:
                self.total[team] = self.total.get(team, 0)+1
        self.models = {}

    def new_model(self, params):
        return self.inputs.model(params)

    def add(self, identifier, params):
        model = self.new_model(params)
        for record in self.ordered:
            model.observe(record["result"])
        self.models[identifier] = model
        return model


def _params(db, row):
    params, _layers, _bootstrap = serving.resolve(db, row)
    return params


def _bootstrap(db, season):
    return db.query(models.RatingCalibration).filter_by(season=season, recipe=BOOTSTRAP_RECIPE).filter(
        models.RatingCalibration.status != "rejected").order_by(models.RatingCalibration.version.desc()).first()


def _enabled(db, season):
    if not settings.rating_autotune or not serving.declared(season):
        return None, {"status": "disabled"}
    bundle = layers.ensure_bundle(db, season)
    if bundle.frozen or db.get(models.FrozenSeason, season):
        return None, {"status": "frozen"}
    active = serving.active_version(db, season)
    if active is None:
        return None, {"status": "no_active_version"}
    return active, None


def safety(db, season, *, now):
    # Roll back to the frozen bootstrap when the active version's served forecasts were worse
    # than the bootstrap's, captured at the same moments, over the last two weeks (90% interval).
    active, stop = _enabled(db, season)
    if stop:
        return stop
    if active.recipe == BOOTSTRAP_RECIPE:
        return {"status": "baseline"}
    bootstrap = _bootstrap(db, season)
    since = (now-timedelta(days=SAFETY_DAYS)).timestamp()
    results = {}
    for r in db.query(models.MatchAllianceResult).filter_by(season_year=season):
        if r.total_score is not None and r.total_score >= 0:
            results.setdefault(r.match_key, {})[r.alliance] = r.total_score
    matches = {m.match_key: m for m in db.query(models.Match).join(models.Event).filter(models.Event.year == season)}
    served, baseline = {}, {}
    for log in db.query(models.MatchPredictionLog).filter_by(season=season, backfilled=False):
        match, sides = matches.get(log.match_key), results.get(log.match_key, {})
        features = log.features or {}
        if match is None or match.actual_time is None or not since <= match.actual_time < now.timestamp() or {"red", "blue"} - sides.keys():
            continue
        if features.get("schema") != serving.STATE_FEATURE_SCHEMA or features.get("version") != active.id or "bootstrap" not in features:
            continue
        if layers.utc(log.final_at).timestamp() >= match.actual_time or sides["red"] == sides["blue"]:
            continue
        y = float(sides["red"] > sides["blue"])
        served[log.match_key] = (log.event_key, (float(log.final["red_win_prob"])-y)**2)
        baseline[log.match_key] = (log.event_key, (float(features["bootstrap"]["p"])-y)**2)
    comparison = paired_cluster_bootstrap(served, baseline, seed=7)
    if comparison.get("n", 0) < SAFETY_MATCHES or comparison["lo90"] <= 0:
        return {"status": "kept", "comparison": comparison}
    bundle = layers.ensure_bundle(db, season)
    active.status = "rolled_back"
    bundle.previous_id, bundle.calibration_id = None, bootstrap.id
    bundle.bundle_version += 1
    bundle.updated_at = now
    db.flush()
    return {"status": "rolled_back", "comparison": comparison, "bundle_version": bundle.bundle_version}


def fit(db, season, *, now, code_sha):
    # 09:00: the rehearsed gradual early-fast fit with the fixed earlier-season calibration prior.
    active, stop = _enabled(db, season)
    if stop:
        return stop
    week = layers.monday(now)
    replay = ProductionReplay(db, season, _bootstrap(db, season), week.timestamp())
    model = replay.add(active.id, _params(db, active))
    # The weekly 0-100 anchors move 5% toward the active model's population; a version is
    # immutable, so the candidate carries the anchors it will be shown with.
    previous = active.params.get("anchors") if active.recipe != BOOTSTRAP_RECIPE else None
    _impact, anchors = weekly.rating_impact(model, model, replay.total, previous)
    return gradual.fit_job(db, season, week, code_sha, replay, active.id, previous, calibration_prior="bootstrap",
                           schedule=gradual.EARLY_FAST, store_anchors=anchors)


def publish(db, season, *, now):
    # 10:00: this Monday's candidate goes live if its rating impact passes under the limits it was
    # selected with and its fit fit inside the hour; otherwise the active version stays.
    active, stop = _enabled(db, season)
    if stop:
        return stop
    week = layers.monday(now)
    if now < week+timedelta(hours=weekly.POLICY["publication_hour_utc"]):
        return {"status": "before_publication"}
    candidate = db.query(models.RatingCalibration).filter_by(season=season, recipe=gradual.EARLY_FAST["recipe"],
        fitted_through=week, status="candidate").order_by(models.RatingCalibration.version.desc()).first()
    if candidate is None or candidate.params.get("parent_active") != active.id:
        return {"status": "no_candidate"}
    seconds = (candidate.fit_metrics or {}).get("rehearsal_seconds")
    if seconds is None or seconds > (weekly.POLICY["publication_hour_utc"]-weekly.POLICY["job_hour_utc"])*3600:
        return {"status": "over_budget"}
    replay = ProductionReplay(db, season, _bootstrap(db, season), now.timestamp())
    old = replay.add(active.id, _params(db, active))
    new = replay.add(candidate.id, validate_params(candidate.params["state"]))
    previous = active.params.get("anchors") if active.recipe != BOOTSTRAP_RECIPE else None
    impact, _ = weekly.rating_impact(old, new, replay.total, previous, candidate.params.get("impact_limits"))
    if not impact["passed"]:
        return {"status": "impact_refused", "impact": impact}
    bundle = layers.ensure_bundle(db, season)
    bundle.previous_id, bundle.calibration_id = active.id, candidate.id
    bundle.bundle_version += 1
    bundle.updated_at = now
    if active.recipe != BOOTSTRAP_RECIPE:
        active.status = "retired"  # the bootstrap stays as it is: the safety baseline and rollback target
    candidate.status, candidate.activated_at = "active", now
    db.flush()
    return {"status": "published", "id": candidate.id, "impact": impact, "bundle_version": bundle.bundle_version}


def install(db, season, artifact, code_sha, *, now=None):
    # Pre-season: store the earlier-season bootstrap (v2 layers) and make it the active version.
    from app.services.ratings.state_fitting import install_bootstrap
    if not serving.declared(season):
        raise ValueError("state serving is off or the season is not declared")
    if not layers.is_v2(artifact["layers"]):
        raise ValueError("the served bootstrap needs v2 score layers")
    row = install_bootstrap(db, artifact, code_sha)
    bundle = layers.ensure_bundle(db, season)
    if bundle.calibration_id is None:
        bundle.calibration_id = row.id
        bundle.bundle_version += 1
        bundle.updated_at = now or datetime.now(timezone.utc)
    db.flush()
    return {"status": "installed", "id": row.id, "active": bundle.calibration_id}
