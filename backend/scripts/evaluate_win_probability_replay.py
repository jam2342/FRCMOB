#!/usr/bin/env python3
# Replays held-out events match by match to compare the shipped win-probability formula
# with a fuel-rate formula, each given only what was known before the match.
#
#   shipped: _deterministic_red_win_prob(rating margin, synergy margin, projected margin),
#            with ratings recomputed from the results before each block of matches.
#            Event-wide inputs the rating also reads (TBA OPR stats, throughput and season
#            strength, synergy projections) can't be cut off by time, so the shipped
#            formula keeps some future information: the comparison is tilted its way.
#   fuel:    sigmoid(k * fuel margin). Each team's fuel rate (per active-hub minute) is
#            its official rate in earlier matches here, shrunk toward its previous
#            event's rate (weight n/(n+3)). k is fitted on the older events.
#
# recompute_event_ratings() commits, so this runs only against a scratch copy of the
# database (--database) and refuses the configured one.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np
from sqlalchemy import create_engine, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.api.routes_events import _deterministic_red_win_prob
from app.core.config import settings
from app.db import models
from app.services.ml import shadow
from app.services.ml.model_eval import binary_log_loss, brier_score
from app.services.ml.synergy import SYNERGY_MODEL_VERSION
from app.services.ratings import data_loader
from app.services.ratings import model as ratings_model

from app.services.events.fuel_win_probability import FUEL_WIN_SCALE, OFFICIAL_SOURCES, SHRINK_MATCHES


def _sigmoid(z: float) -> float:
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _fit_scale(margins: list[float], ys: list[float]) -> float:
    # One-parameter logistic (no intercept, so red and blue stay symmetric), by Newton.
    k = 0.0
    for _ in range(50):
        grad = hess = 0.0
        for x, y in zip(margins, ys):
            p = _sigmoid(k * x)
            grad += (p - y) * x
            hess += p * (1 - p) * x * x
        if hess <= 1e-12:
            break
        step = grad / hess
        k -= step
        if abs(step) < 1e-9:
            break
    return k


def _score(name: str, probs: list[float], ys: list[float]) -> dict:
    if not probs:
        return {"model": name, "n": 0}
    probs = [min(1 - 1e-6, max(1e-6, p)) for p in probs]
    correct = sum(1 for p, y in zip(probs, ys) if p != 0.5 and (p > 0.5) == (y > 0.5))
    ties = sum(1 for p in probs if p == 0.5)
    return {
        "model": name,
        "n": len(probs),
        "accuracy": round((correct + 0.5 * ties) / len(probs), 4),
        "brier": round(brier_score(ys, probs), 4),
        "log_loss": round(binary_log_loss(ys, probs), 4),
    }


def _asof_stats(state: dict, event_key: str, cutoff: int) -> dict:
    # OPR / DPR / CCWM from the official scores before the cutoff (what TBA shows
    # live), by ridge least squares: alliance score = sum of its teams' values.
    played = [m for m in state["event_matches"].get(event_key, []) if state["time"][m] < cutoff]
    teams = sorted({t for m in played for c in ("red", "blue") for t in state["alliances"][m][c]})
    if not played or not teams:
        return {}
    index = {t: i for i, t in enumerate(teams)}
    rows, own, opp = [], [], []
    for m in played:
        score = state["scores"][m]
        for color, other in (("red", "blue"), ("blue", "red")):
            row = np.zeros(len(teams))
            for t in state["alliances"][m][color]:
                row[index[t]] = 1.0
            rows.append(row)
            own.append(score[f"{color}_score"])
            opp.append(score[f"{other}_score"])
    a = np.array(rows)
    gram = a.T @ a + 0.5 * np.eye(len(teams))
    opr = np.linalg.solve(gram, a.T @ np.array(own))
    dpr = np.linalg.solve(gram, a.T @ np.array(opp))
    return {t: SimpleNamespace(opr=float(opr[i]), dpr=float(dpr[i]), ccwm=float(opr[i] - dpr[i])) for t, i in index.items()}


def _install_cutoff(state: dict, strip: set[str]) -> None:
    # Ratings see only findings from matches that started before state["cutoff"].
    original = data_loader.load_event_rating_data

    def load_with_cutoff(db, event_key, manual_context):
        data = original(db, event_key, manual_context)
        cutoff = state.get("cutoff")
        if cutoff is None:
            return data

        def before(match_key):
            t = data.match_time_by_key.get(match_key)
            return t is not None and t < cutoff

        data.finding_rows = [f for f in data.finding_rows if before(f.match_key)]
        data.findings_by_team = defaultdict(list)
        data.video_findings_count_by_team = defaultdict(int)
        for finding in data.finding_rows:
            data.findings_by_team[finding.team_key].append(finding)
        data.raw_finding_rows = [f for f in data.raw_finding_rows if before(f.match_key)]
        data.raw_findings_count_by_team = defaultdict(int)
        for finding in data.raw_finding_rows:
            data.raw_findings_count_by_team[finding.team_key] += 1
        data.throughputs_by_team = defaultdict(
            list,
            {team: [t for t in rows if before(t.match_key)] for team, rows in data.throughputs_by_team.items()},
        )
        filtered_events = defaultdict(lambda: defaultdict(list))
        for team, by_match in data.events_by_team_match.items():
            for match_key, rows in by_match.items():
                if before(match_key):
                    filtered_events[team][match_key] = rows
        data.events_by_team_match = filtered_events
        # Diagnostics: drop event-wide inputs that were computed after the event ended.
        if "stats" in strip:
            data.stats_by_team = {}
        if "asof_opr" in strip:
            data.stats_by_team = _asof_stats(state, event_key, cutoff)
        if "event_strength" in strip:
            data.event_strength_by_team = {}
        if "season_strength" in strip:
            data.season_strength_by_team = {}
        return data

    ratings_model.load_event_rating_data = load_with_cutoff
    # Statbotics is fetched live and only holds season-end values: leave it out.
    ratings_model._load_statbotics_epa_by_team = lambda team_rows: {}
    data_loader._load_statbotics_epa_by_team = lambda team_rows: {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, help="Scratch database name (a restored copy).")
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--block", type=int, default=6, help="Matches predicted per rating recompute.")
    parser.add_argument("--max-events", type=int, default=0)
    parser.add_argument("--calibration", action="store_true", help="Only fit the fuel scale per number of earlier matches here.")
    parser.add_argument("--strip", default="", help="Comma list: stats,asof_opr,event_strength,season_strength,synergy")
    args = parser.parse_args()

    url = make_url(settings.database_url)
    if not args.database or args.database == url.database:
        print("Refusing: --database must name a scratch copy, not the configured database.", file=sys.stderr)
        return 2
    engine = create_engine(url.set(database=args.database), pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False)
    state: dict = {}
    strip = {item.strip() for item in args.strip.split(",") if item.strip()}
    _install_cutoff(state, strip)

    with Session() as db:
        season_events = {k for (k,) in db.query(models.Event.event_key).filter(models.Event.year == args.season)}
        bounds = {
            key: (int(first or 0), int(last or 0))
            for key, first, last in db.query(models.Match.event_key, func.min(models.Match.time), func.max(models.Match.time))
            .filter(models.Match.event_key.in_(sorted(season_events)))
            .group_by(models.Match.event_key)
        }
        events = {k for k in season_events if k in bounds and bounds[k][0] > 0}
        holdout = shadow.holdout_event_keys(db, events)
        event_fuel = {k: v["strength_active_bps"] * 60.0 for k, v in shadow._team_target_map(db, events).items()}
        targets = shadow._match_target_map(db, events)
        rows = (
            db.query(models.Match.match_key, models.Match.event_key, models.Match.time, models.Match.comp_level, models.MatchTeam.alliance, models.MatchTeam.team_key)
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
        synergy = {
            (row.match_key.lower(), row.alliance_color): (float(row.alliance_synergy_points or 0.0), float(row.projected_throughput or 0.0))
            for row in db.query(models.MatchSynergyProjection).filter(
                models.MatchSynergyProjection.event_key.in_(sorted(holdout)),
                models.MatchSynergyProjection.model_version == SYNERGY_MODEL_VERSION,
            )
        }

    matches = defaultdict(lambda: {"red": [], "blue": []})
    meta = {}
    for match_key, event_key, t, level, alliance, team in rows:
        if alliance in ("red", "blue"):
            matches[match_key.lower()][alliance].append(team)
            meta[match_key.lower()] = (event_key, int(t), str(level or "").lower())

    def prior_event(team, event_key):
        start = bounds[event_key][0]
        best = None
        for other in events:
            if other == event_key or (other, team.lower()) not in event_fuel:
                continue
            end = bounds[other][1]
            if 0 < end < start and (best is None or end > bounds[best][1]):
                best = other
        return best

    prior_cache: dict = {}

    def prior_fuel(team, event_key):
        key = (team.lower(), event_key)
        if key not in prior_cache:
            other = prior_event(team, event_key)
            prior_cache[key] = event_fuel.get((other, team.lower())) if other else None
        return prior_cache[key]

    # Running official fuel rate per team at its event, as of each match.
    played = defaultdict(list)
    for match_key, (event_key, t, _level) in meta.items():
        for color in ("red", "blue"):
            for team in matches[match_key][color]:
                played[(event_key, team.lower())].append((t, match_key))
    live = {}
    for (event_key, team), items in played.items():
        total, count = 0.0, 0
        for _t, match_key in sorted(items):
            live[(match_key, team)] = (total / count if count else None, count)
            rate = per_match_fuel.get((match_key, team))
            if rate is not None:
                total, count = total + rate, count + 1

    def team_fuel(match_key, event_key, team):
        prior = prior_fuel(team, event_key)
        running, n = live.get((match_key, team.lower()), (None, 0))
        if running is None:
            return prior, n
        if prior is None:
            return running, n
        return (n * running + SHRINK_MATCHES * prior) / (n + SHRINK_MATCHES), n

    def fuel_features(match_key):
        event_key = meta[match_key][0]
        est = {c: [team_fuel(match_key, event_key, team) for team in matches[match_key][c]] for c in ("red", "blue")}
        known = [v for c in est for v, _n in est[c] if v is not None]
        all_known = len(known) == sum(len(est[c]) for c in est)
        # Same rule as the service: both alliances need a known team.
        if not all(any(v is not None for v, _n in est[c]) for c in est):
            return None, False, 0
        fill = sum(known) / len(known)
        sums = {c: sum(v if v is not None else fill for v, _n in est[c]) for c in est}
        min_n = min(n for c in est for _v, n in est[c])
        return sums["red"] - sums["blue"], all_known, min_n

    usable = [m for m in targets if m in meta and len(matches[m]["red"]) == 3 and len(matches[m]["blue"]) == 3]
    state["scores"] = targets
    state["alliances"] = matches
    state["time"] = {m: meta[m][1] for m in meta}
    state["event_matches"] = defaultdict(list)
    for m in usable:
        state["event_matches"][meta[m][0]].append(m)
    train = [m for m in usable if meta[m][0] not in holdout]
    k_any = _fit_scale(*zip(*[(f, targets[m]["red_win"]) for m in train if (f := fuel_features(m)[0]) is not None]))
    k_all = _fit_scale(*zip(*[(f, targets[m]["red_win"]) for m in train if (ff := fuel_features(m))[1] and (f := ff[0]) is not None]))

    if args.calibration:
        # How confident should the fuel formula be by how much of this event it has seen?
        def bucket(n):
            return "0" if n == 0 else "1-2" if n < 3 else "3+"

        def rows(keys):
            out = []
            for m in keys:
                margin, _all, min_n = fuel_features(m)
                if margin is not None:
                    out.append((bucket(min_n), margin, float(targets[m]["red_win"])))
            return out

        train_rows, test_rows = rows(train), rows([m for m in usable if meta[m][0] in holdout])
        scales = {b: _fit_scale([x for bb, x, _y in train_rows if bb == b], [y for bb, _x, y in train_rows if bb == b]) for b in ("0", "1-2", "3+")}
        report = {"single_scale": round(k_any, 5), "scale_by_matches_seen": {b: round(k, 5) for b, k in scales.items()}, "holdout": {}}
        for b in ("0", "1-2", "3+", "all"):
            subset = [r for r in test_rows if b == "all" or r[0] == b]
            ys = [y for _b, _x, y in subset]
            report["holdout"][b] = {
                "n": len(subset),
                "single": _score("single", [_sigmoid(k_any * x) for _b, x, _y in subset], ys),
                "bucketed": _score("bucketed", [_sigmoid(scales[bb] * x) for bb, x, _y in subset], ys),
            }
        print(json.dumps(report, indent=2))
        return 0

    # ── Replay the held-out events ──
    holdout_list = sorted(holdout, key=lambda e: bounds[e][1])
    if args.max_events:
        holdout_list = holdout_list[: args.max_events]
    records = []
    started = time.perf_counter()
    recomputes = 0
    with Session() as db:
        for event_key in holdout_list:
            ordered = sorted((m for m in usable if meta[m][0] == event_key), key=lambda m: meta[m][1])
            for i in range(0, len(ordered), args.block):
                block = ordered[i : i + args.block]
                state["cutoff"] = meta[block[0]][1]
                ratings_model.recompute_event_ratings(db, event_key)
                recomputes += 1
                rating = {
                    r.team_key.lower(): float(r.rating_0_100 or 50.0)
                    for r in db.query(models.EventTeamRating).filter(models.EventTeamRating.event_key == event_key)
                }
                for m in block:
                    side = {c: [rating.get(t.lower(), 50.0) for t in matches[m][c]] for c in ("red", "blue")}
                    rating_margin = sum(side["red"]) / 3 - sum(side["blue"]) / 3
                    red_syn, red_proj = (0.0, 0.0) if "synergy" in strip else synergy.get((m, "red"), (0.0, 0.0))
                    blue_syn, blue_proj = (0.0, 0.0) if "synergy" in strip else synergy.get((m, "blue"), (0.0, 0.0))
                    shipped = _deterministic_red_win_prob(rating_margin, red_syn - blue_syn, red_proj - blue_proj)
                    margin, all_known, min_n = fuel_features(m)
                    records.append({
                        "first_round": i == 0,
                        "y": float(targets[m]["red_win"]),
                        "level": meta[m][2],
                        "min_n": min_n,
                        "shipped": shipped,
                        "fuel_any": _sigmoid(k_any * margin) if margin is not None else shipped,
                        "fuel_all": _sigmoid(k_all * margin) if all_known else shipped,
                        "has_fuel": margin is not None,
                        "all_known": all_known,
                    })
            db.rollback()
            print(f"{event_key}: {len(ordered)} matches, {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)

    def report(subset):
        ys = [r["y"] for r in subset]
        return [
            _score("shipped formula (as-of ratings + synergy)", [r["shipped"] for r in subset], ys),
            _score("fuel formula, shipped formula when no fuel data", [r["fuel_any"] for r in subset], ys),
            _score("fuel formula only when all six known, else shipped", [r["fuel_all"] for r in subset], ys),
        ]

    print(json.dumps({
        "season": args.season,
        "holdout_events": len(holdout_list),
        "rating_recomputes": recomputes,
        "seconds": round(time.perf_counter() - started),
        "fitted_scale": {"fuel_any": round(k_any, 5), "fuel_all": round(k_all, 5), "shipped": FUEL_WIN_SCALE, "train_matches": len(train)},
        "all": {"n": len(records), "results": report(records)},
        "quals": {"results": report([r for r in records if r["level"] == "qm"])},
        "playoffs": {"results": report([r for r in records if r["level"] != "qm"])},
        "strip": sorted(strip),
        "first_block_no_results_yet": {"results": report([r for r in records if r["first_round"]])},
        "early_event (some team <3 earlier matches)": {"results": report([r for r in records if r["min_n"] < 3])},
        "with_fuel_data": {"n": sum(r["has_fuel"] for r in records), "results": report([r for r in records if r["has_fuel"]])},
        "all_six_known": {"n": sum(r["all_known"] for r in records), "results": report([r for r in records if r["all_known"]])},
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
