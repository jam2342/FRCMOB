from __future__ import annotations

import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

import argparse
import fcntl
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.services.ratings import age_score_rehearsal as age, weekly_rehearsal as weekly  # noqa: E402
from app.services.ratings.component_screen import load  # noqa: E402
from app.services.ratings.research_rehearsal import write_settings  # noqa: E402


def protocol(sources, tables):
    folder = ROOT/"app/services/ratings"
    return {"recipe":age.RECIPE,"policy":age.POLICY,"seasons":[2023,2024,2025,2026],"serving_eligible":False,
        "study_checks":{"first_score_bias":"abs <= 3% overall, first14 and after14 separately",
            "mae":"paired 90% event-cluster upper bound <= 0 for quals and first quals",
            "controls":"wins, nonfirst scores, source files and source state decisions unchanged"},
        "environment":{"numpy":version("numpy")},
        "code_sha256":{**{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.py"))},
            "rehearse_rating_age_score.py":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "sources":{str(y):age.hashes(Path(sources)/str(y)) for y in range(2023,2027)},
        "prior_tables":{str(y):hashlib.sha256((Path(tables)/f"{y}.npz").read_bytes()).hexdigest() for y in range(2023,2026)},
        "release":"blocked: conditional historical score study; full recipe, held-out network, RP and clean 2027 remain"}


def main():
    parser = argparse.ArgumentParser(description="Offline age-aware first-score challenger on a fixed state/win path")
    parser.add_argument("--step",required=True,choices=("freeze","run"))
    parser.add_argument("--sources",required=True,type=Path)
    parser.add_argument("--tables",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--protocol",type=Path)
    parser.add_argument("--cache",type=Path)
    parser.add_argument("--season",type=int,choices=(2023,2024,2025,2026))
    args = parser.parse_args()
    if any(p.resolve().is_relative_to(ROOT.parent) for p in (args.out,args.sources,args.tables)):
        parser.error("study files must stay outside the public repository")
    inputs = [args.sources,args.tables]+([args.cache] if args.cache else [])
    if any(args.out.resolve().is_relative_to(p.resolve()) or p.resolve().is_relative_to(args.out.resolve()) for p in inputs):
        parser.error("output must not overlap sources, tables or cache")
    os.umask(0o077)
    frozen = protocol(args.sources,args.tables)
    args.out.mkdir(parents=True,exist_ok=True)
    if args.step == "freeze":
        if any(not frozen["sources"][str(y)] for y in frozen["seasons"]):
            parser.error("freeze needs all four completed source seasons")
        for y in frozen["seasons"]:
            age.source_run(args.sources/str(y))
        path = write_settings(args.out,frozen)
        assert weekly.read_object(args.out,path.stem) == frozen
        print(json.dumps({"protocol":str(path),"sha256":path.stem}),flush=True)
        return
    if args.protocol is None or args.cache is None or args.season is None:
        parser.error("run needs protocol, cache and season")
    if weekly.read_object(args.protocol.parent,args.protocol.stem) != frozen:
        raise ValueError("age-score protocol, source, tables or implementation changed")
    root = args.out/str(args.season)
    if root.is_symlink():
        raise ValueError("season output cannot be a symlink")
    root.mkdir(exist_ok=True)
    if (root/"run.lock").is_symlink():
        raise ValueError("study lock cannot be a symlink")
    with (root/"run.lock").open("a") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("this season age-score study is already running")
        os.nice(10)
        prior = age.bootstrap(args.season,args.tables)
        data = load(args.cache,args.season)
        code_sha = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
        run = age.rehearse(root,args.sources/str(args.season),data,prior,args.protocol.stem,code_sha,
            progress=lambda v:print(json.dumps(v,allow_nan=False),flush=True))
        report = age.summarize(run,data)
        path = write_settings(root/"reports",report)
        assert weekly.read_object(root/"reports",path.stem) == report
        print(json.dumps({"report":str(path),"season":args.season,"status":report["status"],
            "checks":report["checks"],"study_passed":report["study_passed"],"release_passed":False},allow_nan=False),flush=True)


if __name__ == "__main__":
    main()
