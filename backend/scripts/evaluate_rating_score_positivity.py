#!/usr/bin/env python3
# Positive score forecasts on saved historical rehearsals (#154 gated, #157 reported).
# Read-only on its sources; writes only under --out; never serves anything.
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.services.ratings import age_score_rehearsal as age, score_positivity as positivity, weekly_rehearsal as weekly  # noqa: E402
from app.services.ratings.component_screen import load  # noqa: E402
from app.services.ratings.research_rehearsal import write_settings  # noqa: E402

SEASONS = (2023,2024,2025,2026)
CODE = ("app/services/ratings/score_positivity.py","app/services/ratings/age_score_rehearsal.py","app/services/ratings/weekly_rehearsal.py",
        "app/services/ratings/research_eval.py","app/services/ratings/component_screen.py","scripts/evaluate_rating_score_positivity.py")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def debut_frames(root, source_report):
    reports = [weekly.read_object(root/"reports",p.stem) for p in (root/"reports").glob("*.json")]
    if len(reports) != 1 or reports[0]["identity"]["source_report"] != source_report or reports[0]["status"] != "complete":
        raise ValueError("need one complete debut report on the same source")
    return {f["key"]:f["forecasts"]["debut_score"] for f in weekly.read_object(root/"journals",reports[0]["journal"])["forecasts"]}


def protocol(args):
    return {"recipe":positivity.RECIPE,"policy":positivity.POLICY,"seasons":list(SEASONS),"serving_eligible":False,
        "checks":{"all_positive":"no score forecast at or below zero; no forecast lowered",
            "bottom_decile_mae_not_worse":"paired 90% event-cluster upper bound <= 0 on base-predicted bottom-decile qualification sides",
            "qualification_mae_not_worse":"paired 90% event-cluster upper bound <= 0 on all qualification sides",
            "qualification_bias":"abs <= 3%","top_decile_unmoved":"bias change <= .1 percentage points","wins_unchanged":"exact"},
        "environment":{"numpy":version("numpy")},"code_sha256":{name:sha(ROOT/name) for name in CODE},
        "sources":{str(y):age.hashes(args.source/str(y)) for y in SEASONS},
        "debut_runs":{str(y):age.hashes(args.debut/str(y)) for y in SEASONS},
        "caches":{str(y):load(args.cache,y).source_sha256 for y in SEASONS},
        "release":"blocked: historical development evidence; full recipe, RP integration and clean 2027 remain"}


def main():
    parser = argparse.ArgumentParser(description="Positive score forecasts on saved rehearsals (offline, historical)")
    parser.add_argument("--step",required=True,choices=("freeze","run"))
    parser.add_argument("--source",required=True,type=Path,help="#154 runs folder (one folder per season)")
    parser.add_argument("--debut",required=True,type=Path,help="#157 runs folder (one folder per season)")
    parser.add_argument("--cache",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--protocol",type=Path)
    args = parser.parse_args()
    if any(p.resolve().is_relative_to(ROOT.parent) for p in (args.source,args.debut,args.cache,args.out)):
        parser.error("research files must stay outside the public repository")
    if any(args.out.resolve().is_relative_to(p.resolve()) or p.resolve().is_relative_to(args.out.resolve()) for p in (args.source,args.debut,args.cache)):
        parser.error("output must not overlap its inputs")
    import os
    os.umask(0o077)
    current = protocol(args)
    if args.step == "freeze":
        path = write_settings(args.out/"protocols",current)
        print(json.dumps({"protocol":str(path),"sha256":path.stem}),flush=True)
        return
    if args.protocol is None:
        parser.error("run needs --protocol")
    if weekly.read_object(args.protocol.parent,args.protocol.stem) != current:
        raise ValueError("protocol, sources, cache or implementation changed since freezing")
    commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    for year in SEASONS:
        frames,report,_,report_sha = age.source_run(args.source/str(year))
        data = load(args.cache,year)
        if report["identity"]["data_sha256"] != data.source_sha256:
            raise ValueError("source cache changed")
        debut = debut_frames(args.debut/str(year),report_sha)
        if set(debut) != {f["key"] for f in frames}:
            raise ValueError("debut forecasts cover other matches")
        for f in frames:
            f["forecasts"]["debut_score"] = debut[f["key"]]
        source = {r["result"].card.match_key:r for r in data.records}
        result = {"kind":"positive score check","season":year,"protocol_sha256":args.protocol.stem,"commit":commit,
                  "source_report":report_sha,"serving_eligible":False,"adopted":False,"bases":{}}
        for base in positivity.POLICY["reported_bases"]:
            outcome = positivity.compare(positivity.apply(frames,base),source,base)
            outcome["checks"] = positivity.checks(outcome)
            outcome["passed"] = all(outcome["checks"].values())
            result["bases"][base] = outcome
        result["study_passed"] = result["bases"][positivity.POLICY["gated_base"]]["passed"]
        if age.hashes(args.source/str(year)) != current["sources"][str(year)] or age.hashes(args.debut/str(year)) != current["debut_runs"][str(year)]:
            raise ValueError("inputs changed during the run")
        path = write_settings(args.out/"reports",result)
        print(json.dumps({"season":year,"report":str(path),"study_passed":result["study_passed"],
                          "checks":{b:o["checks"] for b,o in result["bases"].items()}}),flush=True)


if __name__ == "__main__":
    main()
