#!/usr/bin/env python3
# Rating plan phase 2 experiments on cached TBA seasons (no database): E3's diagnosis
# (does match noise grow with the predicted level?), E1 (settings as ratios of the as-of
# unit, untuned, against a hindsight fit), E2 (settings by likelihood/MAP at weeks 1, 2,
# 4 and 8 against a Brier grid), E3 (level-dependent noise, drift and prior). Every fit
# sees earlier weeks only; 2022-2024 are for development, 2025-2026 are run once with
# everything frozen (--step blind).
#
#   PYTHONPATH=. python scripts/evaluate_rating_phase2.py --step diagnose
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np

from app.services.ratings.research_data import load_cached_season
from app.services.ratings.research_eval import (
    Z80,
    collect_rows,
    paired_cluster_bootstrap,
    walk_forward_probs,
)
from app.services.ratings.research_filter import ScaleFreeFilter, ScaleFreeParams
from app.services.ratings.research_fit import (
    BASE,
    WIDTH_FLOOR,
    Pull,
    ScheduledModel,
    as_of_plan,
    fit_brier,
    fit_likelihood,
    level_diagnosis,
    season_inputs,
    side_records,
)
from app.services.ratings.research_models import last_season_z, mean_share, rookies_of
from app.services.ratings.team_model import TeamModelParams

DATA = BACKEND_ROOT.parents[1] / "research-data" / "tba"
REPORTS = BACKEND_ROOT.parents[1] / "research-data" / "reports" / "phase2"
DEV = (2022, 2023, 2024)
BLIND = (2025, 2026)
REFERENCE_SEASON = 2026
WEEK_BANDS = {"weeks 1-2": lambda w, t: w <= 1, "weeks 3-5": lambda w, t: 2 <= w <= 4, "week 6+, DCMP, Championship": lambda w, t: w >= 5 or t in (2, 3, 4, 5)}

_CACHE: dict = {}


def research(year: int):
    if year not in _CACHE:
        try:
            _CACHE[year] = load_cached_season(DATA, year)
        except FileNotFoundError:
            _CACHE[year] = None
    return _CACHE[year]


def inputs_for(year: int):
    key = ("inputs", year)
    if key not in _CACHE:
        _CACHE[key] = season_inputs(research(year), research(year - 1))
    return _CACHE[key]


def stand_in() -> ScaleFreeParams:
    return ScaleFreeParams.from_points(TeamModelParams().total, mean_share(research(REFERENCE_SEASON).season.results))


def params_json(p: ScaleFreeParams) -> dict:
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in asdict(p).items()}


def params_from(d: dict) -> ScaleFreeParams:
    return ScaleFreeParams(**d)


def save(name: str, payload) -> Path:
    path = REPORTS / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def load(name: str):
    return json.loads((REPORTS / f"{name}.json").read_text())


# ── Step 1: E3 diagnosis on the existing as-of predictions ───────────────

def diagnose(year: int, params: ScaleFreeParams | None = None) -> dict:
    # Default: phase 1's stand-in exactly (its last-season z too). With params: the
    # scale-free inputs and those settings. Records each prediction's belief and noise
    # variance so the noise can be read off the errors.
    r = research(year)
    prev = research(year - 1)
    if params is None:
        model = ScaleFreeFilter(
            stand_in(),
            z_previous=last_season_z(prev.season.results) if prev else {},
            rookies=rookies_of(r.rookie_year, year),
            previous_unit=mean_share(prev.season.results) if prev else None,
            record=True,
        )
    else:
        model = inputs_for(year).model(params, record=True)
    rows = collect_rows(r, model)
    rec = side_records(rows, model.detail)
    out = {"all": level_diagnosis(rec)}
    for band, keep in WEEK_BANDS.items():
        mask = np.array([keep(int(w), int(t)) for w, t in zip(rec["week"], rec["type"])])
        out[band] = level_diagnosis({k: v[mask] for k, v in rec.items()})
    return out


def step_diagnose(seasons: list[int], fitted: bool) -> dict:
    report = {}
    for year in seasons:
        started = time.perf_counter()
        report[year] = diagnose(year, params_from(load("hindsight")[str(year)]["mle"]) if fitted else None)
        print(f"{year}: {time.perf_counter() - started:.1f}s", file=sys.stderr)
    for year, diag in report.items():
        a = diag["all"]
        print(f"\n{year}  n={a['n']}  constant noise {a['constant_noise']}  level form {a['level_form']}  loglik gain/obs {a['loglik_gain_per_obs']:+.4f}  level noise p95/p5 x{a['level_noise_ratio_p95_vs_p5']}  implied top/bottom x{a['implied_noise_ratio_top_vs_bottom']}")
        print(f"  {'dec':>3} {'n':>6} {'x':>6} {'bias%':>7} {'err_var':>8} {'belief':>7} {'implied':>8} {'model':>7}")
        for row in a["by_decile"]:
            print(f"  {row['decile']:>3} {row['n']:>6} {row['x']:>6} {row['bias_pct']:>7} {row['err_var']:>8} {row['belief_var']:>7} {row['implied_noise']:>8} {row['model_noise']:>7}")
        for band in WEEK_BANDS:
            b = diag[band]
            if b.get("n", 0) >= 200:
                print(f"  {band:30} n={b['n']:>6}  gain/obs {b['loglik_gain_per_obs']:+.4f}  level p95/p5 x{b['level_noise_ratio_p95_vs_p5']}  implied top/bottom x{b['implied_noise_ratio_top_vs_bottom']}")
    return report


# ── Hindsight: settings fitted on the whole season (a ceiling; it leaks) ──

def _hindsight(year: int) -> dict:
    inputs = inputs_for(year)
    started = time.perf_counter()
    mle = fit_likelihood(inputs, stand_in(), BASE, None)
    brier = fit_brier(inputs, mle["params"], BASE, None)
    return {
        "mle": params_json(mle["params"]),
        "mle_loglik_per_obs_units": round(mle["loglik_per_obs_units"], 5),
        "mle_evals": mle["evals"],
        "brier": params_json(brier["params"]),
        "brier_insample": round(brier["objective"], 5),
        "brier_evals": brier["evals"],
        "seconds": round(time.perf_counter() - started),
    }


def step_hindsight(seasons: list[int]) -> dict:
    existing = load("hindsight") if (REPORTS / "hindsight.json").exists() else {}
    with ProcessPoolExecutor(max_workers=min(8, len(seasons))) as pool:
        for year, out in zip(seasons, pool.map(_hindsight, seasons)):
            existing[str(year)] = out
            print(year, json.dumps(out), file=sys.stderr)
    return existing


# ── Variants: a schedule of settings per season ──────────────────────────

LEVEL_START = {"noise_count": 0.005, "noise_prop": 0.05, "prior_level": 0.05}  # small: week 0 ~ unchanged
VARIANTS: dict[str, dict] = {
    "p1_standin": {"kind": "fixed", "params": "standin", "z": "phase1"},  # phase 1's team_model_total
    "e1_standin": {"kind": "fixed", "params": "standin"},  # 2026 ratios, scale-free z
    "e1_prior": {"kind": "fixed", "params": "prior"},  # earlier seasons' fits, never re-fitted
    "hindsight": {"kind": "fixed", "params": "hindsight_brier"},
    "hindsight_mle": {"kind": "fixed", "params": "hindsight_mle"},
    "e2_map": {"kind": "schedule", "method": "map"},
    "e2_mle": {"kind": "schedule", "method": "mle"},
    "e2_brier": {"kind": "schedule", "method": "brier"},
    "e2_map_t": {"kind": "schedule", "method": "map", "df": 5.0},
    "e3_noise": {"kind": "schedule", "method": "map", "extra": {"noise_count": 0.005, "noise_prop": 0.05}},
    "e3_noise_count": {"kind": "schedule", "method": "map", "extra": {"noise_count": 0.005}},
    "e3_drift": {"kind": "schedule", "method": "map", "extra": {"drift_level": True}},
    "e3_prior": {"kind": "schedule", "method": "map", "extra": {"prior_level": 0.05}},
}


# Frozen before any 2025/2026 run (decided on 2022-2024): E1's two bootstraps against
# the hindsight ceiling, E2's MAP schedule against the Brier grid, and the bundle
# (MAP + noise variance growing with the predicted total), plus the full noise form.
BLIND_VARIANTS = ("p1_standin", "e1_standin", "e1_prior", "hindsight", "hindsight_mle", "e2_map", "e2_brier", "e3_noise_count", "e3_noise")
BLIND_PAIRS = (
    ("e1_standin", "hindsight"), ("e1_prior", "hindsight"), ("e2_map", "e2_brier"), ("e3_noise_count", "e2_map"),
    ("e3_noise", "e2_map"), ("e3_noise_count", "hindsight"), ("e3_noise_count", "p1_standin"),
)


def season_pull(year: int) -> Pull:
    # Centre and width from the likelihood fits of seasons before `year` (plan 3.6); a
    # setting no earlier season fitted is centred on the stand-in. 2022 has none.
    h = load("hindsight") if (REPORTS / "hindsight.json").exists() else {}
    base = stand_in()
    centre, width = {}, {}
    for name in BASE:
        logs = [math.log(h[y]["mle"][name]) for y in sorted(h) if int(y) < year and (name != "beta" or research(int(y) - 1) is not None)]
        centre[name] = float(np.mean(logs)) if logs else math.log(getattr(base, name))
        width[name] = max(float(np.std(logs, ddof=1)) if len(logs) > 1 else 0.0, WIDTH_FLOOR)
    return Pull(centre, width)


def _fixed_params(year: int, which: str) -> ScaleFreeParams:
    if which == "standin":
        return stand_in()
    if which == "prior":
        return season_pull(year).centred(stand_in())
    h = load("hindsight")[str(year)]
    return params_from(h["mle"] if which == "hindsight_mle" else h["brier"])


def run_variant(job: tuple[int, str]) -> dict:
    year, name = job
    spec = VARIANTS[name]
    started = time.perf_counter()
    inputs = inputs_for(year)
    if spec.get("z") == "phase1":
        prev = research(year - 1)
        inputs = replace(inputs, z_previous=last_season_z(prev.season.results) if prev else {})
    if spec["kind"] == "fixed":
        plan = [{"from_week": 0, "params": _fixed_params(year, spec["params"]), "fit": spec["params"]}]
    else:
        pull = season_pull(year)
        extra = spec.get("extra", {})
        bootstrap = replace(pull.centred(stand_in()), **extra)
        names = BASE + tuple(k for k, v in extra.items() if not isinstance(v, bool))
        plan = as_of_plan(inputs, bootstrap, names, method=spec["method"], pull=pull, df=spec.get("df"))
    model = ScheduledModel(inputs, [(p["from_week"], p["params"]) for p in plan], name=name)
    rows = collect_rows(inputs.research, model)
    probs, _, scales = walk_forward_probs(rows)
    matches = [
        [r.match_key, r.event_key, r.week, r.event_type, r.comp_level, r.min_here, r.min_before, r.y, round(p, 6),
         r.prediction.red_mean, r.prediction.red_var, r.red_actual, r.prediction.blue_mean, r.prediction.blue_var, r.blue_actual]
        for r, p in zip(rows, probs)
    ]
    out = {
        "variant": name,
        "season": year,
        "plan": [{**{k: v for k, v in p.items() if k != "params"}, "params": params_json(p["params"])} for p in plan],
        "scales": scales,
        "seconds": round(time.perf_counter() - started),
        "matches": matches,
    }
    save(f"runs/{name}-{year}", out)
    return {k: v for k, v in out.items() if k != "matches"}


def step_run(seasons: list[int], variants: list[str]) -> None:
    jobs = [(y, v) for v in variants for y in seasons]
    with ProcessPoolExecutor(max_workers=8) as pool:
        for out in pool.map(run_variant, jobs):
            print(f"{out['season']} {out['variant']}: {out['seconds']}s  plan " + "; ".join(f"w{p['from_week']} {p['fit']}" for p in out["plan"]), file=sys.stderr)


# ── Report ────────────────────────────────────────────────────────────────

COLUMNS = ("match_key", "event_key", "week", "event_type", "comp_level", "min_here", "min_before", "y", "p", "rm", "rv", "ra", "bm", "bv", "ba")
SLICES = {
    "all": lambda m: True,
    "weeks 1-2": lambda m: m["week"] <= 1,
    "after the first fit (week 2+)": lambda m: m["week"] >= 1,
    "DCMP + Championship": lambda m: m["event_type"] in (2, 3, 4, 5),
}


def load_run(name: str, year: int) -> list[dict] | None:
    path = REPORTS / "runs" / f"{name}-{year}.json"
    if not path.exists():
        return None
    return [dict(zip(COLUMNS, row)) for row in json.loads(path.read_text())["matches"]]


def _sides(m: dict):
    for mean, var, actual in ((m["rm"], m["rv"], m["ra"]), (m["bm"], m["bv"], m["ba"])):
        if actual is not None and var and var > 0:
            yield mean, var, actual


def per_match(matches: list[dict]) -> dict[str, dict[str, tuple[str, float]]]:
    # Per-match losses for the paired bootstrap (lower is better for all three).
    out: dict[str, dict] = {"brier": {}, "neg_loglik": {}, "abs_error": {}}
    for m in matches:
        out["brier"][m["match_key"]] = (m["event_key"], (m["p"] - m["y"]) ** 2)
        sides = list(_sides(m))
        if sides:
            ll = [-0.5 * (math.log(2 * math.pi * v) + (a - mu) ** 2 / v) for mu, v, a in sides]
            out["neg_loglik"][m["match_key"]] = (m["event_key"], -sum(ll) / len(ll))
            out["abs_error"][m["match_key"]] = (m["event_key"], sum(abs(a - mu) for mu, _v, a in sides) / len(sides))
    return out


def summary(matches: list[dict]) -> dict:
    n = len(matches)
    if not n:
        return {"n": 0}
    p = np.clip(np.array([m["p"] for m in matches]), 1e-6, 1 - 1e-6)
    y = np.array([m["y"] for m in matches])
    sides = [s for m in matches for s in _sides(m)]
    mu = np.array([s[0] for s in sides])
    var = np.array([s[1] for s in sides])
    act = np.array([s[2] for s in sides])
    nu = act - mu
    top = mu >= np.quantile(mu, 0.9)
    fav = np.maximum(p, 1 - p)
    fav_won = np.where(p >= 0.5, y, 1 - y)
    extreme = fav >= 0.9
    return {
        "n": n,
        "brier": round(float(np.mean((p - y) ** 2)), 5),
        "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 5),
        "accuracy": round(float(np.mean((p > 0.5) == (y > 0.5))), 4),
        "loglik_per_obs": round(float(np.mean(-0.5 * (np.log(2 * math.pi * var) + nu * nu / var))), 4),
        "points_mae": round(float(np.mean(np.abs(nu))), 3),
        "coverage_80": round(float(np.mean(np.abs(nu) <= Z80 * np.sqrt(var))), 4),
        "top_decile_bias_pct": round(float(100 * nu[top].sum() / mu[top].sum()), 2),
        "top_decile_coverage_80": round(float(np.mean(np.abs(nu[top]) <= Z80 * np.sqrt(var[top]))), 4),
        "extreme_n": int(extreme.sum()),
        "extreme_calibration_gap": round(float(fav[extreme].mean() - fav_won[extreme].mean()), 4) if extreme.any() else None,
    }


def compare(a: list[dict], b: list[dict], keep) -> dict:
    # a minus b, paired on the same matches; negative = a better for every loss.
    la, lb = per_match([m for m in a if keep(m)]), per_match([m for m in b if keep(m)])
    return {metric: paired_cluster_bootstrap(la[metric], lb[metric], n=2000, seed=7) for metric in la}


def _fmt(d: dict) -> str:
    if not d or not d.get("n"):
        return "n/a"
    return f"{d['mean_diff']:+.4f} [{d['lo90']:+.4f}, {d['hi90']:+.4f}]"


def step_report(seasons: list[int], pairs: list[tuple[str, str]], label: str) -> dict:
    report: dict = {"seasons": seasons, "summaries": {}, "comparisons": {}}
    names = sorted({n for pair in pairs for n in pair})
    runs = {(n, y): load_run(n, y) for n in names for y in seasons}
    for n in names:
        for y in seasons:
            if runs[(n, y)] is not None:
                report["summaries"][f"{n} {y}"] = {s: summary([m for m in runs[(n, y)] if keep(m)]) for s, keep in SLICES.items()}
        pooled = [m for y in seasons for m in (runs[(n, y)] or [])]
        if all(runs[(n, y)] is not None for y in seasons):
            # Pooled deciles: each season's own top decile, so a high-scoring game doesn't own it.
            report["summaries"][f"{n} pooled"] = {s: summary([m for m in pooled if keep(m)]) for s, keep in SLICES.items()}
            report["summaries"][f"{n} pooled"]["all"]["top_decile_bias_pct"] = round(float(np.mean([report["summaries"][f"{n} {y}"]["all"]["top_decile_bias_pct"] for y in seasons])), 2)
    for a, b in pairs:
        key = f"{a} vs {b}"
        report["comparisons"][key] = {}
        for y in seasons + ["pooled"]:
            ra = [m for yy in seasons for m in (runs[(a, yy)] or [])] if y == "pooled" else runs[(a, y)]
            rb = [m for yy in seasons for m in (runs[(b, yy)] or [])] if y == "pooled" else runs[(b, y)]
            if ra and rb:
                report["comparisons"][key][str(y)] = {s: compare(ra, rb, keep) for s, keep in SLICES.items()}
    print_report(report)
    return report


def print_report(report: dict) -> None:
    print("\nSummaries (loglik and MAE in points; top-decile bias = sum(actual - predicted) / sum(predicted) in the season's top decile of predicted alliance score)")
    print(f"{'run':24} {'slice':30} {'n':>6} {'brier':>7} {'logloss':>8} {'acc':>6} {'ll/obs':>7} {'MAE':>7} {'cov80':>6} {'top%':>6} {'topcov':>6} {'ext n':>6} {'ext gap':>7}")
    for run, slices in report["summaries"].items():
        for s, v in slices.items():
            if v.get("n"):
                print(f"{run:24} {s:30} {v['n']:>6} {v['brier']:>7.4f} {v['log_loss']:>8.4f} {v['accuracy']:>6.3f} {v['loglik_per_obs']:>7.3f} {v['points_mae']:>7.2f} {v['coverage_80']:>6.3f} {v['top_decile_bias_pct']:>6.2f} {v['top_decile_coverage_80']:>6.3f} {v['extreme_n']:>6} {str(v['extreme_calibration_gap']):>7}")
    print("\nPaired differences, a - b (negative = a better), 90% event-cluster bootstrap")
    for key, by_season in report["comparisons"].items():
        print(f"\n{key}")
        for y, slices in by_season.items():
            for s, c in slices.items():
                print(f"  {y:7} {s:30} Brier {_fmt(c['brier']):28} -loglik {_fmt(c['neg_loglik']):28} MAE {_fmt(c['abs_error'])}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, choices=["diagnose", "hindsight", "run", "report", "blind"])
    parser.add_argument("--fitted", action="store_true", help="diagnose: use the hindsight likelihood fit, not the stand-in")
    parser.add_argument("--seasons", default=",".join(map(str, DEV)))
    parser.add_argument("--variants", default="", help="run: comma-separated VARIANTS names")
    parser.add_argument("--pairs", default="", help="report: a:b,a:b")
    parser.add_argument("--label", default="report")
    args = parser.parse_args()
    seasons = [int(s) for s in args.seasons.split(",")]
    if args.step == "run":
        step_run(seasons, [v for v in args.variants.split(",") if v])
    if args.step == "blind":
        # Once per frozen bundle: hindsight fits first (2026's MAP centre includes 2025's).
        save("hindsight", step_hindsight(list(BLIND)))
        step_run(list(BLIND), list(BLIND_VARIANTS))
        print(f"\nreport: {save('blind', step_report(list(BLIND), list(BLIND_PAIRS), 'blind'))}", file=sys.stderr)
    if args.step == "report":
        pairs = [tuple(p.split(":")) for p in args.pairs.split(",") if p]
        print(f"\nreport: {save(args.label, step_report(seasons, pairs, args.label))}", file=sys.stderr)
    if args.step == "diagnose":
        path = save(f"diagnose{'-fitted' if args.fitted else ''}-{'-'.join(map(str, seasons))}", step_diagnose(seasons, args.fitted))
        print(f"\nreport: {path}", file=sys.stderr)
    if args.step == "hindsight":
        print(f"report: {save('hindsight', step_hindsight(seasons))}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
