from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.services.ratings import component_screen as screen  # noqa: E402
from app.services.ratings.research_rehearsal import write_settings  # noqa: E402
from app.services.ratings.state_inputs import digest  # noqa: E402


def protocol():
    folder = ROOT/"app/services/ratings"
    names = ("component_filter.py","component_screen.py","component_spec.py","research_filter.py","research_models.py",
             "research_fit.py","state_bootstrap.py","strength.py","research_eval.py","calibrations.py",
             "state_inputs.py","replay.py","research_rehearsal.py")
    return {"schema":screen.RECIPE,"policy":screen.POLICY,"specs":{str(y):{t:s.fingerprint for t,s in screen.specs(y).items()} for y in screen.SOURCES},
        "code_sha256":{**{name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in names},
                       "evaluate_component_tiers.py":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "serving_eligible":False,"development_seasons":list(range(2022,2027)),
        "limitations":["Fixed older-season total-state bootstrap, equal variance allocation and as-of component prior shares",
            "This screens a component representation, not independently MAP-fitted component hyperparameters",
            "Tier C uses numeric alliance components; robot/category likelihoods remain separate work",
            "Fixed raw win link; no production correction or weekly state/layer refits",
            "First eligible Monday is a counterfactual screen, not a simulated promotion or release verdict",
            "Rating impact uses the same as-of Tier A median/p99 anchors for both models, without candidate re-anchoring",
            "Historical corrected labels lack correction-publication timestamps; capture remains required",
            "2022 stand-in ratios came from 2026; it is a reference season, not an earlier-only bootstrap test",
            "All 2022–2026 seasons were examined already; 2027 remains the clean test and is not read"]}


def evaluate(cache,reports,year,frozen):
    path = Path(frozen)
    declared = json.loads(path.read_text())
    if path.stem != digest(declared) or declared != protocol():
        raise ValueError("component protocol or implementation changed; freeze a new experiment before reading data")
    if year not in declared["development_seasons"]:
        raise ValueError("only examined component seasons are permitted")
    # Bootstrap/context opens only earlier-year fit files and cache before this season.
    params,z,unit,hashes,limitations = screen.context(cache,reports,year)
    data = screen.load(cache,year)
    start = time.perf_counter()
    result = screen.evaluate(data,params,z_previous=z,previous_unit=unit)
    result.update(schema=screen.RECIPE,protocol_sha256=digest(declared),bootstrap_sources=hashes,
        earlier_context_sha256=digest({"z":z,"unit":unit}),limitations=declared["limitations"]+limitations,
        seconds=time.perf_counter()-start,code_sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip())
    return result


def main():
    parser = argparse.ArgumentParser(description="Freeze/evaluate a non-serving component representation screen")
    parser.add_argument("--step",required=True,choices=("freeze","evaluate"))
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--cache",type=Path)
    parser.add_argument("--reports",type=Path)
    parser.add_argument("--season",type=int,choices=range(2022,2027))
    parser.add_argument("--protocol",type=Path)
    args = parser.parse_args()
    if args.out.resolve().is_relative_to(ROOT.parent):
        parser.error("reports must stay outside the public repository")
    if args.step == "freeze":
        path = write_settings(args.out,protocol())
    else:
        if any(v is None for v in (args.cache,args.reports,args.season,args.protocol)):
            parser.error("evaluation needs cache, reports, season and the frozen protocol")
        result = evaluate(args.cache,args.reports,args.season,args.protocol)
        path = write_settings(args.out,result)
    saved = json.loads(path.read_text())
    assert path.stem == digest(saved)
    print(json.dumps({"path":str(path),"season":saved.get("season"),"serving_eligible":saved["serving_eligible"],
                      "first_eligible_cutoff":saved.get("first_eligible_cutoff")}))


if __name__ == "__main__":
    main()
