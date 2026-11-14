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

from app.services.ratings import weekly_rehearsal as rehearsal  # noqa: E402
from app.services.ratings.component_screen import load  # noqa: E402
from app.services.ratings.research_rehearsal import write_settings  # noqa: E402
from app.services.ratings.state_inputs import digest  # noqa: E402


def protocol():
    folder = ROOT/"app/services/ratings"
    names = ("weekly_rehearsal.py","fast_innovations.py","state_fitting.py","state_inputs.py","state_bootstrap.py",
             "research_filter.py","research_models.py","research_fit.py","research_win.py","research_eval.py",
             "calibrations.py","component_screen.py","component_spec.py","strength.py","replay.py","research_rehearsal.py")
    return {"schema":rehearsal.RECIPE,"policy":rehearsal.POLICY,"seasons":[2023,2024,2025,2026],"serving_eligible":False,
            "environment":{"numpy":version("numpy"),"numba":version("numba")},
            "code_sha256":{**{n:hashlib.sha256((folder/n).read_bytes()).hexdigest() for n in names},
                "rehearse_rating_weekly.py":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            "release":"blocked: historical development simulation; clean 2027 and full release checks still required"}


def main():
    parser = argparse.ArgumentParser(description="Resumable historical weekly rehearsal in a private scratch SQLite database")
    parser.add_argument("--step",required=True,choices=("freeze","run"))
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--protocol",type=Path)
    parser.add_argument("--season",type=int,choices=(2023,2024,2025,2026))
    parser.add_argument("--cache",type=Path)
    parser.add_argument("--reports",type=Path)
    parser.add_argument("--win-tables",type=Path)
    parser.add_argument("--max-weeks",type=int)
    args = parser.parse_args()
    if args.out.resolve().is_relative_to(ROOT.parent):
        parser.error("rehearsal files must stay outside the public repository")
    if args.max_weeks is not None and args.max_weeks < 1:
        parser.error("max-weeks must be positive; omit it for a full season")
    os.umask(0o077)
    args.out.mkdir(parents=True,exist_ok=True)
    if args.step == "freeze":
        path = write_settings(args.out,protocol())
        assert json.loads(path.read_text()) == protocol()
        print(json.dumps({"protocol":str(path),"sha256":path.stem}))
        return
    if any(v is None for v in (args.protocol,args.season,args.cache,args.reports,args.win_tables)):
        parser.error("run needs protocol, season, cache, reports and win-tables")
    frozen = json.loads(args.protocol.read_text())
    if args.protocol.stem != digest(frozen) or frozen != protocol():
        raise ValueError("weekly rehearsal protocol or implementation changed")
    root = args.out/str(args.season)
    if root.is_symlink():
        raise ValueError("season rehearsal folder cannot be a symlink")
    root.mkdir(exist_ok=True)
    if (root/"run.lock").is_symlink():
        raise ValueError("rehearsal lock cannot be a symlink")
    with (root/"run.lock").open("a") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("this season rehearsal is already running")
        os.nice(10)
        artifact = rehearsal.make_bootstrap(args.season,args.reports,args.win_tables)
        data = load(args.cache,args.season)
        previous = load(args.cache,args.season-1)
        code_sha = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
        run = rehearsal.rehearse(root,data,previous,artifact,args.protocol.stem,code_sha,max_weeks=args.max_weeks,
            progress=lambda value:print(json.dumps(value,allow_nan=False),flush=True))
        report = rehearsal.summarize(root,data,run,artifact)
        path = write_settings(root/"reports",report)
        saved = json.loads(path.read_text())
        assert path.stem == digest(saved)
        print(json.dumps({"report":str(path),"season":args.season,"status":saved["status"],"checks":saved["checks"],
                          "weekly_updates":saved["weekly_updates"],"rollbacks":saved["rollbacks"],"release_passed":False},allow_nan=False),flush=True)


if __name__ == "__main__":
    main()
