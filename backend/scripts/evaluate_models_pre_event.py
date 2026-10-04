#!/usr/bin/env python3
# Would the shadow models beat what ships today, using only what is known before
# an event starts?
#
# Training features come from end-of-event ratings, which already contain the
# matches being predicted, so training metrics flatter the models. This script
# re-scores the held-out (newest) events with each team's rating from its most
# recent *earlier* event instead, and compares on identical inputs:
#   match outcome: ML model vs the app's deterministic formula vs "higher rating wins"
#   team strength: ML model vs "same fuel rate as the team's previous event"
# Synergy margins are zeroed for every predictor in the pre-event view (they are
# computed per event). Read-only.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func

from app.api.routes_events import _deterministic_red_win_prob
from app.db import models
from app.db.session import SessionLocal
from app.services.ml import shadow
from app.services.ml.model_eval import binary_log_loss, brier_score, mean_absolute_error, spearman_rank_correlation


def _r(value: float | None) -> float | None:
    return round(value, 4) if isinstance(value, float) else value


def _classification(name: str, probs: list[float], outcomes: list[float]) -> dict:
    if not probs:
        return {"model": name, "n": 0}
    correct = sum(1 for p, y in zip(probs, outcomes) if (p >= 0.5) == (y >= 0.5))
    return {
        "model": name,
        "n": len(probs),
        "accuracy": round(correct / len(probs), 4),
        "brier": _r(brier_score(outcomes, probs)),
        "log_loss": _r(binary_log_loss(outcomes, probs)),
    }


def _regression(name: str, preds: list[float], targets: list[float]) -> dict:
    if not preds:
        return {"model": name, "n": 0}
    return {
        "model": name,
        "n": len(preds),
        "mae": _r(mean_absolute_error(targets, preds)),
        "spearman": _r(spearman_rank_correlation(targets, preds)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--match-outcome-model-version", help="Explicit inactive candidate to evaluate; otherwise use the active release.")
    parser.add_argument("--team-strength-model-version", help="Explicit inactive candidate to evaluate; otherwise use the active release.")
    args = parser.parse_args()

    with SessionLocal() as db:
        season_events = {
            key
            for (key,) in db.query(models.Event.event_key).filter(models.Event.year == args.season)
        }
        bounds = {
            key: (int(first or 0), int(last or 0))
            for key, first, last in db.query(
                models.Match.event_key, func.min(models.Match.time), func.max(models.Match.time)
            )
            .filter(models.Match.event_key.in_(sorted(season_events)))
            .group_by(models.Match.event_key)
        }
        events = {key for key in season_events if key in bounds}
        holdout = shadow.holdout_event_keys(db, events)

        ratings = defaultdict(dict)  # team -> event -> rating row
        for row in db.query(models.EventTeamRating).filter(models.EventTeamRating.event_key.in_(sorted(events))):
            ratings[row.team_key][row.event_key] = row

        def prior_rating(team_key: str, event_key: str) -> models.EventTeamRating | None:
            start = bounds[event_key][0]
            earlier = [
                (bounds[other][1], row)
                for other, row in ratings.get(team_key, {}).items()
                if other != event_key and other in bounds and 0 < bounds[other][1] < start
            ]
            return max(earlier, key=lambda item: item[0])[1] if earlier else None

        # ── match outcome ──
        match_targets = shadow._match_target_map(db, holdout)
        teams_by_match = defaultdict(lambda: {"red": [], "blue": []})
        match_event = {}
        for match_key, event_key, alliance, team_key in (
            db.query(models.Match.match_key, models.Match.event_key, models.MatchTeam.alliance, models.MatchTeam.team_key)
            .join(models.MatchTeam, models.MatchTeam.match_key == models.Match.match_key)
            .filter(models.Match.event_key.in_(sorted(holdout)))
        ):
            if alliance in ("red", "blue"):
                teams_by_match[match_key][alliance].append(team_key)
                match_event[match_key] = event_key

        mo_record = shadow._load_model_registry_row(
            db, model_key=shadow.MATCH_OUTCOME_MODEL_KEY, model_version=args.match_outcome_model_version
        )
        mo_runtime = shadow._load_model_runtime(mo_record) if mo_record else None

        views = {"in_event": [], "pre_event": []}
        outcomes = {"in_event": [], "pre_event": []}
        skipped_no_prior = 0
        for match_key, target in match_targets.items():
            sides = teams_by_match.get(match_key)
            event_key = match_event.get(match_key)
            if not sides or not event_key or not sides["red"] or not sides["blue"]:
                continue
            y = float(target["red_win"])
            for view in ("in_event", "pre_event"):
                side_rows = {}
                for color in ("red", "blue"):
                    rows = [
                        (ratings.get(team, {}).get(event_key) if view == "in_event" else prior_rating(team, event_key))
                        for team in sides[color]
                    ]
                    side_rows[color] = [row for row in rows if row is not None]
                if len(side_rows["red"]) < len(sides["red"]) or len(side_rows["blue"]) < len(sides["blue"]):
                    if view == "pre_event":
                        skipped_no_prior += 1
                    continue

                def mean(values):
                    return sum(values) / len(values)

                red_mean = mean([float(r.rating_0_100) for r in side_rows["red"]])
                blue_mean = mean([float(r.rating_0_100) for r in side_rows["blue"]])
                red_conf = mean([float(r.confidence_0_1 or 0.0) for r in side_rows["red"]])
                blue_conf = mean([float(r.confidence_0_1 or 0.0) for r in side_rows["blue"]])
                views[view].append({
                    "red_rating_mean": red_mean,
                    "blue_rating_mean": blue_mean,
                    "rating_margin": red_mean - blue_mean,
                    "red_confidence_mean": red_conf,
                    "blue_confidence_mean": blue_conf,
                    "confidence_margin": red_conf - blue_conf,
                })
                outcomes[view].append(y)

        match_outcome = {}
        for view, features in views.items():
            ys = outcomes[view]
            deterministic = [_deterministic_red_win_prob(f["rating_margin"], 0.0, 0.0) for f in features]
            higher = [1.0 if f["rating_margin"] > 0 else (0.0 if f["rating_margin"] < 0 else 0.5) for f in features]
            results = [
                _classification("deterministic_formula", deterministic, ys),
                _classification("higher_rating_wins", higher, ys),
            ]
            if mo_runtime:
                ml = shadow._predict_batch(
                    runtime=mo_runtime, rows=[shadow._FeatureVectorRow(feature_vector=f) for f in features]
                )
                results.insert(0, _classification("ml_match_outcome", [min(1 - 1e-6, max(1e-6, p)) for p in ml], ys))
            match_outcome[view] = results

        # ── team strength ──
        team_targets = shadow._team_target_map(db, events)
        ts_record = shadow._load_model_registry_row(
            db, model_key=shadow.TEAM_STRENGTH_MODEL_KEY, model_version=args.team_strength_model_version
        )
        ts_runtime = shadow._load_model_runtime(ts_record) if ts_record else None
        ml_rows, ml_targets, naive_preds, naive_targets = [], [], [], []
        for event_key in holdout:
            for team_key, by_event in ratings.items():
                if event_key not in by_event:
                    continue
                target = team_targets.get((event_key, team_key.lower()))
                prior = prior_rating(team_key, event_key)
                if target is None or prior is None:
                    continue
                prior_target = team_targets.get((prior.event_key, team_key.lower()))
                if prior_target is None:
                    continue
                y = float(target["strength_active_bps"])
                features = shadow._team_strength_feature_vector_from_row_payload(
                    shadow.build_team_strength_shadow_input_from_rating_row(prior)
                )
                ml_rows.append(shadow._FeatureVectorRow(feature_vector=features))
                ml_targets.append(y)
                naive_preds.append(float(prior_target["strength_active_bps"]))
                naive_targets.append(y)
        team_strength = [_regression("previous_event_fuel_rate", naive_preds, naive_targets)]
        if ts_runtime and ml_rows:
            team_strength.insert(0, _regression("ml_team_strength", shadow._predict_batch(runtime=ts_runtime, rows=ml_rows), ml_targets))

    print(json.dumps({
        "season": args.season,
        "events": len(events),
        "holdout_events": len(holdout),
        "match_outcome_model": getattr(mo_record, "model_version", None),
        "team_strength_model": getattr(ts_record, "model_version", None),
        "match_outcome": match_outcome,
        "pre_event_matches_skipped_no_prior_rating": skipped_no_prior,
        "team_strength_pre_event": team_strength,
    }, indent=2, default=lambda value: None if isinstance(value, float) and math.isnan(value) else str(value)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
