from __future__ import annotations

import copy
from datetime import datetime, timezone
import math

import numpy as np

from app.core.config import settings
from app.db import models
from app.services.ratings import calibrations as layers
from app.services.ratings.replay import MatchCard
from app.services.ratings.research_eval import paired_cluster_bootstrap
from app.services.ratings.state_bootstrap import BOOTSTRAP_RECIPE, RECIPE, validate_params
from app.services.ratings.state_fitting import feature, latest, replay
from app.services.ratings.state_inputs import digest, inputs_for, results_digest


def capture(db, season, event_keys, *, now=None):
    if not settings.rating_state_fit_enabled or not settings.rating_prediction_log_enabled:
        return {"status": "disabled"}
    now = layers.utc(now or datetime.now(timezone.utc))
    if db.get(models.FrozenSeason, season):
        return {"status": "season_frozen"}
    bootstrap = latest(db, season, BOOTSTRAP_RECIPE)
    if bootstrap is None:
        return {"status": "missing_state_bootstrap"}
    candidates = db.query(models.RatingCalibration).filter_by(season=season, recipe=RECIPE, status="candidate").order_by(
        models.RatingCalibration.version.desc()).all()
    candidates = [c for c in candidates if c.params["bootstrap_id"] == bootstrap.id and max(layers.utc(c.created_at),layers.utc(c.fitted_through)) <= now][:2]
    versions = [bootstrap, *candidates]
    inputs, data_sha, skipped = inputs_for(db, season, now.timestamp(), bootstrap.params["context"])
    source_sha = results_digest(inputs.research.season.results)
    matches = db.query(models.Match).filter(models.Match.event_key.in_(event_keys), models.Match.actual_time.is_(None)).all()
    teams = {}
    for team in db.query(models.MatchTeam).filter(models.MatchTeam.event_key.in_(event_keys)):
        teams.setdefault(team.match_key, {}).setdefault(team.alliance, []).append(team.team_key.lower())
    logs = {r.match_key:r for r in db.query(models.MatchPredictionLog).filter(models.MatchPredictionLog.event_key.in_(event_keys))}
    completed = {r.match_key for r in db.query(models.MatchAllianceResult).filter(
        models.MatchAllianceResult.event_key.in_(event_keys), models.MatchAllianceResult.total_score >= 0)}
    first = min((r.card.event_start for r in inputs.research.season.results), default=int(now.timestamp()))
    starts = {event: min(m.predicted_time or m.time for m in matches if m.event_key == event and (m.predicted_time or m.time))
              for event in event_keys if any(m.event_key == event and (m.predicted_time or m.time) for m in matches)}
    for result in inputs.research.season.results:
        starts[result.card.event_key] = min(starts.get(result.card.event_key, result.card.event_start), result.card.event_start)
    added = 0
    for version in versions:
        if max(layers.utc(version.created_at), layers.utc(version.fitted_through)) > now:
            continue
        params = version.params["artifact"]["params"] if version.recipe == BOOTSTRAP_RECIPE else version.params["state"]
        fitted_layers = version.params["artifact"]["layers"] if version.recipe == BOOTSTRAP_RECIPE else version.params["layers"]
        model, _, here, total = replay(inputs, validate_params(params), record=False)
        forecasts = {}
        for match in sorted(matches, key=lambda m:m.match_key):
            start = match.predicted_time or match.time
            source = logs.get(match.match_key)
            sides = teams.get(match.match_key, {})
            if not start or start <= now.timestamp() or match.match_key in completed or source is None or source.locked_at is not None or source.backfilled:
                continue
            if source.features.get("schema") != layers.SCHEMA or source.features.get("results_sha256") != source_sha or layers.utc(source.final_at) > now:
                continue
            if set(sides) != {"red", "blue"} or any(len(sides[c]) != 3 for c in ("red", "blue")) or len(set(sides["red"]+sides["blue"])) != 6:
                continue
            if source.features.get("scheduled_start") != start or any(
                sorted(source.features.get(f"{color}_teams",[])) != sorted(sides[color]) for color in ("red","blue")):
                continue
            # First prospective forecast per version/match; both comparators use this capture.
            if db.get(models.RatingStatePrediction, (version.id, match.match_key)) is not None:
                continue
            event_model = forecasts.get(match.event_key)
            if event_model is None:
                event_model = forecasts[match.event_key] = copy.deepcopy(model)
            card = MatchCard(match.match_key, match.event_key, start, match.comp_level, source.features.get("event_start") or starts[match.event_key], tuple(sides["red"]), tuple(sides["blue"]))
            p = event_model.predict(card)
            raw = feature(event_model, card, p, here, total, min(first,card.event_start))
            table = {k:np.array([v]) for k,v in raw.items()}
            table["y"] = np.array([.5])
            red, blue, probability = layers.apply(table, fitted_layers)
            baseline = copy.deepcopy(source.features["row"])
            payload = {"raw": raw, "red_points": float(red[0]), "blue_points": float(blue[0]), "p": float(probability[0]),
                "production": baseline,
                "served": {"rm":float(source.final.get("red_points",baseline["rm"])),
                           "bm":float(source.final.get("blue_points",baseline["bm"])),"rv":baseline["rv"],"bv":baseline["bv"],
                           "p_live":float(source.final["red_win_prob"])}, "served_bundle_version":source.bundle_version,
                "red_teams": list(card.red), "blue_teams": list(card.blue), "data_sha256": data_sha,
                "calibration_sha256": digest(version.params), "source_capture_at": layers.utc(source.final_at).isoformat()}
            digest(payload)
            db.add(models.RatingStatePrediction(calibration_id=version.id, match_key=match.match_key, event_key=match.event_key,
                season=season, captured_at=now, expected_start=start, prediction=payload))
            added += 1
    db.flush()
    return {"status": "captured", "forecasts": added, "versions": len(versions), "skipped_inputs": skipped}


def score(db, version, *, through):
    # Evaluation reads frozen forecasts; it never replays later outcomes into them.
    through = layers.utc(through)
    matches = {m.match_key:m for m in db.query(models.Match).join(models.Event).filter(models.Event.year == version.season)}
    actual = {}
    for r in db.query(models.MatchAllianceResult).filter_by(season_year=version.season):
        if r.total_score is not None and r.total_score >= 0 and r.score_without_fouls is not None and not r.dq_team_keys:
            actual.setdefault(r.match_key,{})[r.alliance] = r
    candidate, baseline, served = {}, {}, {}
    for row in db.query(models.RatingStatePrediction).filter_by(calibration_id=version.id):
        match, sides = matches.get(row.match_key), actual.get(row.match_key,{})
        if match is None or match.actual_time is None or not {"red","blue"} <= sides.keys():
            continue
        if not max(layers.utc(row.captured_at).timestamp(), layers.utc(version.created_at).timestamp(), layers.utc(version.fitted_through).timestamp()) < match.actual_time < through.timestamp():
            continue
        saved = row.prediction
        if digest(version.params) != saved["calibration_sha256"]:
            raise ValueError("state calibration provenance mismatch")
        if any(sorted(str(t).lower() for t in sides[c].team_keys) != sorted(saved[f"{c}_teams"]) for c in ("red","blue")):
            continue
        y = .5 if sides["red"].total_score == sides["blue"].total_score else float(sides["red"].total_score>sides["blue"].total_score)
        if y == .5:
            continue
        ra, ba = float(sides["red"].score_without_fouls), float(sides["blue"].score_without_fouls)
        def losses(rm,bm,rv,bv,p):
            p = min(1-1e-6,max(1e-6,p))
            nll = .25*(math.log(2*math.pi*rv)+(ra-rm)**2/rv+math.log(2*math.pi*bv)+(ba-bm)**2/bv)
            return {"brier": (p-y)**2, "log_loss": -(y*math.log(p)+(1-y)*math.log(1-p)), "neg_score_loglik": nll,
                    "points_mae": (abs(ra-rm)+abs(ba-bm))/2, "event": row.event_key, "time": match.actual_time}
        candidate[row.match_key] = losses(saved["red_points"],saved["blue_points"],saved["raw"]["rv"],saved["raw"]["bv"],saved["p"])
        b = saved["production"]
        baseline[row.match_key] = losses(b["rm"],b["bm"],b["rv"],b["bv"],b["p_live"])
        live = saved["served"]
        served[row.match_key] = losses(live["rm"],live["bm"],live["rv"],live["bv"],live["p_live"])
    comparison = {metric: paired_cluster_bootstrap({k:(r["event"],r[metric]) for k,r in candidate.items()},
                                                  {k:(r["event"],r[metric]) for k,r in baseline.items()},seed=7)
                  for metric in ("brier","log_loss","neg_score_loglik","points_mae")}
    served_comparison = {metric: paired_cluster_bootstrap({k:(r["event"],r[metric]) for k,r in candidate.items()},
                                                         {k:(r["event"],r[metric]) for k,r in served.items()},seed=7)
                         for metric in ("brier","log_loss","neg_score_loglik","points_mae")}
    return {"kind": "prospective_first_forecast", "evaluated_through":through.isoformat(), "n": len(candidate), "comparison": comparison, "served_comparison":served_comparison,
            "serving_eligible": False, "release_blockers": ["total-state component adapter", "rating impact gate", "full rehearsal"]}
