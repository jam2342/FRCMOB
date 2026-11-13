#!/usr/bin/env python3
# Follow-up development evidence only: 2025/2026 have already been examined.
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import numpy as np

import evaluate_rating_phase3 as p3
from app.services.ratings.research_rehearsal import (
    CHALLENGERS, CHAMPION, PROTOCOL, first_match_scores, noninferiority, write_settings,
)
from app.services.ratings.research_win import (
    FEATURES, _pooled_temperatures, _pooled_weights, base_logit, context,
    correction_features, fit_correction, fit_temperatures, sigmoid, walk_forward_scores,
    walk_forward_win,
)

ROOT = p3.REPORTS.parent / "phase4"


def frozen_logistic(t, past, cutoff):
    mask = (t["time"] < cutoff) & (t["y"] != 0.5)
    temps = fit_temperatures(base_logit(t, CHAMPION.base)[mask], t["y"][mask], context(t)[mask], _pooled_temperatures(past))
    offset = temps.apply(base_logit(t, CHAMPION.base), context(t))
    f = correction_features(t)
    weights = fit_correction(f[mask], t["y"][mask], offset[mask], _pooled_weights(past, CHAMPION))
    return sigmoid(offset + f @ weights)


def heldout_network(t, past, rows):
    # Freeze BOTH correction fits before the first held-out match. State-model updates
    # still use earlier played matches, as they would live; correction labels do not.
    import train_win_correction as trainer
    import torch

    torch.set_num_threads(1)
    events = set(t["event"][t["holdout"]].tolist())
    cutoff = int(t["time"][t["holdout"]].min())
    train = [r for r in rows if r["event"] not in events and r["time"] < cutoff and r["y"] != 0.5]
    assert train and not events.intersection(r["event"] for r in train)
    net = trainer.fit(np.array([r["x"] for r in train]), np.array([r["y"] for r in train]))
    lookup = {r["key"]: r for r in rows}
    x = np.array([lookup[str(k)]["x"] for k in t["key"]])
    y = np.array([lookup[str(k)]["y"] for k in t["key"]])
    if not np.array_equal(y, t["y"]):
        raise ValueError("production and research outcomes differ")
    network = trainer.predict(net, x)
    champion = frozen_logistic(t, past, cutoff)
    def mask(tab):
        return tab["holdout"].astype(bool)
    comparison = p3.win_compare([t], [champion], [network], mask)
    return {
        "cutoff": cutoff, "training_matches": len(train), "training_events": sorted({r["event"] for r in train}),
        "holdout_events": sorted(events), "trainer": "train_win_correction.fit (torch, seed 0, 300 epochs)",
        "note": "Both correction fits frozen before cutoff; replay state updates remain as-of. 2026 is development evidence.",
        "champion": p3.win_summary(t, champion, mask(t)), "network": p3.win_summary(t, network, mask(t)),
        "comparison": comparison, "gate": noninferiority(comparison["brier"], PROTOCOL["network_noninferiority"]),
    }


def score_check(tables, protocol):
    history, out = [], {"protocol": protocol.name, "evidence": "development; damping proposed after separate-context failure", "seasons": {}}
    for year, t in tables.items():
        scores = walk_forward_scores(t, p3.score_centre(history), terms=True)
        history.append(p3.fit_score_calibration(p3.side_arrays(t), terms=True))
        first = (t["min_here"] <= 0) & (t["playoff"] == 0)
        damped = tuple(np.where(first, base + 0.5 * (cal - base), cal) for base, cal in zip((t["rm"], t["bm"]), scores[:2]))
        rep = {name: {sl: p3.score_summary(t, *pred, fn(t)) for sl, fn in p3.SCORE_SLICES.items()}
               for name, pred in {"phase3": scores[:2], "half_first": damped}.items()}
        rep["mae_comparison"] = p3.paired_cluster_bootstrap(
            p3.score_losses(t, *damped, p3.SCORE_SLICES["all"](t))["abs_error"],
            p3.score_losses(t, *scores[:2], p3.SCORE_SLICES["all"](t))["abs_error"], seed=7)
        bias = rep["half_first"]["someone's first match here"]["mean_bias_pct"]
        rep["bias_gate"] = "passed" if abs(bias) <= PROTOCOL["first_match_bias_limit_pct"] else "failed"
        out["seasons"][year] = rep
        print(f"{year}: half correction first-match bias {bias:+.2f}%, MAE delta {rep['mae_comparison']['mean_diff']:+.4f}", flush=True)
    path = write_settings(ROOT / "score-runs", out)
    assert len(json.loads(path.read_text())["seasons"]) == len(tables)
    print(f"Score report: {path}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=("freeze", "scores", "evaluate"), required=True)
    args = parser.parse_args()
    protocol = write_settings(ROOT / "protocols", PROTOCOL)
    print(f"Frozen protocol: {protocol}", flush=True)
    if args.step == "freeze":
        return
    tables = {year: p3.load_table(year) for year in PROTOCOL["development"]}
    report = {"protocol": protocol.name, "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "code_sha256": {str(p.relative_to(p3.BACKEND_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              [Path(__file__), Path(p3.__file__), p3.BACKEND_ROOT / "app/services/ratings/research_win.py", p3.BACKEND_ROOT / "app/services/ratings/research_rehearsal.py"]},
              "tables_sha256": {year: hashlib.sha256(p3.table_path(year).read_bytes()).hexdigest() for year in tables},
              "seasons": {}, "settings": [], "production_promotion": "blocked: no clean 2027 evidence"}
    if args.step == "scores":
        score_check(tables, protocol)
        return
    all_probs = {}
    histories = {name: [] for name in ("champion", *CHALLENGERS)}
    score_history, first_history = [], []
    for year, t in tables.items():
        print(f"Evaluating {year}", flush=True)
        probs, winfits = {}, {}
        for name, recipe in {"champion": CHAMPION, **CHALLENGERS}.items():
            probs[name], winfits[name], fit = walk_forward_win(t, recipe, histories[name])
            if year == 2026 and name == "champion":
                past2026 = histories[name].copy()
            histories[name].append(fit)
        all_probs[year] = probs
        scores = walk_forward_scores(t, p3.score_centre(score_history), terms=True)
        candidate = first_match_scores(t, np.mean(first_history, axis=0) if first_history else None)
        score_history.append(p3.fit_score_calibration(p3.side_arrays(t), terms=True))
        first_history.append(candidate[3])
        slices = ("all", "newest 20% of events", "FRC weeks 1-3", "DCMP + Championship")
        rep = {"challengers": {name: {sl: p3.win_compare([t], [probs[name]], [probs["champion"]], p3.WIN_SLICES[sl]) for sl in slices} for name in CHALLENGERS},
               "scores": {name: {sl: p3.score_summary(t, *pred[:2], fn(t)) for sl, fn in p3.SCORE_SLICES.items()} for name, pred in {"phase3": scores, "first_context": candidate, "uncalibrated": (t["rm"], t["bm"])}.items()}}
        bias = rep["scores"]["first_context"]["someone's first match here"]["mean_bias_pct"]
        rep["first_match_gate"] = "passed" if abs(bias) <= PROTOCOL["first_match_bias_limit_pct"] else "failed"
        report["seasons"][year] = rep
        for sf in scores[2]:
            wf = next(w for w in winfits["champion"] if w["week"] == sf["week"])
            payload = {"schema": 1, "season": year, "before_time": sf["before_time"], "protocol": protocol.name,
                       "recipe": asdict(CHAMPION), "score_terms": True, "score_coef": sf["coef"],
                       "temperatures": wf["temperatures"], "pooled_temperature": wf["pooled_temperature"],
                       "weights": wf["weights"], "features": list(FEATURES), "win_uses_uncalibrated_scores": True,
                       "code_sha256": report["code_sha256"], "tables_sha256": report["tables_sha256"]}
            report["settings"].append(str(write_settings(ROOT / "settings", payload)))
        print(f"{year}: first-match bias {bias:+.2f}% ({rep['first_match_gate']})", flush=True)
    report["challenger_gates"] = {}
    for name in CHALLENGERS:
        pooled = p3.win_compare(list(tables.values()), [all_probs[y][name] for y in tables],
                                [all_probs[y]["champion"] for y in tables], p3.WIN_SLICES["all"])
        b = pooled["brier"]
        season_guard = all(report["seasons"][y]["challengers"][name]["all"]["brier"]["mean_diff"] <= PROTOCOL["max_season_regression"] for y in tables)
        passed = b["mean_diff"] <= -PROTOCOL["add_bar"] and b["hi90"] < 0 and season_guard
        report["challenger_gates"][name] = {"comparison": pooled, "season_guard": season_guard, "gate": "passed" if passed else "failed"}
    rows = []
    p3.production_2026(training_rows=rows)
    report["heldout_production_network"] = heldout_network(tables[2026], past2026, rows)
    report["rehearsal"] = {"heldout_network": report["heldout_production_network"]["gate"],
                            "late_events": report["heldout_production_network"]["gate"],
                            "first_match": "passed" if all(r["first_match_gate"] == "passed" for r in report["seasons"].values()) else "failed",
                            "clean_2027": "pending", "promotion": "blocked"}
    output = write_settings(ROOT / "runs", report)
    # Read back the actual artifact; JSON encoding rejects nonfinite results.
    saved = json.loads(output.read_text())
    print(json.dumps({"report": str(output), "rehearsal": saved["rehearsal"], "heldout": saved["heldout_production_network"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
