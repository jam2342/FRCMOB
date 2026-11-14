from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.services.ratings import rp_comparison as comparison  # noqa: E402
from app.services.ratings.state_inputs import digest  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="2026 flag RP vs live threshold recipe; cached inputs only, no writes/network")
    parser.add_argument("--cache",required=True,type=Path)
    parser.add_argument("--priors-2025",required=True,type=Path)
    parser.add_argument("--snapshots",required=True,type=Path)
    args = parser.parse_args()
    started = perf_counter()
    research,metadata,source_sha = comparison.load_inputs(args.cache)
    priors = comparison.read_priors(args.priors_2025)
    parity = comparison.served_parity(comparison.reproduce(research,comparison.live_model(research,priors)),args.snapshots)
    out = {"season":2026,"serving_eligible":False,"parity":parity,
        "provenance":comparison.provenance(research,priors,source_sha),
        "code_sha":subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip(),
        "snapshots_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.snapshots.glob("served_2026*.json"))},
        "limitations":["Examined 2026 development evidence; no release verdict or hyperparameter selection",
            "The live recipe keeps its existing 2026-derived priors and RP calibration; these are not out-of-sample fits",
            "Reproduction uses retrospective event thresholds and scheduled-time fallbacks, like the current historical API",
            "Comparison updates both models only when post_result_time is strictly before the actual start; absent/invalid publication times cannot update either model",
            "Scored labels are common eligible official qual flags; historical correction publication times remain unavailable, so prospective capture is still required"]}
    if parity["passed"]:
        rows,coverage = comparison.replay_comparison(research,metadata,comparison.live_model(research,priors))
        out.update(status="development_comparison",coverage=coverage,summaries=comparison.summarize(rows),forecast_sha256=digest(rows))
    else:
        out["status"] = "reproduction_failed"
    out["seconds"] = perf_counter()-started
    print(json.dumps(out,indent=2,allow_nan=False))
    return 0 if parity["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
