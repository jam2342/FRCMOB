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
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.services.ratings import probability_rehearsal as p, weekly_rehearsal as w  # noqa: E402
from app.services.ratings.component_screen import load  # noqa: E402
from app.services.ratings.research_rehearsal import write_settings  # noqa: E402
from app.services.ratings.state_inputs import digest  # noqa: E402


def protocol():
    folder = ROOT/"app/services/ratings"
    names = ("probability_rehearsal.py","weekly_rehearsal.py","state_fitting.py","calibrations.py",
             "research_win.py","research_rehearsal.py","research_eval.py","component_screen.py",
             "state_bootstrap.py","state_inputs.py")
    return {"schema":p.RECIPE,"policy":p.POLICY,"seasons":[2023,2024,2025,2026],"serving_eligible":False,
            "environment":{"numpy":version("numpy")},"code_sha256":{
                **{n:hashlib.sha256((folder/n).read_bytes()).hexdigest() for n in names},
                "rehearse_rating_probability.py":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}


def main():
    parser = argparse.ArgumentParser(description="Probability-only weekly replay on saved fixed-state forecasts")
    parser.add_argument("--step",required=True,choices=("freeze","run"))
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--protocol",type=Path)
    parser.add_argument("--source",type=Path)
    parser.add_argument("--season",type=int,choices=(2023,2024,2025,2026))
    parser.add_argument("--cache",type=Path)
    parser.add_argument("--reports",type=Path)
    parser.add_argument("--win-tables",type=Path)
    parser.add_argument("--max-weeks",type=int)
    args = parser.parse_args()
    if args.out.resolve().is_relative_to(ROOT.parent):
        parser.error("research outputs must stay outside the public repository")
    if args.max_weeks is not None and args.max_weeks < 1:
        parser.error("max-weeks must be positive")
    os.umask(0o077)
    args.out.mkdir(parents=True,exist_ok=True)
    if args.step == "freeze":
        path = write_settings(args.out,protocol())
        assert digest(json.loads(path.read_text())) == path.stem
        print(json.dumps({"protocol":str(path),"sha256":path.stem}))
        return
    if any(v is None for v in (args.protocol,args.source,args.season,args.cache,args.reports,args.win_tables)):
        parser.error("run needs protocol, source, season, cache, reports and win-tables")
    frozen = json.loads(args.protocol.read_text())
    if args.protocol.stem != digest(frozen) or frozen != protocol():
        raise ValueError("probability rehearsal protocol or implementation changed")
    root = args.out/str(args.season)
    if root.is_symlink():
        raise ValueError("probability output folder cannot be a symlink")
    root.mkdir(exist_ok=True)
    if (root/"run.lock").is_symlink():
        raise ValueError("probability lock cannot be a symlink")
    with (root/"run.lock").open("a") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("this season probability rehearsal is already running")
        data = load(args.cache,args.season)
        artifact = w.make_bootstrap(args.season,args.reports,args.win_tables)
        frames,lineage = p.source_run(args.source/str(args.season),data,artifact)
        identity = {"schema":p.RECIPE,"season":args.season,"protocol_sha256":args.protocol.stem,**lineage}
        source = {r["result"].card.match_key:r for r in data.records}
        run = p.rehearse(root,frames,source,artifact["layers"],identity,max_weeks=args.max_weeks,
                         progress=lambda v:print(json.dumps(v,allow_nan=False),flush=True))
        if lineage["source_journals"] != w.journal_hashes(args.source/str(args.season),list(lineage["source_journals"])):
            raise ValueError("probability replay changed original state forecasts")
        report = p.summarize(run,source)
        path = write_settings(root/"reports",report)
        saved = w.read_object(root/"reports",path.stem)
        print(json.dumps({"report":str(path),"status":saved["status"],"checks":saved["checks"],
                          "weekly_updates":saved["weekly_updates"],"rollbacks":saved["rollbacks"],"release_passed":False}),flush=True)


if __name__ == "__main__":
    main()
