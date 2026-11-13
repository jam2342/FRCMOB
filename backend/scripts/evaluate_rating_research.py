#!/usr/bin/env python3
# Walk-forward comparison of rating models over cached TBA seasons (no database).
# Fetch the data first: scripts/fetch_tba_season.py. Every forecast is as-of: a match is
# predicted before its result is seen, and the win-probability scale for each week is
# fitted on earlier weeks only. Paired intervals resample whole events.
#
#   PYTHONPATH=. python scripts/evaluate_rating_research.py --seasons 2022,2023,2024,2025,2026 --diagnostics
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.ratings.constants import season_baseline
from app.services.ratings.research_data import ResearchSeason, load_cached_season
from app.services.ratings.research_eval import (
    Row,
    alliance_scores,
    collect_rows,
    evaluate,
    paired_cluster_bootstrap,
    per_match_brier,
    walk_forward_probs,
)
from app.services.ratings.research_models import (
    EpaClone,
    RidgePriorOpr,
    ScaleFreeTotalModel,
    last_season_z,
    mean_share,
    rookies_of,
    start_multipliers,
)
from app.services.ratings.team_model import SeasonTeamModel

DEFAULT_DATA = BACKEND_ROOT.parents[1] / "research-data" / "tba"
REFERENCE_SEASON = 2026  # the shipped settings are in its points
MODELS = ("team_model", "team_model_total", "epa_clone", "ridge_prior")


def build_model(name: str, year: int, research: ResearchSeason, previous: ResearchSeason | None, reference_unit: float):
    z_previous = last_season_z(previous.season.results) if previous else {}
    multipliers = start_multipliers(z_previous, rookies_of(research.rookie_year, year))
    previous_unit = mean_share(previous.season.results) if previous else None
    if name == "team_model" and year == REFERENCE_SEASON:
        # The live configuration: hub + tower components, duds, 2026's priors and settings.
        baseline = season_baseline(year)
        model = SeasonTeamModel(
            dict(baseline["team_model_priors"]),
            baseline["team_model_params"],
            team_multipliers=dict(multipliers),
            team_var_multipliers={team: float(baseline.get("prior_known_var_multiplier", 1.0)) for team in z_previous},
            week_multipliers=list(baseline.get("week_multipliers") or []),
        )
        return model, lambda card: model.forecast(card.event_key, card.red, card.blue, card.event_start)
    if name in ("team_model", "team_model_total"):
        return ScaleFreeTotalModel(multipliers, previous_unit, reference_unit, name=name), None
    if name == "epa_clone":
        return EpaClone(multipliers, previous_unit), None
    if name == "ridge_prior":
        return RidgePriorOpr(multipliers, previous_unit), None
    raise SystemExit(f"unknown model {name}")


def run_season(year: int, names: list[str], data: Path, cache: dict[int, ResearchSeason], reference_unit: float) -> dict:
    research = cache[year]
    previous = cache.get(year - 1)
    out = {}
    for name in names:
        started = time.perf_counter()
        model, alt = build_model(name, year, research, previous, reference_unit)
        rows = collect_rows(research, model, alt=alt)
        probs, alt_probs, scales = walk_forward_probs(rows)
        out[name] = {"rows": rows, "probs": probs, "alt_probs": alt_probs, "scales": scales, "seconds": round(time.perf_counter() - started, 1)}
        print(f"  {year} {name}: {len(rows)} matches, {out[name]['seconds']}s", file=sys.stderr)
    return out


def summarize(per_model: dict) -> dict:
    return {name: evaluate(d["rows"], d["probs"], d["alt_probs"]) for name, d in per_model.items()}


def pooled(by_season: dict[int, dict], names: list[str]) -> dict:
    out = {}
    for name in names:
        rows: list[Row] = []
        probs: list[float] = []
        for season_data in by_season.values():
            rows += season_data[name]["rows"]
            probs += season_data[name]["probs"]
        out[name] = {"rows": rows, "probs": probs, "alt_probs": None}
    return out


def intervals(per_model: dict, baseline: str) -> dict:
    base = per_match_brier(per_model[baseline]["rows"], per_model[baseline]["probs"])
    out = {}
    for name, d in per_model.items():
        if name != baseline:
            out[name] = paired_cluster_bootstrap(per_match_brier(d["rows"], d["probs"]), base)
    return out


def diagnostics(rows: list[Row]) -> dict:
    # Does the model's spread mean what it says? By predicted-score decile, week, level.
    def sub(keep):
        return alliance_scores([r for r in rows if keep(r)])

    weeks = sorted({r.week for r in rows})
    return {
        "all": alliance_scores(rows),
        "newest 20% of events": sub(lambda r: r.holdout),
        "district championships and Championship": sub(lambda r: r.event_type in (2, 3, 4, 5)),
        "by_week": {w: {k: v for k, v in sub(lambda r, w=w: r.week == w).items() if k != "by_decile"} for w in weeks},
        "quals": {k: v for k, v in sub(lambda r: r.comp_level == "qm").items() if k != "by_decile"},
        "playoffs": {k: v for k, v in sub(lambda r: r.comp_level != "qm").items() if k != "by_decile"},
        "playoff_seed": "not available: alliance lists aren't cached yet",
    }


def print_table(title: str, summary: dict, ci: dict, slice_name: str) -> None:
    print(f"\n{title}  [{slice_name}]")
    print(f"{'model':18} {'n':>6} {'acc':>7} {'brier':>7} {'logloss':>8} {'slope':>6} {'pts MAE':>8} {'cov80':>6}  Brier diff vs team_model (90% CI)")
    for name, s in summary.items():
        sl = s["slices"].get(slice_name, {})
        if not sl.get("n"):
            continue
        d = ci.get(name) if slice_name == "all" else None
        diff = f"{d['mean_diff']:+.4f} [{d['lo90']:+.4f}, {d['hi90']:+.4f}]" if d and d.get("n") else ""
        print(f"{name:18} {sl['n']:>6} {sl['accuracy']:>7.4f} {sl['brier']:>7.4f} {sl['log_loss']:>8.4f} {str(sl.get('calibration_slope')):>6} {str(sl.get('points_mae')):>8} {str(sl.get('coverage_80', '')):>6}  {diff}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", default="2022,2023,2024,2025,2026")
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--out", type=Path, help="write the full JSON report here")
    args = parser.parse_args()
    seasons = sorted(int(s) for s in args.seasons.split(","))
    names = args.models.split(",")
    baseline = "team_model" if "team_model" in names else names[0]

    cache: dict[int, ResearchSeason] = {}
    for year in sorted(set(seasons) | {s - 1 for s in seasons} | {REFERENCE_SEASON}):
        try:
            cache[year] = load_cached_season(args.data, year)
        except FileNotFoundError:
            if year in seasons or year == REFERENCE_SEASON:
                raise
    reference_unit = mean_share(cache[REFERENCE_SEASON].season.results)

    report: dict = {
        "note": "team_model is the live 2026 configuration on 2026 and a stand-in elsewhere: the shipped total-score settings as ratios of 2026's mean per-robot share, rescaled by each season's own as-of mean. Replaced by fitted settings in phase 2.",
        "reference_unit_2026_points_per_robot": round(reference_unit, 2),
        "seasons": {},
    }
    by_season: dict[int, dict] = {}
    for year in seasons:
        print(f"season {year}", file=sys.stderr)
        by_season[year] = run_season(year, names, args.data, cache, reference_unit)
        summary = summarize(by_season[year])
        ci = intervals(by_season[year], baseline)
        report["seasons"][year] = {
            "matches": len(by_season[year][baseline]["rows"]),
            "loader_skipped": cache[year].skipped,
            "models": summary,
            "brier_vs_" + baseline: ci,
            "week_scales": {n: d["scales"] for n, d in by_season[year].items()},
        }
        if args.diagnostics:
            report["seasons"][year]["diagnostics"] = {n: diagnostics(d["rows"]) for n, d in by_season[year].items() if n in (baseline, "team_model_total")}
    if len(seasons) > 1:
        combined = pooled(by_season, names)
        report["pooled"] = {"models": summarize(combined), "brier_vs_" + baseline: intervals(combined, baseline)}

    for year in seasons:
        s = report["seasons"][year]
        for slice_name in ("all", "holdout (newest 20% of events)", "weeks 1-2"):
            print_table(f"{year} ({s['matches']} matches)", s["models"], s["brier_vs_" + baseline], slice_name)
    if "pooled" in report:
        for slice_name in ("all", "weeks 1-2", "Championship"):
            print_table("pooled", report["pooled"]["models"], report["pooled"]["brier_vs_" + baseline], slice_name)
    if args.diagnostics:
        for year in seasons:
            for name, diag in report["seasons"][year]["diagnostics"].items():
                print(f"\n{year} {name}: standardized error by decile of predicted alliance score (z_variance near 1 = spread is right; mean_error = actual - predicted)")
                for row in diag["all"].get("by_decile", []):
                    print("  ", row)
                fp = report["seasons"][year]["models"][name].get("forecast_vs_predict")
                if fp:
                    print("   forecast() vs predict():", json.dumps(fp))
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, default=str))
        print(f"\nfull report: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
