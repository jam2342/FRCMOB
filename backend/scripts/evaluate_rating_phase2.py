#!/usr/bin/env python3
# Rating plan phase 2 experiments on cached TBA seasons (no database): E3's diagnosis
# (does match noise grow with the predicted level?), E1 (settings as ratios of the as-of
# unit, untuned, against a hindsight reference), E2 (settings by likelihood/MAP when FRC
# weeks 2, 3 and 5 and the Championship start, against a Brier grid), E3 (level-dependent
# noise, drift and prior). Every fit sees only matches played before it; 2022-2024 are
# for development, 2025-2026 are run once with everything frozen (--step blind).
#
#   PYTHONPATH=. python scripts/evaluate_rating_phase2.py --step hindsight --seasons 2022,2023,2024
#   PYTHONPATH=. python scripts/evaluate_rating_phase2.py --step run --variants e2_map,e2_brier
#   PYTHONPATH=. python scripts/evaluate_rating_phase2.py --step report --pairs e2_map:e2_brier
from __future__ import annotations

# ruff: noqa: E402

import argparse
import hashlib
import json
import math
import subprocess
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
    scale_free_z,
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


def hindsight_fits() -> dict:
    path = REPORTS / "hindsight.json"
    return json.loads(path.read_text()) if path.exists() else {}


def inputs_for(year: int):
    # Last season's strengths come from last season run on its own fitted settings
    # (a finished season); 2022 has no cached 2021.
    key = ("inputs", year)
    if key not in _CACHE:
        previous = hindsight_fits().get(str(year - 1))
        _CACHE[key] = season_inputs(research(year), research(year - 1), params_from(previous["mle"]) if previous else None)
    return _CACHE[key]


def stand_in() -> ScaleFreeParams:
    # Phase 1's stand-in: the shipped settings over 2026's whole-season mean share. It
    # uses 2026 results, so it is never part of a blind 2025/2026 bundle: a comparator,
    # and the only centre 2022 has (no earlier season).
    return ScaleFreeParams.from_points(TeamModelParams().total, mean_share(research(REFERENCE_SEASON).season.results))


def params_json(p: ScaleFreeParams) -> dict:
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in asdict(p).items()}


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
            replace(stand_in(), margin_cov=False),
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
        report[year] = diagnose(year, params_from(hindsight_fits()[str(year)]["mle"]) if fitted else None)
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


# ── Hindsight: settings fitted on the whole season (a reference; it leaks) ──

def _mle(year: int) -> dict:
    inputs = inputs_for(year)
    started = time.perf_counter()
    out = fit_likelihood(inputs, season_pull(year).centred(stand_in()), BASE, None)
    return {
        "mle": params_json(out["params"]),
        "mle_loglik_per_obs_units": round(out["loglik_per_obs_units"], 5),
        "mle_evals": out["evals"],
        "mle_converged": out["converged"],
        "mle_seconds": round(time.perf_counter() - started),
    }


def _brier_reference(year: int) -> dict:
    # The reported (walk-forward) Brier, coordinate search from two starts: the
    # whole-season likelihood fit and the earlier seasons' centre. A local reference,
    # not a guaranteed ceiling.
    inputs = inputs_for(year)
    started = time.perf_counter()
    best = None
    for label, start in (("mle", params_from(hindsight_fits()[str(year)]["mle"])), ("centre", season_pull(year).centred(stand_in()))):
        out = fit_brier(inputs, start, BASE, None, pipeline="walk_forward")
        out["start"] = label
        if best is None or out["objective"] < best["objective"]:
            best = out
    return {"brier": params_json(best["params"]), "brier_walk_forward": round(best["objective"], 6), "brier_start": best["start"], "brier_seconds": round(time.perf_counter() - started)}


def step_hindsight(seasons: list[int]) -> dict:
    # In season order: each season's z uses the season before's fit.
    fits = hindsight_fits()
    for year in sorted(seasons):
        fits[str(year)] = _mle(year)
        save("hindsight", fits)
        print(year, json.dumps(fits[str(year)]), file=sys.stderr)
    with ProcessPoolExecutor(max_workers=min(8, len(seasons))) as pool:
        for year, out in zip(seasons, pool.map(_brier_reference, seasons)):
            fits[str(year)].update(out)
            print(year, json.dumps(out), file=sys.stderr)
    save("hindsight", fits)
    return fits


# ── Variants: a schedule of settings per season ──────────────────────────

VARIANTS: dict[str, dict] = {
    "p1_standin": {"kind": "fixed", "params": "standin", "z": "phase1", "margin_cov": False},  # phase 1's team_model_total
    "e1_standin": {"kind": "fixed", "params": "standin"},  # 2026 ratios, scale-free z
    "e1_prior": {"kind": "fixed", "params": "prior"},  # earlier seasons' fits, never re-fitted
    "e1_prior_zstatic": {"kind": "fixed", "params": "prior", "z": "static"},
    "hindsight": {"kind": "fixed", "params": "hindsight_brier"},
    "hindsight_mle": {"kind": "fixed", "params": "hindsight_mle"},
    "e2_map": {"kind": "schedule", "method": "map"},
    "e2_map_nocov": {"kind": "schedule", "method": "map", "margin_cov": False},
    "e2_mle": {"kind": "schedule", "method": "mle"},
    "e2_brier": {"kind": "schedule", "method": "brier"},
    "e2_map_t": {"kind": "schedule", "method": "map", "df": 5.0},
    "e3_noise": {"kind": "schedule", "method": "map", "extra": {"noise_count": 0.005, "noise_prop": 0.05}},
    "e3_noise_count": {"kind": "schedule", "method": "map", "extra": {"noise_count": 0.005}},
    "e3_noise_count_zstatic": {"kind": "schedule", "method": "map", "extra": {"noise_count": 0.005}, "z": "static"},
    "e3_noise_count_nocov": {"kind": "schedule", "method": "map", "extra": {"noise_count": 0.005}, "margin_cov": False},
    "e3_drift": {"kind": "schedule", "method": "map", "extra": {"drift_level": True}},
    "e3_prior": {"kind": "schedule", "method": "map", "extra": {"prior_level": 0.05}},
}

# Frozen before any 2025/2026 run, decided on 2022-2024 only (notes/HISTORY.md,
# 2026-11-13). The bundle: MAP fits + noise variance growing with the predicted total +
# last season's average strength as the start. The rest are its parts and comparators;
# p1_standin and e1_standin use 2026's mean, so on 2026 they are in-sample, not blind.
BUNDLE = "e3_noise_count_zstatic"
BLIND_VARIANTS = (
    "p1_standin", "e1_standin", "e1_prior", "e1_prior_zstatic", "hindsight", "hindsight_mle",
    "e2_map", "e2_brier", "e3_noise_count", BUNDLE,
)
BLIND_PAIRS = (
    ("e1_standin", "hindsight"), ("e1_prior", "hindsight"), ("e1_prior_zstatic", "hindsight"),
    ("e2_map", "e2_brier"), ("e3_noise_count", "e2_map"), (BUNDLE, "e2_map"), (BUNDLE, "e3_noise_count"),
    (BUNDLE, "e2_brier"), (BUNDLE, "hindsight"), (BUNDLE, "p1_standin"),
)


def season_pull(year: int) -> Pull:
    # Centre and width from the likelihood fits of seasons before `year` (plan 3.6); a
    # setting no earlier season fitted is centred on the stand-in. 2022 has none.
    h = hindsight_fits()
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
    h = hindsight_fits()[str(year)]
    return params_from(h["mle"] if which == "hindsight_mle" else h["brier"])


def _plan_json(p: dict) -> dict:
    return {**{k: v for k, v in p.items() if k != "params"}, "params": params_json(p["params"])}


def run_variant(job: tuple[int, str]) -> dict:
    year, name = job
    spec = VARIANTS[name]
    started = time.perf_counter()
    inputs = inputs_for(year)
    prev = research(year - 1)
    if spec.get("z") == "phase1":
        inputs = replace(inputs, z_previous=last_season_z(prev.season.results) if prev else {})
    if spec.get("z") == "static" and prev is not None:
        # Last season's average strength, not its end state: its own fit with no drift.
        fit = params_from(hindsight_fits()[str(year - 1)]["mle"])
        inputs = replace(inputs, z_previous=scale_free_z(prev.season.results, replace(fit, match=1e-6, event=1e-6)))
    cov = {"margin_cov": spec.get("margin_cov", True)}
    if spec["kind"] == "fixed":
        plan = [{"from_time": 0, "label": "all season", "params": replace(_fixed_params(year, spec["params"]), **cov), "fit": spec["params"]}]
    else:
        pull = season_pull(year)
        extra = spec.get("extra", {})
        bootstrap = replace(pull.centred(stand_in()), **extra, **cov)
        names = BASE + tuple(k for k, v in extra.items() if not isinstance(v, bool))
        plan = as_of_plan(inputs, bootstrap, names, method=spec["method"], pull=pull, df=spec.get("df"))
    model = ScheduledModel(inputs, [(p["from_time"], p["params"]) for p in plan], name=name)
    rows = collect_rows(inputs.research, model, keep_ties=True)
    probs, _, scales = walk_forward_probs(rows)
    matches = [
        [r.match_key, r.event_key, r.week, r.event_type, r.comp_level, r.min_here, r.min_before, r.y, round(p, 6),
         r.prediction.red_mean, r.prediction.red_var, r.red_actual, r.prediction.blue_mean, r.prediction.blue_var, r.blue_actual, r.time]
        for r, p in zip(rows, probs)
    ]
    out = {
        "variant": name,
        "season": year,
        "plan": [_plan_json(p) for p in plan],
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
            fits = "; ".join(f"{p['label']} {p['fit']}" + ("" if p.get("converged", True) else " (NOT converged)") + (f" zeroed {p['zeroed']}" if p.get("zeroed") else "") for p in out["plan"])
            print(f"{out['season']} {out['variant']}: {out['seconds']}s  plan {fits}", file=sys.stderr, flush=True)


# ── Report ────────────────────────────────────────────────────────────────

COLUMNS = ("match_key", "event_key", "week", "event_type", "comp_level", "min_here", "min_before", "y", "p", "rm", "rv", "ra", "bm", "bv", "ba", "time")
SLICES = {
    "all": lambda m: True,
    "weeks 1-2": lambda m: m["week"] <= 1,
    "after the first fit (week 2+)": lambda m: m["week"] >= 1,
    "DCMP + Championship": lambda m: m["event_type"] in (2, 3, 4, 5),
}
FAV_BINS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0 + 1e-9)


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
    # Per-match losses for the paired bootstrap (lower is better for all three). Ties
    # count for the score losses only.
    out: dict[str, dict] = {"brier": {}, "neg_loglik": {}, "abs_error": {}}
    for m in matches:
        if m["y"] != 0.5:
            out["brier"][m["match_key"]] = (m["event_key"], (m["p"] - m["y"]) ** 2)
        sides = list(_sides(m))
        if sides:
            ll = [-0.5 * (math.log(2 * math.pi * v) + (a - mu) ** 2 / v) for mu, v, a in sides]
            out["neg_loglik"][m["match_key"]] = (m["event_key"], -sum(ll) / len(ll))
            out["abs_error"][m["match_key"]] = (m["event_key"], sum(abs(a - mu) for mu, _v, a in sides) / len(sides))
    return out


def _favourite(matches: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    p = np.array([m["p"] for m in matches])
    y = np.array([m["y"] for m in matches])
    return np.maximum(p, 1 - p), np.where(p >= 0.5, y, 1 - y), np.clip(np.searchsorted(FAV_BINS, np.maximum(p, 1 - p), side="right") - 1, 0, len(FAV_BINS) - 2)


def calibration(matches: list[dict]) -> dict:
    # Absolute calibration error of the favourite's chance, by fixed bins: overall (ECE)
    # and in the 0.8-0.9 and 0.9+ bins.
    fav, won, which = _favourite(matches)
    out = {"ece": 0.0}
    for b in range(len(FAV_BINS) - 1):
        m = which == b
        if m.any():
            gap = abs(float(fav[m].mean() - won[m].mean()))
            out["ece"] += gap * m.sum() / len(fav)
            if b >= 3:
                out[f"gap_{FAV_BINS[b]:.1f}"] = round(gap, 4)
                out[f"n_{FAV_BINS[b]:.1f}"] = int(m.sum())
    out["ece"] = round(out["ece"], 4)
    return out


def summary(matches: list[dict]) -> dict:
    decided = [m for m in matches if m["y"] != 0.5]
    if not decided:
        return {"n": 0}
    p = np.clip(np.array([m["p"] for m in decided]), 1e-6, 1 - 1e-6)
    y = np.array([m["y"] for m in decided])
    sides = [s for m in matches for s in _sides(m)]
    mu = np.array([s[0] for s in sides])
    var = np.array([s[1] for s in sides])
    act = np.array([s[2] for s in sides])
    nu = act - mu
    top = mu >= np.quantile(mu, 0.9)
    return {
        "n": len(decided),
        "brier": round(float(np.mean((p - y) ** 2)), 5),
        "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 5),
        "accuracy": round(float(np.mean((p > 0.5) == (y > 0.5))), 4),
        "sides": len(sides),
        "loglik_per_obs": round(float(np.mean(-0.5 * (np.log(2 * math.pi * var) + nu * nu / var))), 4),
        "points_mae": round(float(np.mean(np.abs(nu))), 3),
        "coverage_80": round(float(np.mean(np.abs(nu) <= Z80 * np.sqrt(var))), 4),
        "top_decile_bias_pct": round(float(100 * nu[top].sum() / mu[top].sum()), 2),
        "top_decile_coverage_80": round(float(np.mean(np.abs(nu[top]) <= Z80 * np.sqrt(var[top]))), 4),
        **calibration(decided),
    }


def calibration_compare(a: list[dict], b: list[dict], *, n: int = 1000, seed: int = 11) -> dict:
    # a minus b on the same decided matches, resampling whole events: ECE and the 0.9+
    # bin's absolute gap, each model in its own bins, and a's ECE in b's bins.
    keys = sorted({m["match_key"] for m in a if m["y"] != 0.5} & {m["match_key"] for m in b if m["y"] != 0.5})
    if not keys:
        return {}
    ia = {m["match_key"]: m for m in a}
    ib = {m["match_key"]: m for m in b}
    ma, mb = [ia[k] for k in keys], [ib[k] for k in keys]
    events = sorted({m["event_key"] for m in ma})
    position = {e: i for i, e in enumerate(events)}
    e_index = np.array([position[m["event_key"]] for m in ma])
    nb = len(FAV_BINS) - 1

    def sums(fav, won, which):
        cnt, sf, sw = (np.zeros((len(events), nb)) for _ in range(3))
        np.add.at(cnt, (e_index, which), 1.0)
        np.add.at(sf, (e_index, which), fav)
        np.add.at(sw, (e_index, which), won)
        return cnt, sf, sw

    fa, wa, ba = _favourite(ma)
    fb, wb, bb = _favourite(mb)
    own_a, own_b, ref_a = sums(fa, wa, ba), sums(fb, wb, bb), sums(fa, wa, bb)
    rng = np.random.default_rng(seed)
    weights = np.stack([np.bincount(rng.integers(0, len(events), len(events)), minlength=len(events)) for _ in range(n)]).astype(float)
    weights = np.vstack([np.ones(len(events)), weights])  # row 0: the data itself

    def ece(t):
        cnt, sf, sw = (weights @ x for x in t)
        return np.abs(sf - sw).sum(axis=1) / cnt.sum(axis=1)

    def top_gap(t):
        # Undefined (NaN), not perfect, in a resample with no 0.9+ match.
        cnt, sf, sw = (weights @ x for x in t)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(cnt[:, -1] > 0, np.abs(sf[:, -1] - sw[:, -1]) / cnt[:, -1], np.nan)

    out = {}
    for name, diff in (("ece", ece(own_a) - ece(own_b)), ("gap_0.9", top_gap(own_a) - top_gap(own_b)), ("ece_in_b_bins", ece(ref_a) - ece(own_b))):
        out[name] = {"n": len(keys), "mean_diff": float(diff[0]), "lo90": float(np.nanquantile(diff[1:], 0.05)), "hi90": float(np.nanquantile(diff[1:], 0.95))}
    return out


def compare(a: list[dict], b: list[dict], keep) -> dict:
    # a minus b, paired on the same matches; negative = a better for every loss.
    sa, sb = [m for m in a if keep(m)], [m for m in b if keep(m)]
    la, lb = per_match(sa), per_match(sb)
    out = {metric: paired_cluster_bootstrap(la[metric], lb[metric], n=2000, seed=7) for metric in la}
    out["same_matches"] = set(la["brier"]) == set(lb["brier"]) and set(la["abs_error"]) == set(lb["abs_error"])
    out["calibration"] = calibration_compare(sa, sb)
    return out


def _fmt(d: dict) -> str:
    if not d or not d.get("n"):
        return "n/a"
    return f"{d['mean_diff']:+.4f} [{d['lo90']:+.4f}, {d['hi90']:+.4f}]"


def step_report(seasons: list[int], pairs: list[tuple[str, str]]) -> dict:
    report: dict = {"seasons": seasons, "summaries": {}, "comparisons": {}, "gates": {}}
    names = sorted({n for pair in pairs for n in pair})
    runs = {(n, y): load_run(n, y) for n in names for y in seasons}
    for n in names:
        for y in seasons:
            if runs[(n, y)] is not None:
                report["summaries"][f"{n} {y}"] = {s: summary([m for m in runs[(n, y)] if keep(m)]) for s, keep in SLICES.items()}
        if all(runs[(n, y)] is not None for y in seasons):
            pooled = [m for y in seasons for m in runs[(n, y)]]
            report["summaries"][f"{n} pooled"] = {s: summary([m for m in pooled if keep(m)]) for s, keep in SLICES.items()}
            # Each season's own top decile, so a high-scoring game doesn't own the pooled one.
            report["summaries"][f"{n} pooled"]["all"]["top_decile_bias_pct"] = round(float(np.mean([report["summaries"][f"{n} {y}"]["all"]["top_decile_bias_pct"] for y in seasons])), 2)
    for a, b in pairs:
        key = f"{a} vs {b}"
        report["comparisons"][key] = {}
        for y in [*seasons, "pooled"]:
            ra = [m for yy in seasons for m in (runs[(a, yy)] or [])] if y == "pooled" else runs[(a, y)]
            rb = [m for yy in seasons for m in (runs[(b, yy)] or [])] if y == "pooled" else runs[(b, y)]
            if ra and rb:
                report["comparisons"][key][str(y)] = {s: compare(ra, rb, keep) for s, keep in SLICES.items()}
        per_season = [report["comparisons"][key][str(y)]["all"]["brier"]["mean_diff"] for y in seasons if str(y) in report["comparisons"][key]]
        if per_season:
            report["comparisons"][key]["equal_season_brier"] = round(float(np.mean(per_season)), 5)
        report["gates"][key] = gates(a, b, report, seasons)
    print_report(report)
    return report


def gates(a: str, b: str, report: dict, seasons: list[int]) -> dict:
    # The plan's gates, read off the numbers (verdicts and caveats are written up by hand).
    c = report["comparisons"][f"{a} vs {b}"]
    if "pooled" not in c:
        return {}
    pooled = c["pooled"]
    out: dict = {}
    if b == "hindsight":
        out["E1 within 0.002 each season"] = all(c[str(y)]["all"]["brier"]["mean_diff"] <= 0.002 for y in seasons)
        out["E1 within 0.004 weeks 1-2 each season"] = all(c[str(y)]["weeks 1-2"]["brier"]["mean_diff"] <= 0.004 for y in seasons)
    if b == "e2_brier":
        out["E2 loglik better (interval)"] = pooled["all"]["neg_loglik"]["hi90"] < 0
        out["E2 MAE better (interval)"] = pooled["all"]["abs_error"]["hi90"] < 0
        out["E2 Brier not worse by >0.0005 (point)"] = pooled["all"]["brier"]["mean_diff"] <= 0.0005
        out["E2 Brier not worse by >0.0005 (whole interval)"] = pooled["all"]["brier"]["hi90"] <= 0.0005
    if a.startswith("e3_") and b == "e2_map":
        sa, sb = report["summaries"].get(f"{a} pooled", {}).get("all", {}), report["summaries"].get(f"{b} pooled", {}).get("all", {})
        out["E3 top-decile bias halved"] = abs(sa.get("top_decile_bias_pct", 0)) <= 0.5 * abs(sb.get("top_decile_bias_pct", 0))
        out["E3 pooled Brier >= 0.001 better"] = pooled["all"]["brier"]["mean_diff"] <= -0.001
        out["calibration 0.9+ gap halved every season"] = all(
            report["summaries"][f"{a} {y}"]["all"].get("gap_0.9", 1) <= 0.5 * report["summaries"][f"{b} {y}"]["all"].get("gap_0.9", 0) for y in seasons
        )
        out["calibration ECE improvement interval below 0"] = pooled["all"]["calibration"]["ece"]["hi90"] < 0
        out["Brier not worse (point)"] = pooled["all"]["brier"]["mean_diff"] <= 0
        out["add-term bar: pooled <= -0.0015, interval < 0, no season worse > 0.0005"] = (
            pooled["all"]["brier"]["mean_diff"] <= -0.0015 and pooled["all"]["brier"]["hi90"] < 0
            and all(c[str(y)]["all"]["brier"]["mean_diff"] <= 0.0005 for y in seasons)
        )
    return out


def print_report(report: dict) -> None:
    print("\nSummaries (loglik and MAE in points, ties included; Brier etc. on decided matches; top% = sum(actual - predicted) / sum(predicted) in the season's top decile of predicted alliance score; ECE and gaps: favourite's chance by fixed bins)")
    print(f"{'run':26} {'slice':30} {'n':>6} {'brier':>7} {'logloss':>8} {'acc':>6} {'ll/obs':>7} {'MAE':>7} {'cov80':>6} {'top%':>6} {'topcov':>6} {'ECE':>6} {'gap.8':>6} {'gap.9':>6} {'n.9':>6}")
    for run, slices in report["summaries"].items():
        for s, v in slices.items():
            if v.get("n"):
                print(f"{run:26} {s:30} {v['n']:>6} {v['brier']:>7.4f} {v['log_loss']:>8.4f} {v['accuracy']:>6.3f} {v['loglik_per_obs']:>7.3f} {v['points_mae']:>7.2f} {v['coverage_80']:>6.3f} {v['top_decile_bias_pct']:>6.2f} {v['top_decile_coverage_80']:>6.3f} {v['ece']:>6.4f} {str(v.get('gap_0.8')):>6} {str(v.get('gap_0.9')):>6} {str(v.get('n_0.9')):>6}")
    print("\nPaired differences, a - b (negative = a better), 90% event-cluster bootstrap (conditional on the fitted predictions)")
    for key, by_season in report["comparisons"].items():
        print(f"\n{key}   equal-season mean Brier diff {by_season.get('equal_season_brier')}")
        for y, slices in by_season.items():
            if not isinstance(slices, dict):
                continue
            for s, c in slices.items():
                cal = c.get("calibration") or {}
                flag = "" if c["same_matches"] else "  MATCH SETS DIFFER"
                print(f"  {y:7} {s:30} Brier {_fmt(c['brier']):28} -loglik {_fmt(c['neg_loglik']):28} MAE {_fmt(c['abs_error']):28} ECE {_fmt(cal.get('ece', {}))}{flag}")
        for gate, ok in report["gates"].get(key, {}).items():
            print(f"  gate: {gate}: {'pass' if ok else 'FAIL'}")


# ── Blind seasons: once per frozen bundle ────────────────────────────────

def manifest() -> dict:
    digest = hashlib.sha256()
    for year in (*DEV, *BLIND):
        for path in sorted((DATA / str(year)).glob("*.json")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())

    def git(*args):
        return subprocess.run(["git", *args], cwd=BACKEND_ROOT, capture_output=True, text=True).stdout.strip()

    return {
        "commit": git("rev-parse", "HEAD"),
        "uncommitted_changes": bool(git("status", "--porcelain", "--", ".")),
        "data_sha256": digest.hexdigest(),
        "variants": {v: VARIANTS[v] for v in BLIND_VARIANTS},
        "pairs": BLIND_PAIRS,
        "dev_hindsight": {y: hindsight_fits().get(str(y)) for y in DEV},
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def step_blind(force: bool) -> None:
    # One run per committed bundle: its outputs go under blind/<commit> and a second run
    # of the same commit is refused. A new bundle (new commit) may run with --force, which
    # keeps the earlier ones; say so whenever it's used.
    record = manifest()
    if record["uncommitted_changes"]:
        raise SystemExit("commit first: the manifest must name the code that ran")
    folder = REPORTS / "blind" / record["commit"]
    if folder.exists():
        raise SystemExit(f"{folder} exists: this bundle has had its blind run.")
    earlier = sorted(p.name for p in (REPORTS / "blind").glob("*")) if (REPORTS / "blind").exists() else []
    if earlier and not force:
        raise SystemExit(f"blind runs already exist for {earlier}; pass --force for a new bundle and report it as such")
    save(f"blind/{record['commit']}/manifest", {**record, "earlier_bundles": earlier, "status": "started"})
    # Hindsight first: 2026's MAP centre includes 2025's fit.
    fits = step_hindsight(list(BLIND))
    step_run(list(BLIND), list(BLIND_VARIANTS))
    save(f"blind/{record['commit']}/report", step_report(list(BLIND), list(BLIND_PAIRS)))
    outputs = {}
    for path in sorted([REPORTS / "hindsight.json", *(REPORTS / "runs").glob("*-2025.json"), *(REPORTS / "runs").glob("*-2026.json")]):
        outputs[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    save(f"blind/{record['commit']}/manifest", {
        **record, "earlier_bundles": earlier, "status": "finished", "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "blind_hindsight": {y: fits.get(str(y)) for y in BLIND}, "output_sha256": outputs,
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, choices=["diagnose", "hindsight", "run", "report", "blind"])
    parser.add_argument("--fitted", action="store_true", help="diagnose: use the hindsight likelihood fit, not the stand-in")
    parser.add_argument("--seasons", default=",".join(map(str, DEV)))
    parser.add_argument("--variants", default="", help="run: comma-separated VARIANTS names")
    parser.add_argument("--pairs", default="", help="report: a:b,a:b")
    parser.add_argument("--label", default="report")
    parser.add_argument("--force", action="store_true", help="blind: run again for a new bundle")
    args = parser.parse_args()
    seasons = [int(s) for s in args.seasons.split(",")]
    if args.step in ("hindsight", "run") and set(seasons) & set(BLIND):
        raise SystemExit("2025/2026 are blind: use --step blind")
    if args.step == "diagnose":
        path = save(f"diagnose{'-fitted' if args.fitted else ''}-{'-'.join(map(str, seasons))}", step_diagnose(seasons, args.fitted))
        print(f"\nreport: {path}", file=sys.stderr)
    if args.step == "hindsight":
        print(f"report: {save('hindsight', step_hindsight(seasons))}", file=sys.stderr)
    if args.step == "run":
        step_run(seasons, [v for v in args.variants.split(",") if v])
    if args.step == "report":
        pairs = [tuple(p.split(":")) for p in args.pairs.split(",") if p]
        print(f"\nreport: {save(args.label, step_report(seasons, pairs))}", file=sys.stderr)
    if args.step == "blind":
        step_blind(args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
