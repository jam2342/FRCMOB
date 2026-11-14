from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.services.ratings import rp_fitting  # noqa: E402
from app.services.ratings.state_inputs import digest  # noqa: E402


def write_artifact(folder, payload):
    folder.mkdir(parents=True,exist_ok=True)
    path = folder/f"{digest(payload)}.json"
    encoded = json.dumps(payload,sort_keys=True,indent=2,allow_nan=False)+"\n"
    if path.exists() and path.read_text() != encoded:
        raise ValueError("existing RP artifact has different contents")
    if not path.exists():
        with path.open("x") as handle:
            handle.write(encoded)
    return path


def main():
    parser = argparse.ArgumentParser(description="Fit on 2022–2023, validate on 2024; no evaluation-season reads")
    parser.add_argument("--cache",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--calibration-only",action="store_true",help="Keep original state variances fixed and fit only probability calibration")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    if args.out.resolve().is_relative_to(repo):
        parser.error("RP artifacts must stay outside the repository")
    def progress(n,score):
        print(f"Training trial {n}: balanced log loss {score:.6f}",file=sys.stderr,flush=True)
    artifact = rp_fitting.fit(args.cache,progress=progress,calibration_only=args.calibration_only)
    rp_fitting.validate_artifact(artifact)
    path = write_artifact(args.out,artifact)
    # The candidate is fixed and saved before opening validation data.
    validation = rp_fitting.validate_on_2024(args.cache,artifact)
    validation_path = write_artifact(args.out,validation)
    frozen = {"schema":"flag-rp-frozen-v1","artifact_sha256":digest(artifact),"validation_sha256":digest(validation),
              "validation_passed":validation["passed"],"evaluation_seasons":[2025,2026],"serving_eligible":False}
    frozen_path = write_artifact(args.out,frozen)
    print(json.dumps({"artifact":str(path),"validation":str(validation_path),"frozen":str(frozen_path),
                      "validation_passed":validation["passed"]},indent=2))
    return 0 if validation["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
