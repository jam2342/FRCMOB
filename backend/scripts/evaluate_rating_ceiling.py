#!/usr/bin/env python3
# Re-scores a weekly rehearsal's week-4 check against the prospective-layer ceiling
# (hindsight state, layers refitted each Monday as live), alongside the old ceiling.
# Read-only on its source; writes only under --out; never serves anything.
from __future__ import annotations

import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.services.ratings import age_score_rehearsal as age, prospective_ceiling as ceiling, weekly_rehearsal as weekly  # noqa: E402
from app.services.ratings.component_screen import load  # noqa: E402
from app.services.ratings.research_rehearsal import write_settings  # noqa: E402

SEASONS = (2023,2024,2025,2026)
CODE = ("app/services/ratings/prospective_ceiling.py","app/services/ratings/state_fitting.py","app/services/ratings/calibrations.py",
        "app/services/ratings/research_win.py","app/services/ratings/weekly_rehearsal.py","app/services/ratings/age_score_rehearsal.py",
        "app/services/ratings/state_inputs.py","app/services/ratings/research_filter.py","scripts/evaluate_rating_ceiling.py")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protocol(args):
    return {"recipe":ceiling.RECIPE,"policy":ceiling.POLICY,"seasons":list(SEASONS),
        "check":"week-4-on Brier, active minus ceiling, paired 90% event-cluster upper bound <= hindsight_brier_tolerance",
        "tolerance":weekly.POLICY["hindsight_brier_tolerance"],"decided_by":"Jamal, 2026-11-15, after 2025 failed on in-sample layers",
        "judged_on":"2027; 2023-2026 reported under both ceilings","environment":{"numpy":version("numpy")},
        "code_sha256":{name:sha(ROOT/name) for name in CODE},"sources":{str(y):age.hashes(args.source/str(y)) for y in SEASONS},
        "caches":{str(y):load(args.cache,y).source_sha256 for y in SEASONS},"serving_eligible":False}


def main():
    parser = argparse.ArgumentParser(description="Week-4 check against the prospective-layer ceiling (offline, historical)")
    parser.add_argument("--step",required=True,choices=("freeze","run"))
    parser.add_argument("--source",required=True,type=Path,help="weekly rehearsal runs folder (one folder per season)")
    parser.add_argument("--cache",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--protocol",type=Path)
    parser.add_argument("--season",type=int,choices=SEASONS)
    args = parser.parse_args()
    if any(p.resolve().is_relative_to(ROOT.parent) for p in (args.source,args.cache,args.out)):
        parser.error("research files must stay outside the public repository")
    if any(args.out.resolve().is_relative_to(p.resolve()) or p.resolve().is_relative_to(args.out.resolve()) for p in (args.source,args.cache)):
        parser.error("output must not overlap its inputs")
    os.umask(0o077)
    current = protocol(args)
    if args.step == "freeze":
        path = write_settings(args.out/"protocols",current)
        print(json.dumps({"protocol":str(path),"sha256":path.stem}),flush=True)
        return
    if args.protocol is None or args.season is None:
        parser.error("run needs --protocol and --season")
    if weekly.read_object(args.protocol.parent,args.protocol.stem) != current:
        raise ValueError("protocol, source, cache or implementation changed since freezing")
    root = args.source/str(args.season)
    frames,report,versions,report_sha = age.source_run(root)
    oracle = weekly.read_object(root/"supplements",json.loads((root/"hindsight.json").read_text())["sha256"])
    if oracle["errors"]:
        raise ValueError("source hindsight fit failed")
    data = load(args.cache,args.season)
    if data.source_sha256 != report["identity"]["data_sha256"]:
        raise ValueError("source cache changed")
    source = {r["result"].card.match_key:r for r in data.records}
    path = ceiling.layer_path(root,args.season,report,versions,oracle["state"])
    summary = ceiling.summarize(frames,source,oracle,path)
    saved = report["metrics"]["week4_on"]["active_minus_hindsight"]["brier"]["mean_diff"]
    if abs(summary["metrics"]["week4_on"]["active_minus_old_ceiling"]["brier"]["mean_diff"]-saved) > 1e-12:
        raise ValueError("old ceiling does not reproduce the source report")
    if age.hashes(root) != current["sources"][str(args.season)]:
        raise ValueError("source changed during the run")
    result = {"kind":"prospective ceiling check","season":args.season,"protocol_sha256":args.protocol.stem,"source_report":report_sha,
              "commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),"hindsight_state":oracle["state"],
              **summary,"serving_eligible":False}
    out = write_settings(args.out/"reports",result)
    print(json.dumps({"season":args.season,"report":str(out),"checks":summary["checks"]}),flush=True)


if __name__ == "__main__":
    main()
