#!/usr/bin/env python3
# Rating plan phase 3 on cached TBA seasons (no database): the mean bias of predicted
# alliance scores (an as-of calibration per context), the win layer (E4: Phi(z) and a
# temperature per context, a scale-free logistic correction or the 16-unit network,
# starting weights from earlier seasons) and stacking (E5: EPA-clone, margin-Elo and
# ridge-cOPR logits as inputs). The state model is phase 2's frozen bundle, replayed from
# its saved fits (no refit). 2022-2024 are for development; 2025-2026 run once with the
# phase 3 bundle frozen (--step blind), which also reproduces production's 2026 network.
#
#   PYTHONPATH=. python scripts/evaluate_rating_phase3.py --step tables
#   PYTHONPATH=. python scripts/evaluate_rating_phase3.py --step dev
from __future__ import annotations

# ruff: noqa: E402

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
    sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np

import evaluate_rating_phase2 as p2
from app.services.ratings.research_eval import Z80, fit_scale, paired_cluster_bootstrap
from app.services.ratings.research_fit import scale_free_z
from app.services.ratings.research_models import EpaClone, MarginElo, RidgePriorOpr, mean_share, start_multipliers
from app.services.ratings.research_win import (
    CONTEXTS,
    FEATURES,
    ScoreCalibration,
    WinRecipe,
    bucket_probs,
    bundle_table,
    fit_score_calibration,
    logit_phi,
    side_arrays,
    side_probs,
    walk_forward_scores,
    walk_forward_win,
    zscore,
)

REPORTS = BACKEND_ROOT.parents[1] / "research-data" / "reports" / "phase3"
DEV = p2.DEV
BLIND = p2.BLIND

SCORE_VARIANTS = ("score_none", "score_cal", "score_cal_past", "score_terms", "score_terms_past")
SCORE_RECIPES = {"score_cal": (False, False), "score_cal_past": (False, True), "score_terms": (True, False), "score_terms_past": (True, True)}  # (terms, past)
WIN_VARIANTS: dict[str, WinRecipe] = {
    "temp": WinRecipe(),
    "temp_logistic": WinRecipe(base="logistic"),
    "temp_past": WinRecipe(past=True),
    "logit": WinRecipe(correction="logistic"),
    "logit_past": WinRecipe(correction="logistic", past=True),
    "net": WinRecipe(correction="network"),
    "net_past": WinRecipe(correction="network", past=True),
    "stack": WinRecipe(correction="logistic", past=True, stack=("epa", "elo")),
    "stack_ridge": WinRecipe(correction="logistic", past=True, stack=("epa", "elo", "ridge")),
    "stack_nopast": WinRecipe(correction="logistic", stack=("epa", "elo")),
    # The same layers on margins from the calibrated scores (score_terms_past).
    "temp_logistic_cal": WinRecipe(base="logistic"),
    "logit_past_cal": WinRecipe(base="logistic", correction="logistic", past=True),
    "logit_past_logistic": WinRecipe(base="logistic", correction="logistic", past=True),
    # The blind set: everything on the logistic base (it beat Phi(z) on log loss in dev).
    "logit_logistic": WinRecipe(base="logistic", correction="logistic"),
    "net_past_logistic": WinRecipe(base="logistic", correction="network", past=True),
    "stack_logistic": WinRecipe(base="logistic", correction="logistic", past=True, stack=("epa", "elo")),
}
ON_CALIBRATED = ("temp_logistic_cal", "logit_past_cal")
FIXED = ("bucket", "phi")  # the saved harness logistic (today), and Phi(z) with nothing fitted
WIN_PAIRS = (
    ("temp", "bucket"), ("temp_logistic", "bucket"), ("temp", "temp_logistic"), ("phi", "bucket"),
    ("temp_past", "temp"), ("logit", "temp"), ("logit_past", "temp"), ("logit_past", "logit"),
    ("net", "logit"), ("net_past", "logit_past"), ("net", "temp"),
    ("stack", "logit_past"), ("stack_nopast", "logit"), ("stack_ridge", "stack"),
    ("logit_past_logistic", "logit_past"), ("temp_logistic_cal", "temp_logistic"), ("logit_past_cal", "logit_past_logistic"),
)
SCORE_PAIRS = (
    ("score_cal", "score_none"), ("score_cal_past", "score_none"), ("score_terms", "score_none"), ("score_terms_past", "score_none"),
    ("score_terms", "score_cal"), ("score_terms_past", "score_terms"),
)

# Frozen before any 2025/2026 run, decided on 2022-2024 only (notes/HISTORY.md,
# 2026-11-13). Scores: the as-of calibration with the alliance make-up terms, started from
# earlier seasons. Win: logistic on margin / spread, a temperature per context and the
# scale-free logistic correction, both started from earlier seasons. The network tied the
# logistic and stacking missed the add bar in dev: they run as comparators only.
PHASE3_BUNDLE = {"score": "score_terms_past", "win": "logit_past_logistic"}
# The earlier-seasons chain for the blind run starts at 2023: 2022 has no earlier season,
# so its in-season predictions start from a stand-in built from 2026's mean score.
BLIND_CHAIN = (2023, 2024, *BLIND)
BLIND_SCORE = ("score_none", "score_cal_past", "score_terms_past")
BLIND_SCORE_PAIRS = (("score_terms_past", "score_none"), ("score_cal_past", "score_none"), ("score_terms_past", "score_cal_past"))
BLIND_FIXED = ("bucket",)
BLIND_WIN = ("temp_logistic", "logit_logistic", "logit_past_logistic", "net_past_logistic", "stack_logistic")
BLIND_PAIRS = (
    ("temp_logistic", "bucket"), ("logit_past_logistic", "temp_logistic"), ("logit_past_logistic", "logit_logistic"),
    ("net_past_logistic", "logit_past_logistic"), ("stack_logistic", "logit_past_logistic"), ("logit_past_logistic", "bucket"),
)


# ── Tables (per-match features, replayed from the phase 2 bundle) ────────

def season_inputs_static(year: int):
    # Exactly the phase 2 bundle's inputs: last season's average strength (its own fit,
    # drift off) as the start.
    inputs = p2.inputs_for(year)
    prev = p2.research(year - 1)
    if prev is not None:
        fit = p2.params_from(p2.hindsight_fits()[str(year - 1)]["mle"])
        inputs = replace(inputs, z_previous=scale_free_z(prev.season.results, replace(fit, match=1e-6, event=1e-6)))
    return inputs


def build_table(year: int, side_models: tuple[str, ...] = ("epa", "elo", "ridge")) -> dict[str, np.ndarray]:
    started = time.perf_counter()
    saved = json.loads((p2.REPORTS / "runs" / f"{p2.BUNDLE}-{year}.json").read_text())
    plan = [(p["from_time"], p2.params_from(p["params"])) for p in saved["plan"]]
    inputs = season_inputs_static(year)
    prev = p2.research(year - 1)
    previous_unit = mean_share(prev.season.results) if prev else None
    multipliers = start_multipliers(inputs.z_previous, inputs.rookies)
    makers = {"epa": EpaClone, "elo": MarginElo, "ridge": RidgePriorOpr}
    side = {name: side_probs(inputs, makers[name](multipliers, previous_unit)) for name in side_models}
    table = bundle_table(inputs, plan, side)
    # The replay must give the saved bundle's predictions (to the saved settings' rounding).
    saved_rm = np.array([m[9] for m in saved["matches"]])
    assert [m[0] for m in saved["matches"]] == table["key"].tolist()
    drift = float(np.max(np.abs(table["rm"] - saved_rm) / np.maximum(saved_rm, 1.0)))
    assert drift < 1e-3, f"{year}: replay drifted from the saved bundle by {drift}"
    print(f"{year}: table {len(table['key'])} rows, replay drift {drift:.1e}, {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)
    return table


def table_path(year: int) -> Path:
    return REPORTS / "tables" / f"{year}.npz"


def save_table(year: int, table: dict[str, np.ndarray]) -> None:
    table_path(year).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(table_path(year), **table)


def load_table(year: int) -> dict[str, np.ndarray]:
    with np.load(table_path(year), allow_pickle=False) as data:
        return {k: data[k] for k in data.files}


# ── Running the variants ─────────────────────────────────────────────────

def score_centre(fits: list[ScoreCalibration]) -> ScoreCalibration | None:
    if not fits:
        return None
    return ScoreCalibration(np.mean([f.coef for f in fits], axis=0), fits[0].terms)


def run_seasons(
    seasons: list[int], tables: dict[int, dict], win_names: tuple[str, ...] = tuple(WIN_VARIANTS),
    score_names: tuple[str, ...] = SCORE_VARIANTS, fixed: tuple[str, ...] = FIXED,
) -> dict:
    # In season order, so each season's "past" variants start from the seasons before it.
    # Only the named variants are computed.
    out: dict = {"score": {}, "win": {}, "score_fits": {}, "win_fits": {}}
    score_history: dict[bool, list[ScoreCalibration]] = {False: [], True: []}
    win_history: dict[str, list] = {name: [] for name in win_names}
    for year in sorted(seasons):
        t = tables[year]
        started = time.perf_counter()
        none = (t["rm"].copy(), t["bm"].copy())
        out["score"][year] = {"score_none": none}
        out["score_fits"][year] = {}
        for name in score_names[1:]:
            terms, past = SCORE_RECIPES[name]
            red, blue, fits = walk_forward_scores(t, score_centre(score_history[terms]) if past else None, terms=terms)
            out["score"][year][name] = (red, blue)
            out["score_fits"][year][name] = fits
        for terms in (False, True):
            score_history[terms].append(fit_score_calibration(side_arrays(t), terms=terms))
        probs = {"bucket": t["p_bucket"]}
        if "phi" in fixed:
            probs["phi"] = 1.0 / (1.0 + np.exp(-logit_phi(zscore(t))))
        out["win_fits"][year] = {}
        calibrated = {**t, "rm": out["score"][year]["score_terms_past"][0], "bm": out["score"][year]["score_terms_past"][1]} if any(n in ON_CALIBRATED for n in win_names) else t
        for name in win_names:
            p, fits, season_fit = walk_forward_win(calibrated if name in ON_CALIBRATED else t, WIN_VARIANTS[name], win_history[name])
            probs[name] = p
            out["win_fits"][year][name] = fits
            win_history[name].append(season_fit)
        out["win"][year] = probs
        print(f"{year}: variants in {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)
    return out


# ── Metrics ──────────────────────────────────────────────────────────────

FAV_BINS = p2.FAV_BINS
WIN_SLICES = {
    "all": lambda t: np.ones(len(t["y"]), bool),
    "FRC week 1": lambda t: t["week"] == 0,
    "FRC week 2": lambda t: t["week"] == 1,
    "FRC week 3": lambda t: t["week"] == 2,
    "FRC weeks 1-3": lambda t: t["week"] <= 2,
    "FRC week 4": lambda t: t["week"] == 3,
    "FRC weeks 4-7": lambda t: (t["week"] >= 3) & (t["week"] <= 6),
    "FRC week 8+": lambda t: t["week"] >= 7,
    "DCMP + Championship": lambda t: np.isin(t["event_type"], (2, 3, 4, 5)),
    "quals": lambda t: t["playoff"] == 0,
    "playoffs": lambda t: t["playoff"] > 0,
    "someone's first match here": lambda t: t["min_here"] <= 0,
    "nobody has played here yet": lambda t: t["max_here"] <= 0,
    "first third of an event": lambda t: t["third"] == 0,
    "newest 20% of events": lambda t: t["holdout"],
}


def _matches(t, p, mask) -> list[dict]:
    # The phase 2 report's per-match shape, so its calibration bootstrap applies as is.
    idx = np.flatnonzero(mask)
    return [{"match_key": str(t["key"][i]), "event_key": str(t["event"][i]), "y": float(t["y"][i]), "p": float(p[i])} for i in idx]


def win_summary(t, p, mask) -> dict:
    m = mask & (t["y"] != 0.5)
    if not m.any():
        return {"n": 0}
    q = np.clip(p[m], 1e-6, 1 - 1e-6)
    y = t["y"][m]
    lg = np.log(q / (1 - q))
    return {
        "n": int(m.sum()),
        "brier": round(float(np.mean((q - y) ** 2)), 5),
        "log_loss": round(float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))), 5),
        "accuracy": round(float(np.mean((q > 0.5) == (y > 0.5))), 4),
        "calibration_slope": round(float(fit_scale(lg, y)), 3) if m.sum() >= 30 else None,
        **calibration_raw(q, y),
    }


def calibration_raw(p: np.ndarray, y: np.ndarray) -> dict:
    # p2.calibration (favourite's chance in fixed bins) at full precision, for the gates.
    fav = np.maximum(p, 1 - p)
    won = np.where(p >= 0.5, y, 1 - y)
    which = np.clip(np.searchsorted(FAV_BINS, fav, side="right") - 1, 0, len(FAV_BINS) - 2)
    out = {"ece": 0.0}
    for b in range(len(FAV_BINS) - 1):
        m = which == b
        if m.any():
            gap = abs(float(fav[m].mean() - won[m].mean()))
            out["ece"] += gap * m.sum() / len(fav)
            if b >= 3:
                out[f"gap_{FAV_BINS[b]:.1f}"] = gap
                out[f"n_{FAV_BINS[b]:.1f}"] = int(m.sum())
    return out


def _losses(t, p, mask) -> dict[str, dict]:
    m = mask & (t["y"] != 0.5)
    q = np.clip(p, 1e-6, 1 - 1e-6)
    y = t["y"]
    brier = (q - y) ** 2
    ll = -(y * np.log(q) + (1 - y) * np.log(1 - q))
    keys, events = t["key"], t["event"]
    return {
        "brier": {str(keys[i]): (str(events[i]), float(brier[i])) for i in np.flatnonzero(m)},
        "log_loss": {str(keys[i]): (str(events[i]), float(ll[i])) for i in np.flatnonzero(m)},
    }


def win_compare(ts: list[dict], pa: list[np.ndarray], pb: list[np.ndarray], slice_fn) -> dict:
    la: dict = {"brier": {}, "log_loss": {}}
    lb: dict = {"brier": {}, "log_loss": {}}
    ma: list = []
    mb: list = []
    for t, a, b in zip(ts, pa, pb):
        mask = slice_fn(t)
        for k, v in _losses(t, a, mask).items():
            la[k].update(v)
        for k, v in _losses(t, b, mask).items():
            lb[k].update(v)
        ma += _matches(t, a, mask & (t["y"] != 0.5))
        mb += _matches(t, b, mask & (t["y"] != 0.5))
    out = {k: paired_cluster_bootstrap(la[k], lb[k], n=2000, seed=7) for k in la}
    out["calibration"] = p2.calibration_compare(ma, mb) if ma else {}
    return out


def score_summary(t, red, blue, mask) -> dict:
    # decile_bias_pct: deciles of this variant's own predictions (what a reader of the
    # prediction sees); decile_bias_fixed_pct: deciles of the uncalibrated predictions, so
    # every variant is judged on the same alliances.
    rows = np.flatnonzero(mask)
    sides = side_arrays(t, rows)
    if not len(sides["actual"]):
        return {"n": 0}
    mu = np.concatenate([red[rows], blue[rows]])
    base = np.concatenate([t["rm"][rows], t["bm"][rows]])
    a = np.concatenate([t["ra"][rows], t["ba"][rows]])
    v = np.concatenate([t["rv"][rows], t["bv"][rows]])
    ok = np.isfinite(a) & (v > 0)
    mu, base, a, v = mu[ok], base[ok], a[ok], v[ok]
    nu = a - mu
    return {
        "sides": int(ok.sum()),
        "points_mae": round(float(np.mean(np.abs(nu))), 3),
        "loglik_per_obs": round(float(np.mean(-0.5 * (np.log(2 * math.pi * v) + nu * nu / v))), 4),
        "coverage_80": round(float(np.mean(np.abs(nu) <= Z80 * np.sqrt(v))), 4),
        "mean_bias_pct": round(float(100 * nu.sum() / mu.sum()), 2),
        "decile_bias_pct": decile_bias(mu, a),
        "decile_bias_fixed_pct": decile_bias(mu, a, by=base),
    }


def decile_bias(mu: np.ndarray, actual: np.ndarray, deciles: int = 10, by: np.ndarray | None = None) -> list[float]:
    # sum(actual - predicted) / sum(predicted) per decile of `by` (default: mu itself).
    by = mu if by is None else by
    edges = np.quantile(by, np.linspace(0, 1, deciles + 1))
    which = np.clip(np.searchsorted(edges, by, side="right") - 1, 0, deciles - 1)
    return [float(100 * (actual[which == d] - mu[which == d]).sum() / max(mu[which == d].sum(), 1e-9)) for d in range(deciles)]


SCORE_SLICES = {
    "all": lambda t: np.ones(len(t["y"]), bool),
    "quals": lambda t: t["playoff"] == 0,
    "playoffs": lambda t: t["playoff"] > 0,
    "someone's first match here": lambda t: (t["min_here"] <= 0) & (t["playoff"] == 0),
    "DCMP + Championship": lambda t: np.isin(t["event_type"], (2, 3, 4, 5)),
    "Championship": lambda t: np.isin(t["event_type"], (3, 4)),
    "FRC weeks 1-2": lambda t: t["week"] <= 1,
}


def score_losses(t, red, blue, mask) -> dict[str, dict]:
    out: dict = {"abs_error": {}, "neg_loglik": {}}
    for i in np.flatnonzero(mask):
        sides = [(m, v, a) for m, v, a in ((red[i], t["rv"][i], t["ra"][i]), (blue[i], t["bv"][i], t["ba"][i])) if np.isfinite(a) and v > 0]
        if sides:
            key, event = str(t["key"][i]), str(t["event"][i])
            out["abs_error"][key] = (event, sum(abs(a - m) for m, _v, a in sides) / len(sides))
            out["neg_loglik"][key] = (event, sum(0.5 * (math.log(2 * math.pi * v) + (a - m) ** 2 / v) for m, v, a in sides) / len(sides))
    return out


def score_compare(ts, pa, pb, slice_fn) -> dict:
    la: dict = {"abs_error": {}, "neg_loglik": {}}
    lb: dict = {"abs_error": {}, "neg_loglik": {}}
    for t, a, b in zip(ts, pa, pb):
        mask = slice_fn(t)
        for k, v in score_losses(t, *a, mask).items():
            la[k].update(v)
        for k, v in score_losses(t, *b, mask).items():
            lb[k].update(v)
    return {k: paired_cluster_bootstrap(la[k], lb[k], n=2000, seed=7) for k in la}


def report(
    seasons: list[int], tables: dict[int, dict], runs: dict, win_pairs, score_pairs,
    win_names: tuple[str, ...] = tuple(WIN_VARIANTS), score_names: tuple[str, ...] = SCORE_VARIANTS, fixed: tuple[str, ...] = FIXED,
) -> dict:
    out: dict = {"seasons": seasons, "score": {"summaries": {}, "comparisons": {}, "gates": {}}, "win": {"summaries": {}, "comparisons": {}, "gates": {}}}
    # Score calibration.
    via_bucket: dict = {}
    for name in score_names:
        for y in seasons:
            red, blue = runs["score"][y][name]
            out["score"]["summaries"][f"{name} {y}"] = {s: score_summary(tables[y], red, blue, f(tables[y])) for s, f in SCORE_SLICES.items()}
            # The win probability these means give through the harness's bucket logistic.
            t = tables[y]
            p = bucket_probs(t, (red - blue) / np.sqrt(t["mv"]))
            decided = t["y"] != 0.5
            via_bucket[(name, y)] = (p[decided] - t["y"][decided]) ** 2
            out["score"]["summaries"][f"{name} {y}"]["all"]["brier_via_bucket_logistic"] = round(float(via_bucket[(name, y)].mean()), 5)
    for a, b in score_pairs:
        key = f"{a} vs {b}"
        out["score"]["comparisons"][key] = {}
        for y in [*seasons, "pooled"]:
            ys = seasons if y == "pooled" else [y]
            out["score"]["comparisons"][key][str(y)] = {
                s: score_compare([tables[yy] for yy in ys], [runs["score"][yy][a] for yy in ys], [runs["score"][yy][b] for yy in ys], f) for s, f in SCORE_SLICES.items()
            }
        pooled_diff = float(np.concatenate([via_bucket[(a, y)] - via_bucket[(b, y)] for y in seasons]).mean())
        out["score"].setdefault("brier_via_bucket_logistic_pooled_diff", {})[key] = pooled_diff
        out["score"]["gates"][key] = score_gates(out["score"], a, b, seasons, pooled_diff)
    # Win layer.
    names = [*fixed, *win_names]
    for name in names:
        for y in seasons:
            out["win"]["summaries"][f"{name} {y}"] = {s: win_summary(tables[y], runs["win"][y][name], f(tables[y])) for s, f in WIN_SLICES.items()}
        # Pooled over the seasons, match by match.
        out["win"]["summaries"][f"{name} pooled"] = {"all": _pooled_summary([tables[y] for y in seasons], [runs["win"][y][name] for y in seasons])}
    for a, b in win_pairs:
        key = f"{a} vs {b}"
        out["win"]["comparisons"][key] = {}
        for y in [*seasons, "pooled"]:
            ys = seasons if y == "pooled" else [y]
            out["win"]["comparisons"][key][str(y)] = {
                s: win_compare([tables[yy] for yy in ys], [runs["win"][yy][a] for yy in ys], [runs["win"][yy][b] for yy in ys], f) for s, f in WIN_SLICES.items()
            }
        out["win"]["gates"][key] = win_gates(out["win"], a, b, seasons)
    out["win"]["fits"] = {str(y): {n: f[-1] for n, f in runs["win_fits"][y].items()} for y in seasons}
    out["win"]["features"] = FEATURES
    out["score"]["fits"] = {str(y): {n: f[-1] for n, f in runs["score_fits"][y].items()} for y in seasons}
    out["score"]["contexts"] = CONTEXTS
    return out


def _pooled_summary(ts: list[dict], ps: list[np.ndarray]) -> dict:
    keys = ("key", "event", "y")
    t = {k: np.concatenate([x[k] for x in ts]) for k in keys}
    return win_summary(t, np.concatenate(ps), np.ones(len(t["y"]), bool))


def score_gates(rep: dict, a: str, b: str, seasons: list[int], brier_diff: float) -> dict:
    c = rep["comparisons"][f"{a} vs {b}"]

    def halved(slice_name, column):
        return all(
            abs(rep["summaries"][f"{a} {y}"][slice_name][column][-1]) <= 0.5 * abs(rep["summaries"][f"{b} {y}"][slice_name][column][-1])
            for y in seasons
        )

    return {
        "top-decile bias halved every season (own deciles)": halved("all", "decile_bias_pct"),
        "top-decile bias halved every season (fixed deciles)": halved("all", "decile_bias_fixed_pct"),
        "top-decile bias halved at DCMP + Championship every season (own deciles)": halved("DCMP + Championship", "decile_bias_pct"),
        "top-decile bias halved at DCMP + Championship every season (fixed deciles)": halved("DCMP + Championship", "decile_bias_fixed_pct"),
        "points MAE better (pooled interval < 0)": c["pooled"]["all"]["abs_error"]["hi90"] < 0,
        "score loglik better (pooled interval < 0)": c["pooled"]["all"]["neg_loglik"]["hi90"] < 0,
        "no season's MAE worse (point)": all(c[str(y)]["all"]["abs_error"]["mean_diff"] <= 0 for y in seasons),
        "Brier via the bucket logistic not worse (pooled, match level)": brier_diff <= 0.0,
    }


def win_gates(rep: dict, a: str, b: str, seasons: list[int]) -> dict:
    c = rep["comparisons"][f"{a} vs {b}"]
    pooled = c["pooled"]

    def add(slice_name):
        return (
            pooled[slice_name]["brier"]["mean_diff"] <= -0.0015 and pooled[slice_name]["brier"]["hi90"] < 0
            and all(c[str(y)][slice_name]["brier"]["mean_diff"] <= 0.0005 for y in seasons)
        )

    sa, sb = rep["summaries"][f"{a} pooled"]["all"], rep["summaries"][f"{b} pooled"]["all"]
    cal = pooled["all"]["calibration"]
    return {
        "add rule (all)": add("all"),
        "add rule (FRC weeks 1-3)": add("FRC weeks 1-3"),
        "add rule (someone's first match here)": add("someone's first match here"),
        "add rule (nobody has played here yet)": add("nobody has played here yet"),
        "add rule (first third of an event)": add("first third of an event"),
        "simplify rule: pooled Brier not worse by > 0.0005": pooled["all"]["brier"]["mean_diff"] <= 0.0005,
        # Calibration-only rule, in the bins it is meant to fix (0.9+ favourites) and overall.
        "calibration rule: 0.9+ gap halved, interval < 0, Brier not worse": bool(
            sa.get("gap_0.9", 1) <= 0.5 * sb.get("gap_0.9", 0) and cal.get("gap_0.9", {}).get("hi90", 1) < 0 and pooled["all"]["brier"]["mean_diff"] <= 0
        ),
        "calibration rule: ECE halved, interval < 0, Brier not worse": bool(
            sa["ece"] <= 0.5 * sb["ece"] and cal.get("ece", {}).get("hi90", 1) < 0 and pooled["all"]["brier"]["mean_diff"] <= 0
        ),
    }


def _fmt(d: dict) -> str:
    if not d or not d.get("n"):
        return "n/a"
    return f"{d['mean_diff']:+.4f} [{d['lo90']:+.4f}, {d['hi90']:+.4f}]"


def print_report(rep: dict, seasons: list[int]) -> None:
    s = rep["score"]
    print("\nScore calibration: bias by decile of each variant's own predicted alliance score (% of predicted; + = under-predicted)")
    for key, v in s["summaries"].items():
        for sl in ("all", "DCMP + Championship", "playoffs", "someone's first match here"):
            x = v[sl]
            if x.get("sides"):
                extra = f"  Brier(bucket) {v['all']['brier_via_bucket_logistic']:.4f}" if sl == "all" else ""
                print(f"  {key:22} {sl:22} MAE {x['points_mae']:7.2f} ll {x['loglik_per_obs']:7.3f} cov80 {x['coverage_80']:.3f} mean {x['mean_bias_pct']:+5.1f}  deciles {' '.join(f'{d:+5.1f}' for d in x['decile_bias_pct'])}{extra}")
    for key, by in s["comparisons"].items():
        print(f"\n  {key}")
        for y, sl in by.items():
            for name in ("all", "DCMP + Championship", "playoffs", "someone's first match here"):
                print(f"    {y:7} {name:22} MAE {_fmt(sl[name]['abs_error']):28} -loglik {_fmt(sl[name]['neg_loglik'])}")
        print(f"    Brier via the bucket logistic, pooled match-level diff {s['brier_via_bucket_logistic_pooled_diff'][key]:+.5f}")
        for g, ok in s["gates"][key].items():
            print(f"    gate: {g}: {'pass' if ok else 'FAIL'}")
    w = rep["win"]
    print("\nWin layer summaries")
    print(f"  {'run':22} {'slice':28} {'n':>6} {'brier':>7} {'logloss':>8} {'acc':>6} {'slope':>6} {'ECE':>6} {'gap.9':>6}")
    for key, v in w["summaries"].items():
        for sl, x in v.items():
            if x.get("n") and sl in ("all", "FRC weeks 1-3", "DCMP + Championship", "someone's first match here", "nobody has played here yet", "first third of an event", "playoffs", "newest 20% of events"):
                print(f"  {key:22} {sl:28} {x['n']:>6} {x['brier']:>7.4f} {x['log_loss']:>8.4f} {x['accuracy']:>6.3f} {str(x['calibration_slope']):>6} {x['ece']:>6.4f} {x.get('gap_0.9', float('nan')):>6.4f}")
    print("\nPaired differences a - b (negative = a better), 90% event-cluster bootstrap")
    for key, by in w["comparisons"].items():
        print(f"\n  {key}")
        for y, sl in by.items():
            for name in ("all", "FRC week 1", "FRC week 2", "FRC weeks 1-3", "FRC week 4", "FRC weeks 4-7", "FRC week 8+", "DCMP + Championship", "someone's first match here", "nobody has played here yet", "first third of an event", "playoffs"):
                c = sl[name]
                cal = c.get("calibration", {}).get("ece", {}) if name == "all" else {}
                print(f"    {y:7} {name:28} Brier {_fmt(c['brier']):28} logloss {_fmt(c['log_loss']):28}" + (f" ECE {_fmt(cal)}" if cal else ""))
        for g, ok in w["gates"][key].items():
            print(f"    gate: {g}: {'pass' if ok else 'FAIL'}")


def to_json(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return str(obj)


def save(name: str, payload) -> Path:
    path = REPORTS / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=to_json))
    return path


# ── Production's 2026 network, reproduced ────────────────────────────────

PROD = BACKEND_ROOT.parents[1] / "research-data" / "prod"
TBA_ALL = BACKEND_ROOT.parents[1] / "research-data" / "tba-all"
CORRECTION_WEIGHTS = BACKEND_ROOT / "app" / "services" / "ratings" / "data" / "win_correction_2026.json"


def production_priors() -> dict[str, float]:
    # Production's own 2025 prior strengths (team_prior_strengths, read-only export).
    out = {}
    for line in (PROD / "team_prior_strengths_2025.csv").read_text().splitlines():
        parts = line.split(",")
        if len(parts) == 3 and parts[0].startswith("frc"):
            out[parts[0].lower()] = float(parts[1])
    return out


def production_2026() -> dict[str, tuple[float, float]]:
    # What users see, as closely as the research data allows: the live team model (hub +
    # tower, duds, 2026 settings) with production's stored priors, replayed over every
    # 2026 event TBA has (offseason and week-0 events too, as production's database does)
    # with disqualified alliances kept (production keeps them), then production's bucket
    # scales and the 14-input network. The network was trained on every 2026 event, so it
    # is in-sample here. Returns (base, corrected) per match.
    from app.services.events.win_probability import red_win_prob
    from app.services.ratings.constants import season_baseline
    from app.services.ratings.research_data import load_cached_season
    from app.services.ratings.team_model import SeasonTeamModel
    from app.services.ratings.team_model_service import correction_features
    from app.services.ratings.win_correction import active, corrected

    if not CORRECTION_WEIGHTS.exists() or not active(2026):
        raise SystemExit("production's 2026 correction weights are missing or switched off")
    research = load_cached_season(TBA_ALL, 2026, exclude_dq=False)
    baseline = season_baseline(2026)
    z = production_priors()
    beta = float(baseline.get("prior_beta", 0.0))
    rookie = float(baseline.get("rookie_multiplier", 1.0))
    multipliers = {team: math.exp(beta * value) for team, value in z.items()}
    for team, year in research.rookie_year.items():
        if year == 2026:
            multipliers.setdefault(team, rookie)
    model = SeasonTeamModel(
        dict(baseline["team_model_priors"]),
        baseline["team_model_params"],
        team_multipliers=multipliers,
        team_var_multipliers={team: float(baseline.get("prior_known_var_multiplier", 1.0)) for team in z},
        week_multipliers=list(baseline.get("week_multipliers") or []),
    )
    here: dict = {}
    total: dict = {}
    out = {}
    for result in research.season.results:
        card = result.card
        teams = card.red + card.blue
        prediction = model.predict(card)
        seen = min(here.get((t, card.event_key), 0) for t in teams)
        before = min(total.get(t, 0) - here.get((t, card.event_key), 0) for t in teams)
        feats = correction_features(model, card, prediction, seen, before)
        margin = (prediction.red_mean + prediction.red_fouls) - (prediction.blue_mean + prediction.blue_fouls)
        sd = math.sqrt(max((prediction.red_var or 0) + (prediction.blue_var or 0) + prediction.red_fouls_var + prediction.blue_fouls_var, 1e-6))
        base = red_win_prob(margin, sd, seen, playoff=card.comp_level != "qm")
        out[card.match_key] = (base, corrected(2026, base, feats))
        model.observe(result)
        for t in teams:
            here[(t, card.event_key)] = here.get((t, card.event_key), 0) + 1
            total[t] = total.get(t, 0) + 1
    return out


def step_parity() -> dict:
    # Probabilities only, no outcomes: the reproduction against what the live API served
    # for the events saved in research-data/prod/served_<event>.json.
    prod = production_2026()
    out = {}
    for path in sorted(PROD.glob("served_*.json")):
        served = json.loads(path.read_text())
        gaps_base, gaps_net = [], []
        for m in served["matches"]:
            pred = m.get("prediction") or {}
            key = m["match_key"].lower()
            if key in prod and pred.get("red_win_prob") is not None and pred.get("red_win_prob_team_model") is not None:
                gaps_base.append(abs(prod[key][0] - pred["red_win_prob_team_model"]))
                gaps_net.append(abs(prod[key][1] - pred["red_win_prob"]))
        out[served["event_key"]] = {
            "matches": len(gaps_net),
            "base_mean_abs_gap": round(float(np.mean(gaps_base)), 5) if gaps_base else None,
            "base_max_abs_gap": round(float(np.max(gaps_base)), 5) if gaps_base else None,
            "network_mean_abs_gap": round(float(np.mean(gaps_net)), 5) if gaps_net else None,
            "network_max_abs_gap": round(float(np.max(gaps_net)), 5) if gaps_net else None,
        }
        print(served["event_key"], out[served["event_key"]])
    return out


# ── Blind seasons: once per frozen phase 3 bundle ────────────────────────

def _same(a: np.ndarray, b: np.ndarray) -> bool:
    # Unplayed sides are NaN in both: equal.
    return bool(np.array_equal(a, b, equal_nan=a.dtype.kind == "f" and b.dtype.kind == "f"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest() -> dict:
    def git(*args):
        return subprocess.run(["git", *args], cwd=BACKEND_ROOT, capture_output=True, text=True).stdout.strip()

    digest = hashlib.sha256()
    for folder, years in ((p2.DATA, (*DEV, *BLIND)), (TBA_ALL, (2026,))):
        for year in years:
            for path in sorted((folder / str(year)).glob("*.json")):
                digest.update(path.name.encode())
                digest.update(path.read_bytes())
    return {
        "commit": git("rev-parse", "HEAD"),
        "uncommitted_changes": bool(git("status", "--porcelain", "--", ".")),
        "data_sha256": digest.hexdigest(),
        "phase2_bundle_runs_sha256": {f"{p2.BUNDLE}-{y}.json": _sha(p2.REPORTS / "runs" / f"{p2.BUNDLE}-{y}.json") for y in (*DEV, *BLIND)},
        "phase2_hindsight_sha256": _sha(p2.REPORTS / "hindsight.json"),
        "production_weights_sha256": _sha(CORRECTION_WEIGHTS),
        "production_priors_sha256": _sha(PROD / "team_prior_strengths_2025.csv"),
        "bundle": PHASE3_BUNDLE,
        "chain": BLIND_CHAIN,
        "score_variants": BLIND_SCORE,
        "score_pairs": BLIND_SCORE_PAIRS,
        "win_variants": {n: asdict(WIN_VARIANTS[n]) for n in BLIND_WIN},
        "fixed": BLIND_FIXED,
        "pairs": BLIND_PAIRS,
        "note": (
            "2025 and 2026 were already run blind once for phase 2's state model and their results were seen: weaker "
            "evidence than phase 2's blind run; 2027 is the only untouched test. The chain starts at 2023 because 2022's "
            "predictions start from a stand-in built from 2026's mean score. Development choices (2022-2024) did use 2022."
        ),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


CLAIM = REPORTS / "blind" / "CLAIMED"


def step_blind() -> None:
    # Checks first, then an exclusive claim (a second run, even after a crash, is
    # refused), then everything rebuilt from the committed code: tables for the whole chain,
    # only the frozen variants and pairs.
    if not PHASE3_BUNDLE:
        raise SystemExit("freeze PHASE3_BUNDLE first")
    record = manifest()
    if record["uncommitted_changes"]:
        raise SystemExit("commit first: the manifest must name the code that ran")
    # Everything that can fail and shows no result goes before the claim: the tables
    # (features and the replay check) and production's own predictions.
    tables = {y: build_table(y, ("epa", "elo")) for y in BLIND_CHAIN}
    rebuilt = {}
    for y in BLIND_CHAIN:
        if y in DEV and table_path(y).exists():
            saved = load_table(y)
            shared = set(saved) & set(tables[y])
            rebuilt[str(y)] = {
                "same_values_on_shared_columns": all(_same(saved[k], tables[y][k]) for k in shared),
                "only_in_saved": sorted(set(saved) - shared), "only_in_rebuilt": sorted(set(tables[y]) - shared),
            }
    prod = production_2026()
    missing = [k for k in tables[2026]["key"] if k not in prod]
    if missing:
        raise SystemExit(f"production replay lacks {len(missing)} official 2026 matches, e.g. {missing[:3]}")
    CLAIM.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(CLAIM, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"{CLAIM} exists: the phase 3 blind run has been claimed ({CLAIM.read_text().strip()}); 2025/2026 run once") from None
    with os.fdopen(fd, "w") as handle:
        handle.write(f"{record['commit']} {record['started']}\n")
    save(f"blind/{record['commit']}/manifest", {**record, "status": "started", "dev_tables_vs_saved": rebuilt})
    for y in BLIND:
        save_table(y, tables[y])
    rep = frozen_pipeline(list(BLIND_CHAIN), list(BLIND), tables)
    rep["production_2026"] = production_comparison(tables[2026], rep.pop("_win_probs")[2026], prod)
    save(f"blind/{record['commit']}/report", rep)
    print_report(rep, list(BLIND))
    print_production(rep["production_2026"])
    outputs = {f"{y}.npz": _sha(table_path(y)) for y in BLIND}
    save(f"blind/{record['commit']}/manifest", {
        **record, "dev_tables_vs_saved": rebuilt, "status": "finished", "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dev_tables_identical_to_saved": rebuilt, "blind_table_sha256": outputs,
    })


def frozen_pipeline(chain: list[int], evaluated: list[int], tables: dict[int, dict]) -> dict:
    # Exactly what the blind step runs: the frozen variants and pairs only.
    runs = run_seasons(chain, tables, BLIND_WIN, BLIND_SCORE, BLIND_FIXED)
    rep = report(evaluated, tables, runs, BLIND_PAIRS, BLIND_SCORE_PAIRS, BLIND_WIN, BLIND_SCORE, BLIND_FIXED)
    rep["_win_probs"] = runs["win"]
    return rep


def step_rehearse() -> None:
    # The blind pipeline on development seasons (2024 plays "blind"), to catch crashes
    # before the one real run. Writes nothing under blind/.
    tables = {y: build_table(y, ("epa", "elo")) for y in (2023, 2024)}
    rep = frozen_pipeline([2023, 2024], [2024], tables)
    rep.pop("_win_probs")
    print_report(rep, [2024])
    save("rehearsal-2024", rep)


def production_comparison(t: dict, probs: dict[str, np.ndarray], prod: dict[str, tuple[float, float]]) -> dict:
    base = np.array([prod[k][0] for k in t["key"]])
    net = np.array([prod[k][1] for k in t["key"]])
    best = probs[PHASE3_BUNDLE["win"]]
    out: dict = {
        "note": "production's network was trained on every 2026 event: in-sample here. Scored on official events; the reproduction replays offseason events too and matched served probabilities to 5e-5 on three events.",
        "slices": {},
    }
    for sl in ("all", "newest 20% of events", "FRC weeks 1-3", "DCMP + Championship", "nobody has played here yet", "first third of an event"):
        mask = WIN_SLICES[sl](t)
        out["slices"][sl] = {
            "production_network": win_summary(t, net, mask),
            "production_base": win_summary(t, base, mask),
            "phase3_best": win_summary(t, best, mask),
            "phase3_bucket": win_summary(t, probs["bucket"], mask),
            "best_vs_network": win_compare([t], [best], [net], WIN_SLICES[sl]),
            "best_vs_production_base": win_compare([t], [best], [base], WIN_SLICES[sl]),
        }
    return out


def print_production(rep: dict) -> None:
    print(f"\nProduction 2026 ({rep['note']})")
    for sl, v in rep["slices"].items():
        print(f"  {sl:28} network {v['production_network']['brier']:.4f}  production base {v['production_base']['brier']:.4f}  phase 3 best {v['phase3_best']['brier']:.4f}  bucket {v['phase3_bucket']['brier']:.4f}"
              f"  best - network {_fmt(v['best_vs_network']['brier'])}  logloss {_fmt(v['best_vs_network']['log_loss'])}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, choices=["tables", "dev", "parity", "rehearse", "blind"])
    parser.add_argument("--seasons", default=",".join(map(str, DEV)))
    parser.add_argument("--label", default="dev")
    parser.add_argument("--blind-set", action="store_true", help="dev: only the frozen blind variants and pairs")
    args = parser.parse_args()
    seasons = [int(s) for s in args.seasons.split(",")]
    if args.step in ("tables", "dev") and set(seasons) & set(BLIND):
        raise SystemExit("2025/2026 are blind: use --step blind")
    if args.step == "tables":
        for y in seasons:
            save_table(y, build_table(y))
    if args.step == "dev":
        tables = {y: load_table(y) for y in seasons}
        if args.blind_set:
            runs = run_seasons(seasons, tables, BLIND_WIN)
            rep = report(seasons, tables, runs, BLIND_PAIRS, SCORE_PAIRS, BLIND_WIN)
        else:
            runs = run_seasons(seasons, tables)
            rep = report(seasons, tables, runs, WIN_PAIRS, SCORE_PAIRS)
        print_report(rep, seasons)
        print(f"\nreport: {save(args.label, rep)}", file=sys.stderr)
    if args.step == "parity":
        save("parity", step_parity())
    if args.step == "rehearse":
        step_rehearse()
    if args.step == "blind":
        step_blind()
    return 0


if __name__ == "__main__":
    sys.exit(main())
