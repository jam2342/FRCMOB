#!/usr/bin/env python3
# Re-validate the currently-deployed FRC detector against the LOCKED Einstein
# 2026 holdout. Run this:
#   * monthly as a drift tripwire,
#   * before swapping deployed weights (compare new vs current honestly),
#   * whenever pct_degraded or with_signal/findings in prod looks wrong.
#
# Uses the model the app would actually use (settings.video_tracking_yolo_model)
# unless --model is passed. Validates against
# media/datasets/frc_einstein_2026_holdout_reviewed_yolo (locked, never
# trained on).
#
# Exit 0 iff mAP@0.5 >= --min-map50. Pipe-friendly JSON to stdout.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

HOLDOUT_YAML = (
    BACKEND_ROOT
    / "media/datasets/frc_einstein_2026_holdout_reviewed_yolo/dataset.yaml"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-validate the deployed FRC detector on the locked Einstein holdout."
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override model path (default: settings.video_tracking_yolo_model).",
    )
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument(
        "--min-map50",
        type=float,
        default=0.71,
        help="Exit non-zero if mAP@0.5 falls below this. Default matches the "
        "v2 deployment benchmark (0.7172, rounded).",
    )
    args = parser.parse_args()

    if not HOLDOUT_YAML.is_file():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Locked holdout not found at {HOLDOUT_YAML}",
                }
            )
        )
        return 2

    from app.core.config import settings

    model_path = args.model or settings.video_tracking_yolo_model
    resolved = Path(model_path)
    if not resolved.is_absolute():
        resolved = BACKEND_ROOT / resolved
    if not resolved.is_file():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Model file not found: {resolved}",
                    "configured": str(model_path),
                }
            )
        )
        return 2

    from ultralytics import YOLO

    model = YOLO(str(resolved))
    metrics = model.val(
        data=str(HOLDOUT_YAML), imgsz=int(args.imgsz), verbose=False, plots=False
    )
    box = metrics.box
    map50 = float(box.map50)
    result = {
        "model": str(resolved),
        "holdout": str(HOLDOUT_YAML),
        "imgsz": int(args.imgsz),
        "map50": round(map50, 4),
        "map50_95": round(float(box.map), 4),
        "precision": round(float(box.mp), 4),
        "recall": round(float(box.mr), 4),
        "min_map50": float(args.min_map50),
        "ok": map50 >= float(args.min_map50),
    }
    if not result["ok"]:
        result["error"] = (
            f"mAP@0.5={map50:.4f} below threshold {args.min_map50}. "
            "Model has drifted or wrong weights are deployed. Investigate."
        )
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
