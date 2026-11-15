#!/usr/bin/env python3
# Paired 2026 comparison: a weekly rehearsal's saved forecasts against production's
# correction network retrained on the same publication clock. Development evidence only
# (2026 has been examined); writes nothing outside --out and never serves anything.
from __future__ import annotations

import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.services.ratings import network_pairing as pairing  # noqa: E402
from app.services.ratings.research_rehearsal import write_settings  # noqa: E402
from app.services.ratings.state_inputs import digest  # noqa: E402

CODE = ("app/services/ratings/network_pairing.py", "app/services/ratings/weekly_rehearsal.py", "app/services/ratings/team_model.py",
        "app/services/ratings/team_model_service.py", "app/services/ratings/win_correction.py", "app/services/ratings/constants.py",
        "app/services/events/win_probability.py", "app/services/ratings/research_data.py", "app/services/ratings/component_screen.py",
        "app/services/ratings/research_eval.py", "app/services/ratings/research_rehearsal.py", "scripts/train_win_correction.py",
        "scripts/evaluate_rating_phase3.py", "scripts/evaluate_rating_network_pairing.py")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(Path(root).rglob("*")) if p.is_file() and p.name != "run.lock"}


def protocol(source, cache, priors):
    head = json.loads((source / "head.json").read_text())["sha256"]
    return {"schema": pairing.SCHEMA, "policy": pairing.POLICY, "serving_eligible": False,
            "source": {"manifest": json.loads((source / "manifest.json").read_text()), "head_sha256": head,
                       "reports": sorted(p.stem for p in (source / "reports").glob("*.json"))},
            "inputs": {"cache_season": str(pairing.POLICY["season"]), "cache_name": cache.name, "priors_sha256": sha(priors),
                       "in_sample_weights_sha256": sha(ROOT / "app/services/ratings/data/win_correction_2026.json")},
            "environment": {"numpy": version("numpy"), "torch": version("torch")},
            "code_sha256": {name: sha(ROOT / name) for name in CODE},
            "release": "blocked: examined 2026 development evidence; clean 2027 and full phase 7 still required"}


def read_priors(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        parts = line.split(",")
        if len(parts) == 3 and parts[0].startswith("frc"):
            out[parts[0].lower()] = float(parts[1])
    return out


def production_inputs(cache, priors):
    from app.services.ratings.constants import season_baseline
    from app.services.ratings.research_data import load_cached_season

    season = load_cached_season(cache, pairing.POLICY["season"], exclude_dq=False)
    factory = pairing.production_factory(season_baseline(pairing.POLICY["season"]), read_priors(priors), season.rookie_year)
    return season, factory


def step_parity(args):
    # The replay and read-only forecast must reproduce phase 3's production replay exactly.
    import evaluate_rating_phase3 as p3
    from app.services.ratings.win_correction import corrected

    season, factory = production_inputs(args.all_cache, args.priors)
    reference = p3.production_2026()
    ours = {r.card.match_key: (base, corrected(2026, base, x)) for r, base, x in pairing.immediate(season.season.results, factory)}
    if set(ours) != set(reference):
        raise ValueError("parity replay covers different matches")
    gaps = [max(abs(ours[k][0] - reference[k][0]), abs(ours[k][1] - reference[k][1])) for k in ours]
    value = {"kind": "production replay parity", "matches": len(ours), "max_abs_gap": max(gaps),
             "exact": max(gaps) == 0.0, "reference": "evaluate_rating_phase3.production_2026 (served parity 5e-5 on three events)"}
    path = write_settings(args.out / "parity", value)
    print(json.dumps({"parity": str(path), **value}), flush=True)
    if not value["exact"]:
        raise SystemExit("production replay parity failed")


def step_run(args, frozen):
    from app.services.ratings.component_screen import load
    from app.services.ratings.win_correction import corrected

    source = args.source
    before = tree(source)
    data = load(args.cache, pairing.POLICY["season"])
    season, factory = production_inputs(args.cache, args.priors)
    production = {r.card.match_key: r for r in season.season.results}
    records = {r["result"].card.match_key: r["result"] for r in data.records}
    if set(production) != set(records):
        raise ValueError("production and rehearsal match sets differ")
    for key, r in records.items():
        p = production[key]
        if (p.card.time, p.card.red, p.card.blue, p.red_score, p.blue_score) != (r.card.time, r.card.red, r.card.blue, r.red_score, r.blue_score):
            raise ValueError(f"production and rehearsal disagree on {key}")
    head, frames = pairing.load_source(source)
    posted = {r["result"].card.match_key: r["posted"] for r in data.records}
    replay = pairing.PublishedReplay(production.values(), posted, factory)
    issued = {}
    for record in data.records:
        card = production[record["result"].card.match_key].card
        replay.advance(card.time)
        issued[card.match_key] = replay.forecast(card)
    print(json.dumps({"issued": len(issued), "rewinds": replay.rewinds}), flush=True)
    networks = pairing.train_networks(data.records, production, factory)
    saved = [str(write_settings(args.run / "networks", n)) for n in networks]
    print(json.dumps({"networks": sum("state" in n for n in networks), "jobs": len(networks)}), flush=True)
    first, version_id, static_network = pairing.static_versions(frames, networks)
    _, newest = pairing.newest_events(frames)
    newest_keys = [f["key"] for f in frames if f["event"] in newest]
    frozen_forecasts, frozen_sha = pairing.frozen_candidate(source, data, frames, version_id, newest_keys)
    same = [f for f in frames if f["event"] in newest and f["forecasts"]["active"]["version"] == version_id]
    parity = max(abs(frozen_forecasts[f["key"]]["p"] - f["forecasts"]["active"]["p"]) for f in same) if same else None
    if not same or parity != 0.0:
        raise ValueError(f"frozen candidate does not reproduce saved forecasts (n={len(same)}, gap={parity})")
    rows = pairing.assemble(frames, data, issued, networks, static_network, frozen_forecasts, lambda base, x: corrected(2026, base, x))
    predictions = write_settings(args.run / "predictions", {
        "schema": pairing.SCHEMA, "protocol_sha256": args.protocol.stem, "source_head_sha256": json.loads((source / "head.json").read_text())["sha256"],
        "network_files": [Path(p).stem for p in saved], "static": {"first_newest_key": first["key"], "first_newest_time": first["time"],
        "candidate_version": version_id, "candidate_params_sha256": frozen_sha,
        "network_weights_sha256": static_network["weights_sha256"] if static_network else None,
        "network_publication": static_network["publication"] if static_network else None},
        "rows": rows})
    report = pairing.summarize(rows, frames)
    report.update({"schema": pairing.SCHEMA, "kind": "historical paired network comparison", "protocol_sha256": args.protocol.stem,
                   "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                   "predictions_sha256": predictions.stem, "network_files": [Path(p).stem for p in saved],
                   "networks": [{k: n.get(k) for k in ("week", "job", "publication", "labels", "results", "label_keys_sha256", "latest_label_posted", "weights_sha256")} for n in networks],
                   "issued_rewinds": replay.rewinds, "frozen_candidate_parity": {"matches": len(same), "max_abs_gap": parity},
                   "source_unchanged": tree(source) == before, "serving_eligible": False, "adopted": False,
                   "limitations": ["2026 is examined development data; no clean blind claim",
                                   "Production's team-model constants and bucket scales were hand-tuned on 2026 and cannot be held out",
                                   "Production as served also saw preseason/offseason events; both pipelines here see only the official-event results",
                                   "Corrections in final cached labels lack historical correction-publication timestamps",
                                   "The network uses only 2026 labels; it has no earlier-season start, unlike the candidate",
                                   "Network training replays prefix results in playing order; evaluation uses as-issued delayed-result features"]})
    if not report["source_unchanged"]:
        raise ValueError("source rehearsal files changed during the run")
    path = write_settings(args.run / "reports", report)
    print(json.dumps({"report": str(path), "predictions": str(predictions), "gate": report["gate"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Paired held-out production-network comparison (offline, historical)")
    parser.add_argument("--step", required=True, choices=("freeze", "parity", "run"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path, help="source rehearsal season folder")
    parser.add_argument("--cache", required=True, type=Path, help="official-event TBA cache the source used")
    parser.add_argument("--all-cache", type=Path, help="phase 3's all-event cache, for the parity step")
    parser.add_argument("--priors", required=True, type=Path)
    parser.add_argument("--protocol", type=Path)
    args = parser.parse_args()
    for path in (args.out, args.source):
        if path.resolve().is_relative_to(ROOT.parent):
            parser.error("research files must stay outside the public repository")
    os.umask(0o077)
    args.out.mkdir(parents=True, exist_ok=True)
    current = protocol(args.source, args.cache, args.priors)
    if args.step == "freeze":
        path = write_settings(args.out / "protocols", current)
        print(json.dumps({"protocol": str(path), "sha256": path.stem}), flush=True)
        return
    if args.protocol is None:
        parser.error("parity and run need --protocol")
    frozen = json.loads(args.protocol.read_text())
    if args.protocol.stem != digest(frozen) or frozen != current:
        raise ValueError("protocol, inputs or implementation changed since freezing")
    if args.step == "parity":
        if args.all_cache is None:
            parser.error("parity needs --all-cache")
        step_parity(args)
        return
    args.run = args.out / ("runs-" + subprocess.check_output(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, text=True).strip())
    step_run(args, frozen)


if __name__ == "__main__":
    main()
