from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.services.ratings import rp_fitting as fitting, rp_comparison as live  # noqa: E402
from app.services.ratings.component_spec import get_spec  # noqa: E402
from app.services.ratings.flag_rp import FlagRpModel, FlagRpParams  # noqa: E402
from app.services.ratings.state_inputs import digest  # noqa: E402


def load_frozen(path):
    path = Path(path)
    frozen = json.loads(path.read_text())
    if frozen["schema"] != "flag-rp-frozen-v1" or frozen["evaluation_seasons"] != [2025,2026] or frozen["serving_eligible"] is not False:
        raise ValueError("unsupported frozen RP recipe")
    if path.stem != digest(frozen):
        raise ValueError("frozen RP content hash mismatch")
    artifact = json.loads((path.parent/f"{frozen['artifact_sha256']}.json").read_text())
    validation = json.loads((path.parent/f"{frozen['validation_sha256']}.json").read_text())
    if digest(artifact) != frozen["artifact_sha256"] or digest(validation) != frozen["validation_sha256"] or validation["artifact_sha256"] != digest(artifact):
        raise ValueError("RP artifact/validation hash mismatch")
    fitting.validate_artifact(artifact)
    if frozen["validation_passed"] is not True or validation["passed"] is not True or validation["season"] != 2024 or not all(validation["guards"].values()):
        raise ValueError("2024 validation failed; later-season evaluation is blocked")
    return artifact,validation


def evaluate(cache, season, frozen_path, *, priors_path=None, snapshots=None):
    # Verify the frozen recipe before opening any evaluation-season data.
    artifact,validation = load_frozen(frozen_path)
    if season not in (2025,2026):
        raise ValueError("only the frozen examined-season evaluation is allowed")
    params,calibration = fitting.validate_artifact(artifact)
    parity = None
    if season == 2026:
        if priors_path is None or snapshots is None:
            raise ValueError("2026 evaluation requires production priors and served snapshots")
        research,metadata,source = live.load_inputs(cache)
        priors = live.read_priors(priors_path)
        parity = live.served_parity(live.reproduce(research,live.live_model(research,priors)),snapshots)
        if not parity["passed"]:
            raise ValueError("production RP reproduction failed")
        rows,coverage = live.replay_comparison(research,metadata,live.live_model(research,priors),challenger=FlagRpModel(get_spec(2026),params))
        old = [{"key":k,"p":r["flag"],**r} for k,r in rows.items()]
        raw = [{**r,"p":r["fitted_flag"]} for r in old]
        baseline = [{**r,"p":r["live"]} for r in old]
    else:
        data = fitting.load_published(cache,season)
        source = data.source_sha256
        old,raw = fitting.forecasts(data,FlagRpParams()),fitting.forecasts(data,params)
        baseline = None
        coverage = {"matches":len(data.matches),"skipped":data.skipped}
    calibrated = {r["key"]:fitting.calibrated(r["p"],r["base"],calibration) for r in raw}
    comparisons = {"raw_fitted_minus_default":fitting.compare(old,raw),
        "calibrated_minus_default":fitting.compare(old,raw,calibrated_rows=calibrated),
        "calibrated_minus_raw":fitting.compare(raw,raw,calibrated_rows=calibrated)}
    if baseline is not None:
        comparisons["calibrated_minus_live"] = fitting.compare(baseline,raw,calibrated_rows=calibrated)
    starts = {}
    for r in old:
        starts[r["event"]] = min(starts.get(r["event"],r["time"]),r["time"])
    newest = set(sorted(starts,key=lambda e:(starts[e],e))[-max(1,(len(starts)+4)//5):])
    first = min(starts.values(),default=0)
    slices = {"first_7_days":lambda r:r["time"] < first+604800,
              "first_21_days":lambda r:r["time"] < first+3*604800,
              "newest_events":lambda r:r["event"] in newest}
    by_slice = {}
    for name,select in slices.items():
        a,b = [r for r in old if select(r)],[r for r in raw if select(r)]
        by_slice[name] = {"calibrated_minus_default":fitting.compare(a,b,calibrated_rows=calibrated)}
        if baseline is not None:
            by_slice[name]["calibrated_minus_live"] = fitting.compare([r for r in baseline if select(r)],b,calibrated_rows=calibrated)
    return {"season":season,"artifact_sha256":digest(artifact),"validation_sha256":digest(validation),
        "source_sha256":source,"parity":parity,"coverage":coverage,"comparisons":comparisons,"slices":by_slice,
        "forecast_sha256":digest(raw),"serving_eligible":False,
        "limitations":["Fixed 2022–2023 fit checked on 2024; no tuning on this evaluation season",
            "2025 and 2026 are already examined development data; 2027 remains the clean test",
            "Publication-order replay and final cached corrections do not prove historical served-path parity",
            "No prospective RP capture, serving integration or release verdict"]}


def main():
    parser = argparse.ArgumentParser(description="Evaluate a hashed RP recipe only after its 2024 validation passed")
    parser.add_argument("--frozen",required=True,type=Path)
    parser.add_argument("--cache",required=True,type=Path)
    parser.add_argument("--season",required=True,type=int,choices=(2025,2026))
    parser.add_argument("--priors-2025",type=Path)
    parser.add_argument("--snapshots",type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.cache,args.season,args.frozen,priors_path=args.priors_2025,snapshots=args.snapshots),indent=2,allow_nan=False))


if __name__ == "__main__":
    main()
