#!/usr/bin/env python3
# Can a model trained only on what is known before a match beat the formula that ships?
#
# The shadow models learned from end-of-event ratings, which already contain the
# matches they predict (training accuracy 82%, honest pre-event accuracy 49.8% vs the
# formula's 59.2%; see evaluate_models_pre_event.py). This script rebuilds the training
# rows from pre-match information only and re-fits:
#   prior event: each team's rating, confidence and official fuel rate from its most
#                recent earlier event
#   this event:  the team's official fuel rate in its earlier matches here, and how
#                many there were (0 for its first match)
# It trains on the older events and scores on the same newest-20% holdout as the
# evaluation script, next to the deterministic formula and simple baselines.
# Read-only: nothing is written to the database or the model registry.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import torch
from sqlalchemy import func

from app.api.routes_events import _deterministic_red_win_prob
from app.db import models
from app.db.session import SessionLocal
from app.services.ml import shadow
from app.services.ml.model_eval import (
    binary_log_loss,
    brier_score,
    expected_calibration_error,
    mean_absolute_error,
    spearman_rank_correlation,
)

OFFICIAL_SOURCES = ("tba_score_breakdown", "tba_score_breakdown_backfill")

MATCH_FEATURES = [
    "rating_margin",
    "red_rating_mean",
    "blue_rating_mean",
    "red_rating_min",
    "blue_rating_min",
    "confidence_margin",
    "prior_fuel_margin",
    "red_prior_fuel_sum",
    "blue_prior_fuel_sum",
    "live_fuel_margin",
    "red_live_fuel_sum",
    "blue_live_fuel_sum",
    "live_matches_min",
    "blended_fuel_margin",
]

PRIOR_ONLY_FEATURES = [f for f in MATCH_FEATURES if "live" not in f and "blended" not in f]


def _r(value):
    return round(value, 4) if isinstance(value, float) and not math.isnan(value) else value


def _classification(name, probs, ys):
    if not probs:
        return {"model": name, "n": 0}
    probs = [min(1 - 1e-6, max(1e-6, float(p))) for p in probs]
    correct = sum(1 for p, y in zip(probs, ys) if (p >= 0.5) == (y >= 0.5))
    return {
        "model": name,
        "n": len(probs),
        "accuracy": round(correct / len(probs), 4),
        "brier": _r(brier_score(ys, probs)),
        "log_loss": _r(binary_log_loss(ys, probs)),
        "ece": _r(expected_calibration_error(ys, probs)),
    }


def _regression(name, preds, ys):
    if not preds:
        return {"model": name, "n": 0}
    return {
        "model": name,
        "n": len(preds),
        "mae": _r(mean_absolute_error(ys, preds)),
        "spearman": _r(spearman_rank_correlation(ys, preds)),
    }


def _standardize(train_x, *others):
    mean = train_x.mean(dim=0)
    std = train_x.std(dim=0)
    std = torch.where(std < 1e-6, torch.ones_like(std), std)
    return mean, std, [(x - mean) / std for x in (train_x, *others)]


def _time_split(rows, ratio=0.8):
    ordered = sorted(rows, key=lambda r: r["t"])
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * ratio)))
    return ordered[:cut], ordered[cut:]


def _dataset(train_rows, features, target):
    fit, val = _time_split(train_rows)
    fx = torch.tensor([[r[f] for f in features] for r in fit], dtype=torch.float32)
    vx = torch.tensor([[r[f] for f in features] for r in val], dtype=torch.float32)
    mean, std, (fx, vx) = _standardize(fx, vx)
    data = shadow._PreparedDataset(
        feature_order=features,
        train_x=fx,
        train_y=torch.tensor([r[target] for r in fit], dtype=torch.float32),
        val_x=vx,
        val_y=torch.tensor([r[target] for r in val], dtype=torch.float32),
        mean=mean,
        std=std,
        train_count=len(fit),
        val_count=len(val),
    )
    return data


def _apply(model, data, rows, features, *, sigmoid):
    x = torch.tensor([[r[f] for f in features] for r in rows], dtype=torch.float32)
    x = (x - data.mean) / data.std
    model.eval()
    with torch.no_grad():
        out = model(x).squeeze(-1)
        return (torch.sigmoid(out) if sigmoid else out).tolist()


def _fit_linear(train_rows, features, target, *, logistic, epochs=600):
    # Plain (logistic) regression with early stopping on a time-ordered validation slice:
    # the small, hard-to-overfit counterpart to the shadow network.
    data = _dataset(train_rows, features, target)
    model = torch.nn.Linear(len(features), 1)
    opt = torch.optim.Adam(model.parameters(), lr=0.03)
    loss_fn = torch.nn.BCEWithLogitsLoss() if logistic else torch.nn.SmoothL1Loss(beta=0.2)
    best, best_state = float("inf"), None
    for _ in range(epochs):
        model.train()
        opt.zero_grad()
        loss = loss_fn(model(data.train_x).squeeze(-1), data.train_y)
        loss.backward()
        opt.step()
        with torch.no_grad():
            val = float(loss_fn(model(data.val_x).squeeze(-1), data.val_y))
        if val < best:
            best, best_state = val, {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    return model, data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    random.seed(args.seed)
    torch.manual_seed(args.seed)

    with SessionLocal() as db:
        season_events = {k for (k,) in db.query(models.Event.event_key).filter(models.Event.year == args.season)}
        bounds = {
            key: (int(first or 0), int(last or 0))
            for key, first, last in db.query(models.Match.event_key, func.min(models.Match.time), func.max(models.Match.time))
            .filter(models.Match.event_key.in_(sorted(season_events)))
            .group_by(models.Match.event_key)
        }
        events = {k for k in season_events if k in bounds and bounds[k][0] > 0}
        holdout = shadow.holdout_event_keys(db, events)

        ratings = defaultdict(dict)  # team -> event -> rating row
        for row in db.query(models.EventTeamRating).filter(models.EventTeamRating.event_key.in_(sorted(events))):
            ratings[row.team_key.lower()][row.event_key] = row
        event_fuel = {k: v["strength_active_bps"] * 60.0 for k, v in shadow._team_target_map(db, events).items()}
        match_targets = shadow._match_target_map(db, events)

        match_rows = (
            db.query(models.Match.match_key, models.Match.event_key, models.Match.time, models.MatchTeam.alliance, models.MatchTeam.team_key)
            .join(models.MatchTeam, models.MatchTeam.match_key == models.Match.match_key)
            .filter(models.Match.event_key.in_(sorted(events)), models.Match.time.isnot(None))
            .all()
        )
        per_match_fuel = {
            (str(m).lower(), str(t).lower()): float(rate)
            for m, t, rate in db.query(models.TeamMatchFinding.match_key, models.TeamMatchFinding.team_key, models.TeamMatchFinding.fuel_scoring_rate)
            .filter(
                models.TeamMatchFinding.event_key.in_(sorted(events)),
                models.TeamMatchFinding.source.in_(OFFICIAL_SOURCES),
                models.TeamMatchFinding.fuel_scoring_rate.isnot(None),
            )
        }

    def prior(team, event_key):
        start = bounds[event_key][0]
        earlier = [
            (bounds[other][1], other, row)
            for other, row in ratings.get(team, {}).items()
            if other != event_key and other in bounds and 0 < bounds[other][1] < start
        ]
        return max(earlier, key=lambda item: item[0])[1:] if earlier else (None, None)

    matches = defaultdict(lambda: {"red": [], "blue": []})
    meta = {}
    for match_key, event_key, t, alliance, team in match_rows:
        if alliance in ("red", "blue"):
            matches[match_key.lower()][alliance].append(team.lower())
            meta[match_key.lower()] = (event_key, int(t))

    # Each team's official fuel rate in earlier matches at the same event, as of each match.
    by_event_team = defaultdict(list)
    for match_key, (event_key, t) in meta.items():
        for color in ("red", "blue"):
            for team in matches[match_key][color]:
                by_event_team[(event_key, team)].append((t, match_key))
    live = {}
    for (event_key, team), played in by_event_team.items():
        total, count = 0.0, 0
        for t, match_key in sorted(played):
            live[(match_key, team)] = (total / count if count else None, count)
            rate = per_match_fuel.get((match_key, team))
            if rate is not None:
                total, count = total + rate, count + 1

    def team_state(match_key, event_key, team):
        prior_event, row = prior(team, event_key)
        if row is None:
            return None
        prior_fuel = event_fuel.get((prior_event, team))
        live_fuel, live_n = live.get((match_key, team), (None, 0))
        prior_fuel = prior_fuel if prior_fuel is not None else 0.0
        # Shrink this event's running rate toward the prior one until a few matches are in.
        weight = live_n / (live_n + 3.0)
        blended = prior_fuel if live_fuel is None else weight * live_fuel + (1 - weight) * prior_fuel
        return {
            "rating": float(row.rating_0_100),
            "confidence": float(row.confidence_0_1 or 0.0),
            "prior_fuel": prior_fuel,
            "live_fuel": live_fuel if live_fuel is not None else prior_fuel,
            "live_n": live_n,
            "blended": blended,
            "row": row,
        }

    mo_train, mo_test, skipped = [], [], 0
    for match_key, target in match_targets.items():
        if match_key not in meta:
            continue
        event_key, t = meta[match_key]
        sides = matches[match_key]
        if len(sides["red"]) != 3 or len(sides["blue"]) != 3:
            continue
        states = {c: [team_state(match_key, event_key, team) for team in sides[c]] for c in ("red", "blue")}
        if any(s is None for c in states for s in states[c]):
            skipped += 1
            continue
        red, blue = states["red"], states["blue"]

        def mean(xs):
            return sum(xs) / len(xs)

        row = {
            "t": t,
            "y": float(target["red_win"]),
            "red_rating_mean": mean([s["rating"] for s in red]),
            "blue_rating_mean": mean([s["rating"] for s in blue]),
            "red_rating_min": min(s["rating"] for s in red),
            "blue_rating_min": min(s["rating"] for s in blue),
            "red_prior_fuel_sum": sum(s["prior_fuel"] for s in red),
            "blue_prior_fuel_sum": sum(s["prior_fuel"] for s in blue),
            "red_live_fuel_sum": sum(s["live_fuel"] for s in red),
            "blue_live_fuel_sum": sum(s["live_fuel"] for s in blue),
            "live_matches_min": float(min(s["live_n"] for s in red + blue)),
            "confidence_margin": mean([s["confidence"] for s in red]) - mean([s["confidence"] for s in blue]),
            "blended_fuel_margin": sum(s["blended"] for s in red) - sum(s["blended"] for s in blue),
        }
        row["rating_margin"] = row["red_rating_mean"] - row["blue_rating_mean"]
        end = {c: [ratings.get(team, {}).get(event_key) for team in sides[c]] for c in ("red", "blue")}
        row["end_rating_margin"] = (
            None if any(x is None for c in end for x in end[c])
            else mean([float(x.rating_0_100) for x in end["red"]]) - mean([float(x.rating_0_100) for x in end["blue"]])
        )
        row["prior_fuel_margin"] = row["red_prior_fuel_sum"] - row["blue_prior_fuel_sum"]
        row["live_fuel_margin"] = row["red_live_fuel_sum"] - row["blue_live_fuel_sum"]
        (mo_test if event_key in holdout else mo_train).append(row)

    ys = [r["y"] for r in mo_test]
    preds = [
        ("deterministic_formula (shipped, prior-event rating)", [_deterministic_red_win_prob(r["rating_margin"], 0.0, 0.0) for r in mo_test]),
        ("higher_prior_fuel_wins", [1.0 if r["prior_fuel_margin"] > 0 else 0.0 if r["prior_fuel_margin"] < 0 else 0.5 for r in mo_test]),
        ("higher_blended_fuel_wins", [1.0 if r["blended_fuel_margin"] > 0 else 0.0 if r["blended_fuel_margin"] < 0 else 0.5 for r in mo_test]),
    ]
    for name, feats in (
        ("logistic: rating margin (recalibrated formula)", ["rating_margin"]),
        ("logistic: blended fuel margin", ["blended_fuel_margin"]),
        ("logistic: prior-event features only", PRIOR_ONLY_FEATURES),
        ("logistic: all pre-match features", MATCH_FEATURES),
    ):
        model, data = _fit_linear(mo_train, feats, "y", logistic=True)
        preds.append((name, _apply(model, data, mo_test, feats, sigmoid=True)))
    data = _dataset(mo_train, MATCH_FEATURES, "y")
    net, net_metrics = shadow._train_classification_model(data)
    preds.append(("shadow network: all pre-match features", _apply(net, data, mo_test, MATCH_FEATURES, sigmoid=True)))
    results = [_classification(name, p, ys) for name, p in preds]
    # Upper bound for the formula mid-event: end-of-event ratings already contain this match.
    ended = [i for i, r in enumerate(mo_test) if r["end_rating_margin"] is not None]
    results.append(_classification(
        "deterministic_formula with end-of-event ratings (leaky upper bound)",
        [_deterministic_red_win_prob(mo_test[i]["end_rating_margin"], 0.0, 0.0) for i in ended],
        [ys[i] for i in ended],
    ))
    # Early-event slice: some team in the match had fewer than 3 earlier matches here.
    early = [i for i, r in enumerate(mo_test) if r["live_matches_min"] < 3]
    early_results = [_classification(name, [p[i] for i in early], [ys[i] for i in early]) for name, p in preds]

    # ── team strength: predict a team's fuel rate at an event from what was known before it ──
    ts_train, ts_test = [], []
    for (event_key, team), y in event_fuel.items():
        if event_key not in bounds:
            continue
        prior_event, row = prior(team, event_key)
        if row is None or (prior_event, team) not in event_fuel:
            continue
        features = shadow._team_strength_feature_vector_from_row_payload(shadow.build_team_strength_shadow_input_from_rating_row(row))
        features.update({"prior_fuel_bpm": event_fuel[(prior_event, team)], "live_fuel_bpm": 0.0, "live_matches": 0.0})
        item = {"t": bounds[event_key][0], "y": y, **features}
        (ts_test if event_key in holdout else ts_train).append(item)
    ts_features = shadow.TEAM_STRENGTH_FEATURE_ORDER + ["prior_fuel_bpm"]
    ts_y = [r["y"] for r in ts_test]
    team_results = [_regression("previous_event_fuel_rate", [r["prior_fuel_bpm"] for r in ts_test], ts_y)]
    model, data = _fit_linear(ts_train, ts_features, "y", logistic=False)
    team_results.append(_regression("linear: prior-event features", _apply(model, data, ts_test, ts_features, sigmoid=False), ts_y))
    data = _dataset(ts_train, ts_features, "y")
    net, _ = shadow._train_regression_model(data)
    team_results.append(_regression("shadow network: prior-event features", _apply(net, data, ts_test, ts_features, sigmoid=False), ts_y))

    print(json.dumps({
        "season": args.season,
        "events": len(events),
        "holdout_events": len(holdout),
        "match_outcome": {
            "train_matches": len(mo_train),
            "holdout_matches": len(mo_test),
            "skipped_no_prior_event": skipped,
            "holdout_red_win_rate": _r(sum(ys) / len(ys)) if ys else None,
            "holdout": results,
            "network_validation": {k: _r(v) for k, v in net_metrics.items()},
            "holdout_early_event": early_results,
        },
        "team_strength": {
            "train_rows": len(ts_train),
            "holdout_rows": len(ts_test),
            "holdout": team_results,
        },
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
