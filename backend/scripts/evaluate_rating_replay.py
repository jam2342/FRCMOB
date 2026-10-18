#!/usr/bin/env python3
# Replay a season and compare team models on what happened next (ratings/replay.py).
# Read-only: the session is set READ ONLY.
#
#   PYTHONPATH=. python scripts/evaluate_rating_replay.py --season 2026 [--models v15,fuel_only] [--grid]
#
# Prints one JSON report per model plus a comparison table. --grid searches v15's
# shrink settings on the training events only (fit on the older 70%, scored on the
# rest), then replays the chosen setting on the held-out events.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
import time
from dataclasses import replace
from statistics import median
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.ratings.constants import STRENGTH_PARAMS, season_baseline
from app.services.ratings.replay import FuelOnly, Season, StrengthV15, load_season, run_replay, split_holdout, typical_robot
from app.services.ratings.team_model import ComponentParams, SeasonTeamModel, TeamModelParams, component_priors


# Early-season priors as production uses them (team_model_service.season_prior_multipliers).
PRIOR_MULTIPLIERS: tuple[dict, dict] = ({}, {})


def _shipped_team_params(year: int) -> TeamModelParams:
    try:
        return season_baseline(year)["team_model_params"]
    except ValueError:
        return TeamModelParams()


def _training_events(season: Season) -> set[str]:
    return {r.card.event_key for r in season.results} - season.holdout_events


MODELS = {
    "v15": lambda season, params=STRENGTH_PARAMS: StrengthV15(season.typical, params),
    "fuel_only": lambda season, params=None: FuelOnly(season.typical),
    "team_model": lambda season, params=None: SeasonTeamModel(
        component_priors(season.results, _training_events(season)), params or _shipped_team_params(season.year),
        team_multipliers=PRIOR_MULTIPLIERS[0], team_var_multipliers=PRIOR_MULTIPLIERS[1],
    ),
}


def _grid_team_model(season: Season) -> tuple[TeamModelParams, list[dict]]:
    # Coordinate search, one setting at a time, on the training events only (fit on the
    # older 70%, scored on the rest). Scored by Brier, alliance-points error as the tie-break.
    training = [r for r in season.results if r.card.event_key not in season.holdout_events]
    inner = Season(season.year, training, split_holdout(training, 0.3), season.typical)
    priors = component_priors(training, _training_events(inner))
    best = TeamModelParams()
    rows: list[dict] = []

    def score(params: TeamModelParams) -> tuple[float, float]:
        report = run_replay(inner, SeasonTeamModel(priors, params))
        row = {"params": {k: getattr(params, k).__dict__ for k in ("teleop", "auto", "tower")}, **report["matches"]["all"],
               "alliance_error": report["alliance_points_error"]["mean_abs"]}
        rows.append(row)
        print(json.dumps(row), file=sys.stderr, flush=True)
        return report["matches"]["all"]["brier"], report["alliance_points_error"]["mean_abs"] or 0.0

    best_score = score(best)
    grid = {
        "teleop": {"noise_var": (1600.0, 2500.0, 4000.0, 6400.0), "prior_var": (900.0, 1600.0, 2500.0, 4000.0),
                   "event_var": (50.0, 150.0, 300.0, 600.0, 1200.0), "match_var": (0.0, 5.0, 10.0, 25.0, 50.0)},
        "auto": {"noise_var": (200.0, 400.0, 800.0), "prior_var": (100.0, 225.0, 400.0), "event_var": (20.0, 60.0, 150.0)},
        "tower": {"noise_var": (30.0, 60.0, 120.0), "prior_var": (20.0, 40.0, 80.0), "event_var": (5.0, 10.0, 25.0)},
    }
    for _round in range(2):
        for component, settings in grid.items():
            for setting, values in settings.items():
                for value in values:
                    current = getattr(best, component)
                    if getattr(current, setting) == value:
                        continue
                    candidate = replace(best, **{component: replace(current, **{setting: value})})
                    result = score(candidate)
                    if result < best_score:
                        best, best_score = candidate, result
    rows.sort(key=lambda row: (row["brier"], row["alliance_error"]))
    return best, rows


def _grid(season: Season) -> tuple[object, list[dict]]:
    training = [r for r in season.results if r.card.event_key not in season.holdout_events]
    inner = Season(season.year, training, split_holdout(training, 0.3), season.typical)
    candidates = [STRENGTH_PARAMS]
    for event_shrink in (1.5, 3.0, 5.0, 8.0):
        for prior_window in (6, 12, 24):
            for recent_weight in (1.0, 2.0):
                candidates.append(replace(STRENGTH_PARAMS, event_shrink=event_shrink, prior_window=prior_window, recent_weight=recent_weight))
    for prior_shrink in (0.5, 1.0, 2.0, 8.0):
        candidates.append(replace(STRENGTH_PARAMS, prior_shrink=prior_shrink))
    rows = []
    for params in dict.fromkeys(candidates):
        report = run_replay(inner, StrengthV15(season.typical, params))
        rows.append({"params": params.__dict__, **report["matches"]["all"]})
        print(json.dumps(rows[-1]), file=sys.stderr, flush=True)
    rows.sort(key=lambda row: row["brier"])
    return type(STRENGTH_PARAMS)(**rows[0]["params"]), rows


def _team_model_baseline(season: Season, params: TeamModelParams) -> dict:
    # Priors = season means per robot over every event; the median robot = the median of
    # each team's points at the end of each event it played 5+ matches at.
    priors = component_priors(season.results)
    model = SeasonTeamModel(priors, params)
    played: dict[tuple[str, str], int] = {}
    for result in season.results:
        model.predict(result.card)
        model.observe(result)
        for team in result.card.red + result.card.blue:
            played[(team, result.card.event_key)] = played.get((team, result.card.event_key), 0) + 1
    ends = {name: [] for name in ("teleop", "auto", "tower", "total")}
    for (team, event_key), count in played.items():
        if count < 5:
            continue
        belief = model.belief(team, event_key)
        for name in ("teleop", "auto", "tower"):
            ends[name].append(belief[name].mean)
        ends["total"].append(sum(b.mean for b in belief.values()))
    ordered = sorted(ends["total"])
    return {
        "priors": {k: round(v, 3) for k, v in priors.items()},
        "median_robot_points": round(median(ends["total"]), 2),
        "typical": {name: round(median(ends[name]), 2) for name in ("teleop", "auto", "tower")},
        "total_percentiles": {p: round(ordered[int(p / 100 * (len(ordered) - 1))], 1) for p in (10, 25, 50, 75, 90, 99, 100)},
        "team_events": len(ordered),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--models", default="v15,team_model,fuel_only")
    parser.add_argument("--grid", action="store_true", help="Search v15's settings.")
    parser.add_argument("--grid-team", action="store_true", help="Search the team model's settings.")
    parser.add_argument("--upset-floor", action="store_true", help="Fit an upset floor on the win probability.")
    parser.add_argument("--playoff-scale", action="store_true", help="Give playoffs their own win-probability scale.")
    args = parser.parse_args()

    started = time.perf_counter()
    db = SessionLocal()
    db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
    db.commit()
    season = load_season(db, args.season)
    try:
        from app.services.ratings.team_model_service import season_prior_multipliers

        PRIOR_MULTIPLIERS[0].update(season_prior_multipliers(db, args.season, season_baseline(args.season))[0])
        PRIOR_MULTIPLIERS[1].update(season_prior_multipliers(db, args.season, season_baseline(args.season))[1])
    except ValueError:
        pass
    db.close()

    reports = []
    grid_rows: list[dict] = []
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        if name == "v15" and args.grid:
            params, grid_rows = _grid(season)
            reports.append(run_replay(season, MODELS[name](season, params)) | {"params": params.__dict__})
        elif name == "team_model" and args.grid_team:
            params, team_rows = _grid_team_model(season)
            grid_rows = grid_rows or team_rows
            reports.append(run_replay(season, MODELS[name](season, params)) | {
                "params": {k: getattr(params, k).__dict__ for k in ("teleop", "auto", "tower")}
            })
        else:
            reports.append(run_replay(season, MODELS[name](season), upset_floor=args.upset_floor, playoff_scale=args.playoff_scale))

    all_events = {r.card.event_key for r in season.results}
    with_totals = sum(1 for r in season.results if r.red_points is not None)
    team_params = next(
        (replace(_shipped_team_params(args.season), **{k: ComponentParams(**v) for k, v in r["params"].items()})
         for r in reports if r["model"] == "team_model" and "params" in r),
        _shipped_team_params(args.season),
    )
    team_baseline = _team_model_baseline(season, team_params) if "team_model" in args.models else None
    print(json.dumps({
        "season": args.season,
        "matches": len(season.results),
        "matches_with_alliance_totals": with_totals,
        "events": len(all_events),
        "holdout_events": len(season.holdout_events),
        "typical_robot_training_events": season.typical,
        # What ratings/constants.py SEASON_BASELINES ships: the same medians over every event.
        "typical_robot_all_events": typical_robot(season.results, all_events),
        "reports": reports,
        # What ratings/constants.py ships for the team model (every event, chosen settings).
        "team_model_baseline": team_baseline,
        "grid_top5": grid_rows[:5],
        "seconds": round(time.perf_counter() - started, 1),
    }, indent=2))

    header = f"{'model':12} {'acc':>6} {'brier':>6} {'first-evt brier':>15} {'playoff brier':>13} {'alliance err':>12} {'next-evt rho':>12}"
    print(header, file=sys.stderr)
    for report in reports:
        m = report["matches"]
        print(
            f"{report['model']:12} {m['all']['accuracy']:6.3f} {m['all']['brier']:6.4f} "
            f"{m['a team at its first event'].get('brier', float('nan')):15.4f} {m['playoffs'].get('brier', float('nan')):13.4f} "
            f"{report['alliance_points_error']['mean_abs'] or float('nan'):12.1f} {report['next_event']['spearman']:12.4f}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
