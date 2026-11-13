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


def build_table(year: int) -> dict[str, np.ndarray]:
    started = time.perf_counter()
    saved = json.loads((p2.REPORTS / "runs" / f"{p2.BUNDLE}-{year}.json").read_text())
    plan = [(p["from_time"], p2.params_from(p["params"])) for p in saved["plan"]]
    inputs = season_inputs_static(year)
    prev = p2.research(year - 1)
    previous_unit = mean_share(prev.season.results) if prev else None
    multipliers = start_multipliers(inputs.z_previous, inputs.rookies)
    side = {
        "epa": side_probs(inputs, EpaClone(multipliers, previous_unit)),
        "elo": side_probs(inputs, MarginElo(multipliers, previous_unit)),
        "ridge": side_probs(inputs, RidgePriorOpr(multipliers, previous_unit)),
    }
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


def run_seasons(seasons: list[int], tables: dict[int, dict], win_names: tuple[str, ...] = tuple(WIN_VARIANTS)) -> dict:
    # In season order, so each season's "past" variants start from the seasons before it.
    out: dict = {"score": {}, "win": {}, "score_fits": {}, "win_fits": {}}
    score_history: dict[bool, list[ScoreCalibration]] = {False: [], True: []}
    win_history: dict[str, list] = {name: [] for name in win_names}
    for year in sorted(seasons):
        t = tables[year]
        started = time.perf_counter()
        none = (t["rm"].copy(), t["bm"].copy())
        out["score"][year] = {"score_none": none}
        out["score_fits"][year] = {}
        for name, terms, past in (("score_cal", False, False), ("score_cal_past", False, True), ("score_terms", True, False), ("score_terms_past", True, True)):
            red, blue, fits = walk_forward_scores(t, score_centre(score_history[terms]) if past else None, terms=terms)
            out["score"][year][name] = (red, blue)
            out["score_fits"][year][name] = fits
        for terms in (False, True):
            score_history[terms].append(fit_score_calibration(side_arrays(t), terms=terms))
        probs = {"bucket": t["p_bucket"], "phi": 1.0 / (1.0 + np.exp(-logit_phi(zscore(t))))}
        out["win_fits"][year] = {}
        red, blue = out["score"][year]["score_terms_past"]
        calibrated = {**t, "rm": red, "bm": blue}
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
    "nobody has played here yet": lambda t: t["min_here"] <= 0,
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
        **p2.calibration(_matches(t, p, m)),
    }


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
    rows = np.flatnonzero(mask)
    sides = side_arrays(t, rows)
    if not len(sides["actual"]):
        return {"n": 0}
    mu = np.concatenate([red[rows], blue[rows]])
    a = np.concatenate([t["ra"][rows], t["ba"][rows]])
    v = np.concatenate([t["rv"][rows], t["bv"][rows]])
    ok = np.isfinite(a) & (v > 0)
    mu, a, v = mu[ok], a[ok], v[ok]
    nu = a - mu
    return {
        "sides": int(ok.sum()),
        "points_mae": round(float(np.mean(np.abs(nu))), 3),
        "loglik_per_obs": round(float(np.mean(-0.5 * (np.log(2 * math.pi * v) + nu * nu / v))), 4),
        "coverage_80": round(float(np.mean(np.abs(nu) <= Z80 * np.sqrt(v))), 4),
        "mean_bias_pct": round(float(100 * nu.sum() / mu.sum()), 2),
        "decile_bias_pct": decile_bias(mu, a),
    }


def decile_bias(mu: np.ndarray, actual: np.ndarray, deciles: int = 10) -> list[float]:
    # sum(actual - predicted) / sum(predicted) per decile of the variant's own predictions.
    edges = np.quantile(mu, np.linspace(0, 1, deciles + 1))
    which = np.clip(np.searchsorted(edges, mu, side="right") - 1, 0, deciles - 1)
    return [round(float(100 * (actual[which == d] - mu[which == d]).sum() / max(mu[which == d].sum(), 1e-9)), 2) for d in range(deciles)]


SCORE_SLICES = {
    "all": lambda t: np.ones(len(t["y"]), bool),
    "quals": lambda t: t["playoff"] == 0,
    "playoffs": lambda t: t["playoff"] > 0,
    "first match here": lambda t: (t["min_here"] <= 0) & (t["playoff"] == 0),
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


def report(seasons: list[int], tables: dict[int, dict], runs: dict, win_pairs, score_pairs, win_names: tuple[str, ...] = tuple(WIN_VARIANTS)) -> dict:
    out: dict = {"seasons": seasons, "score": {"summaries": {}, "comparisons": {}, "gates": {}}, "win": {"summaries": {}, "comparisons": {}, "gates": {}}}
    # Score calibration.
    for name in SCORE_VARIANTS:
        for y in seasons:
            red, blue = runs["score"][y][name]
            out["score"]["summaries"][f"{name} {y}"] = {s: score_summary(tables[y], red, blue, f(tables[y])) for s, f in SCORE_SLICES.items()}
            # The win probability these means give through the harness's bucket logistic.
            z = (red - blue) / np.sqrt(tables[y]["mv"])
            out["score"]["summaries"][f"{name} {y}"]["all"]["brier_via_bucket_logistic"] = win_summary(tables[y], bucket_probs(tables[y], z), np.ones(len(z), bool))["brier"]
    for a, b in score_pairs:
        key = f"{a} vs {b}"
        out["score"]["comparisons"][key] = {}
        for y in [*seasons, "pooled"]:
            ys = seasons if y == "pooled" else [y]
            out["score"]["comparisons"][key][str(y)] = {
                s: score_compare([tables[yy] for yy in ys], [runs["score"][yy][a] for yy in ys], [runs["score"][yy][b] for yy in ys], f) for s, f in SCORE_SLICES.items()
            }
        out["score"]["gates"][key] = score_gates(out["score"], a, b, seasons)
    # Win layer.
    names = [*FIXED, *win_names]
    for name in names:
        for y in seasons:
            out["win"]["summaries"][f"{name} {y}"] = {s: win_summary(tables[y], runs["win"][y][name], f(tables[y])) for s, f in WIN_SLICES.items()}
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


def score_gates(rep: dict, a: str, b: str, seasons: list[int]) -> dict:
    c = rep["comparisons"][f"{a} vs {b}"]
    top = {y: (rep["summaries"][f"{a} {y}"]["all"]["decile_bias_pct"][-1], rep["summaries"][f"{b} {y}"]["all"]["decile_bias_pct"][-1]) for y in seasons}
    big = {y: (rep["summaries"][f"{a} {y}"]["DCMP + Championship"]["decile_bias_pct"][-1], rep["summaries"][f"{b} {y}"]["DCMP + Championship"]["decile_bias_pct"][-1]) for y in seasons}
    return {
        "top-decile bias halved every season": all(abs(ta) <= 0.5 * abs(tb) for ta, tb in top.values()),
        "top-decile bias halved at DCMP + Championship every season": all(abs(ta) <= 0.5 * abs(tb) for ta, tb in big.values()),
        "points MAE better (pooled interval < 0)": c["pooled"]["all"]["abs_error"]["hi90"] < 0,
        "score loglik better (pooled interval < 0)": c["pooled"]["all"]["neg_loglik"]["hi90"] < 0,
        "no season's MAE worse (point)": all(c[str(y)]["all"]["abs_error"]["mean_diff"] <= 0 for y in seasons),
        "Brier via the bucket logistic not worse than 0.0005 (pooled mean)": float(np.mean([rep["summaries"][f"{a} {y}"]["all"]["brier_via_bucket_logistic"] - rep["summaries"][f"{b} {y}"]["all"]["brier_via_bucket_logistic"] for y in seasons])) <= 0.0005,
    }


def win_gates(rep: dict, a: str, b: str, seasons: list[int]) -> dict:
    c = rep["comparisons"][f"{a} vs {b}"]
    pooled = c["pooled"]

    def add(slice_name):
        return (
            pooled[slice_name]["brier"]["mean_diff"] <= -0.0015 and pooled[slice_name]["brier"]["hi90"] < 0
            and all(c[str(y)][slice_name]["brier"]["mean_diff"] <= 0.0005 for y in seasons)
        )

    ece_a = np.mean([rep["summaries"][f"{a} {y}"]["all"]["ece"] for y in seasons])
    ece_b = np.mean([rep["summaries"][f"{b} {y}"]["all"]["ece"] for y in seasons])
    return {
        "add rule (all)": add("all"),
        "add rule (FRC weeks 1-3)": add("FRC weeks 1-3"),
        "add rule (nobody has played here yet)": add("nobody has played here yet"),
        "add rule (first third of an event)": add("first third of an event"),
        "simplify rule: pooled Brier not worse by > 0.0005": pooled["all"]["brier"]["mean_diff"] <= 0.0005,
        "calibration rule: ECE halved, interval < 0, Brier not worse": bool(ece_a <= 0.5 * ece_b and pooled["all"]["calibration"].get("ece", {}).get("hi90", 1) < 0 and pooled["all"]["brier"]["mean_diff"] <= 0),
    }


def _fmt(d: dict) -> str:
    if not d or not d.get("n"):
        return "n/a"
    return f"{d['mean_diff']:+.4f} [{d['lo90']:+.4f}, {d['hi90']:+.4f}]"


def print_report(rep: dict, seasons: list[int]) -> None:
    s = rep["score"]
    print("\nScore calibration: bias by decile of each variant's own predicted alliance score (% of predicted; + = under-predicted)")
    for key, v in s["summaries"].items():
        for sl in ("all", "DCMP + Championship", "playoffs", "first match here"):
            x = v[sl]
            if x.get("sides"):
                extra = f"  Brier(bucket) {v['all']['brier_via_bucket_logistic']:.4f}" if sl == "all" else ""
                print(f"  {key:22} {sl:22} MAE {x['points_mae']:7.2f} ll {x['loglik_per_obs']:7.3f} cov80 {x['coverage_80']:.3f} mean {x['mean_bias_pct']:+5.1f}  deciles {' '.join(f'{d:+5.1f}' for d in x['decile_bias_pct'])}{extra}")
    for key, by in s["comparisons"].items():
        print(f"\n  {key}")
        for y, sl in by.items():
            for name in ("all", "DCMP + Championship", "playoffs", "first match here"):
                print(f"    {y:7} {name:22} MAE {_fmt(sl[name]['abs_error']):28} -loglik {_fmt(sl[name]['neg_loglik'])}")
        for g, ok in s["gates"][key].items():
            print(f"    gate: {g}: {'pass' if ok else 'FAIL'}")
    w = rep["win"]
    print("\nWin layer summaries")
    print(f"  {'run':22} {'slice':28} {'n':>6} {'brier':>7} {'logloss':>8} {'acc':>6} {'slope':>6} {'ECE':>6} {'gap.9':>6}")
    for key, v in w["summaries"].items():
        for sl, x in v.items():
            if x.get("n") and sl in ("all", "FRC weeks 1-3", "DCMP + Championship", "nobody has played here yet", "first third of an event", "playoffs", "newest 20% of events"):
                print(f"  {key:22} {sl:28} {x['n']:>6} {x['brier']:>7.4f} {x['log_loss']:>8.4f} {x['accuracy']:>6.3f} {str(x['calibration_slope']):>6} {x['ece']:>6.4f} {str(x.get('gap_0.9')):>6}")
    print("\nPaired differences a - b (negative = a better), 90% event-cluster bootstrap")
    for key, by in w["comparisons"].items():
        print(f"\n  {key}")
        for y, sl in by.items():
            for name in ("all", "FRC week 1", "FRC week 2", "FRC weeks 1-3", "FRC week 4", "FRC weeks 4-7", "FRC week 8+", "DCMP + Championship", "nobody has played here yet", "first third of an event", "playoffs"):
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


# ── Production's 2026 network, reproduced (blind step only) ──────────────

def production_2026() -> dict[str, tuple[float, float]]:
    # What users see: the live team model (hub + tower, duds, 2026 settings), production's
    # bucket scales and the 14-input network trained on 2026 (in-sample on every 2026
    # event), through the same replay order as team_model_service._compute.
    from evaluate_rating_research import build_model

    from app.services.events.win_probability import red_win_prob
    from app.services.ratings.team_model_service import correction_features
    from app.services.ratings.win_correction import corrected

    research = p2.research(2026)
    previous = p2.research(2025)
    model, _alt = build_model("team_model", 2026, research, previous, mean_share(research.season.results))
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


# ── Blind seasons: once per frozen phase 3 bundle ────────────────────────

def manifest() -> dict:
    def git(*args):
        return subprocess.run(["git", *args], cwd=BACKEND_ROOT, capture_output=True, text=True).stdout.strip()

    digest = hashlib.sha256()
    for year in (*DEV, *BLIND):
        for path in sorted((p2.DATA / str(year)).glob("*.json")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    runs = {f"{p2.BUNDLE}-{y}.json": hashlib.sha256((p2.REPORTS / "runs" / f"{p2.BUNDLE}-{y}.json").read_bytes()).hexdigest() for y in (*DEV, *BLIND)}
    return {
        "commit": git("rev-parse", "HEAD"),
        "uncommitted_changes": bool(git("status", "--porcelain", "--", ".")),
        "data_sha256": digest.hexdigest(),
        "phase2_bundle_runs_sha256": runs,
        "phase2_hindsight_sha256": hashlib.sha256((p2.REPORTS / "hindsight.json").read_bytes()).hexdigest(),
        "bundle": PHASE3_BUNDLE,
        "win_variants": {n: asdict(WIN_VARIANTS[n]) for n in BLIND_WIN},
        "pairs": BLIND_PAIRS,
        "note": "2025 and 2026 were already run blind for phase 2's state model; this is weaker evidence than phase 2's blind run. 2027 is the only untouched test.",
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def step_blind() -> None:
    record = manifest()
    if not PHASE3_BUNDLE:
        raise SystemExit("freeze PHASE3_BUNDLE first")
    if record["uncommitted_changes"]:
        raise SystemExit("commit first: the manifest must name the code that ran")
    folder = REPORTS / "blind"
    if folder.exists() and any(folder.iterdir()):
        raise SystemExit(f"a phase 3 blind run exists ({sorted(p.name for p in folder.iterdir())}): 2025/2026 run once")
    save(f"blind/{record['commit']}/manifest", {**record, "status": "started"})
    tables = {y: load_table(y) for y in DEV}
    for y in BLIND:
        tables[y] = build_table(y)
        save_table(y, tables[y])
    seasons = [*DEV, *BLIND]
    runs = run_seasons(seasons, tables, BLIND_WIN)
    rep = report(list(BLIND), tables, runs, BLIND_PAIRS, SCORE_PAIRS, BLIND_WIN)
    rep["production_2026"] = production_comparison(tables[2026], runs["win"][2026])
    save(f"blind/{record['commit']}/report", rep)
    print_report(rep, list(BLIND))
    print_production(rep["production_2026"])
    outputs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPORTS / "tables").glob("*.npz"))}
    save(f"blind/{record['commit']}/manifest", {**record, "status": "finished", "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "table_sha256": outputs})


def production_comparison(t: dict, probs: dict[str, np.ndarray]) -> dict:
    prod = production_2026()
    base = np.array([prod[k][0] for k in t["key"]])
    net = np.array([prod[k][1] for k in t["key"]])
    best = probs[PHASE3_BUNDLE["win"]]
    out: dict = {"note": "production network trained on every 2026 event: in-sample here. Official events only (production also replays offseason events).", "slices": {}}
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
    parser.add_argument("--step", required=True, choices=["tables", "dev", "blind"])
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
    if args.step == "blind":
        step_blind()
    return 0


if __name__ == "__main__":
    sys.exit(main())
