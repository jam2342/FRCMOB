from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.services.ratings import rp_fitting as fitting, rp_weekly as weekly, rp_comparison as live  # noqa: E402
from app.services.ratings.flag_rp import FlagRpParams  # noqa: E402
from app.services.ratings.state_inputs import digest  # noqa: E402


def evaluate(cache, season, *, priors_path=None, snapshots=None):
    data = fitting.load_published(cache,season)
    parity,baseline = None,None
    coverage = {"matches":len(data.matches),"skipped":data.skipped}
    if season == 2026:
        if priors_path is None or snapshots is None:
            raise ValueError("2026 evaluation requires production priors and served snapshots")
        research,metadata,source = live.load_inputs(cache)
        priors = live.read_priors(priors_path)
        parity = live.served_parity(live.reproduce(research,live.live_model(research,priors)),snapshots)
        if not parity["passed"]:
            raise ValueError("production RP reproduction failed")
        common,coverage = live.replay_comparison(research,metadata,live.live_model(research,priors))
        rows = [{"key":k,"p":r["flag"],**r} for k,r in common.items()]
        baseline = [{**r,"p":r["live"]} for r in rows]
        coverage["live_source_sha256"] = source
    else:
        rows = fitting.forecasts(data,FlagRpParams())
    probabilities,timeline = weekly.walk_forward(data,rows)
    starts = {}
    for r in rows:
        starts[r["event"]] = min(starts.get(r["event"],r["time"]),r["time"])
    newest = set(sorted(starts,key=lambda e:(starts[e],e))[-max(1,(len(starts)+4)//5):])
    first = min(starts.values(),default=0)
    types = {card.event_key:event_type for card,_,_,event_type in data.matches}
    slices = {"all":lambda r:True,"first_7_days":lambda r:r["time"] < first+604800,
              "first_21_days":lambda r:r["time"] < first+3*604800,"newest_events":lambda r:r["event"] in newest,
              "championships":lambda r:types[r["event"]] in (2,3,4,5)}
    comparisons = {}
    for name,select in slices.items():
        chosen = [r for r in rows if select(r)]
        comparisons[name] = {"weekly_minus_raw":fitting.compare(chosen,chosen,calibrated_rows=probabilities)}
        if baseline is not None:
            comparisons[name]["weekly_minus_live"] = fitting.compare([r for r in baseline if select(r)],chosen,calibrated_rows=probabilities)
    return {"schema":weekly.RECIPE,"season":season,"policy":weekly.POLICY,"state_params":asdict(FlagRpParams()),
        "implementation_sha256":weekly.implementation_sha256(),"source_sha256":data.source_sha256,
        "spec_sha256":data.spec.fingerprint,"forecast_sha256":digest(rows),"corrected_sha256":digest(probabilities),
        "coverage":coverage,"parity":parity,"comparisons":comparisons,"timeline":timeline,"serving_eligible":False,
        "limitations":["Historical development replay on already examined seasons; 2027 remains the clean test",
            "Each RP starts at identity every season; only labels posted strictly before its Monday fit can train it",
            "Final cached corrections lack historical publication times; these are not prospective captured forecasts",
            "2026 snapshot parity validates the live reproduction, not historical upcoming forecasts",
            "No serving integration, parameter search, acceptance verdict or automatic activation"]}


def main():
    parser = argparse.ArgumentParser(description="Fixed weekly per-RP calibration on examined seasons; cached inputs only")
    parser.add_argument("--cache",required=True,type=Path)
    parser.add_argument("--season",required=True,type=int,choices=range(2022,2027))
    parser.add_argument("--priors-2025",type=Path)
    parser.add_argument("--snapshots",type=Path)
    args = parser.parse_args()
    result = evaluate(args.cache,args.season,priors_path=args.priors_2025,snapshots=args.snapshots)
    result["code_sha"] = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__ == "__main__":
    main()
