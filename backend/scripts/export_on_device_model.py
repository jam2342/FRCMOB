#!/usr/bin/env python3
# Export the FRC detector to the ONNX the PWA loads.
#
# Two things here are not obvious and cost real time to rediscover:
#
# 1. --opset 19. Ultralytics defaults to a much newer opset (22 on 8.4.14), and
#    onnxruntime-web's WebGPU backend has no fast kernels for parts of it -- it silently
#    falls back and the graph runs ~19x slower. Measured on an Apple M2, same weights,
#    same 640 input: opset 12 -> 52 ms a frame, opset 22 -> 1133 ms, with near-identical
#    graphs (353 vs 355 nodes), so nothing warns you. Opsets 12/13/15/17/19 all run at
#    ~53 ms, so 19 is the safe ceiling -- and it is above the 13 that per-channel
#    quantisation would need, if anyone revisits int8.
#
# 2. --imgsz is the single biggest lever on accuracy, and 640 is a bad default for this
#    game. Robots are small in a wide field shot, so halving resolution guts recall. On
#    the locked Einstein holdout, square-letterboxed exactly as the browser feeds it:
#
#        imgsz   mAP@0.5   recall
#          640    0.3402   0.2347   <- what the PWA shipped with
#          768    0.5349   0.4150
#          896    0.6058   0.4844
#         1024    0.6472   0.5250
#         1280    0.7189   0.5877   <- the number the docs quote
#
#    In-browser WebGPU cost scales about linearly with pixels: 640 -> 53 ms,
#    896 -> 102 ms, 1024 -> 153 ms. A 3 fps sample interval allows 333 ms and optical
#    flow takes ~46 ms of it, so 896 buys +78% mAP for +49 ms and still leaves room for
#    a phone being slower than this Mac.
#
# Exports beside a COPY of the weights, never the weights themselves: ultralytics writes
# its output next to the source .pt, which will happily overwrite a model you meant to keep.
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS = BACKEND_ROOT / "media/models/frc_robot_detector_v2.pt"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the on-device detector ONNX.")
    parser.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    parser.add_argument("--imgsz", type=int, default=896)
    parser.add_argument(
        "--opset",
        type=int,
        default=19,
        help="Do not exceed 19 without re-benchmarking WebGPU. See the note above.",
    )
    parser.add_argument("--out", required=True, help="Destination .onnx path.")
    args = parser.parse_args()

    weights = Path(args.weights)
    if not weights.is_file():
        print(f"weights not found: {weights}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    from ultralytics import YOLO

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / weights.name
        shutil.copy2(weights, staged)
        produced = Path(
            YOLO(str(staged)).export(
                format="onnx",
                imgsz=int(args.imgsz),
                opset=int(args.opset),
                simplify=True,
                dynamic=False,
            )
        )
        shutil.move(str(produced), out)

    size_mb = out.stat().st_size / 1_000_000
    print(f"wrote {out} ({size_mb:.1f} MB, imgsz={args.imgsz}, opset={args.opset})")
    print("Verify accuracy before shipping it:")
    print(f"  PYTHONPATH=. python scripts/verify_holdout.py --model {weights} --imgsz {args.imgsz}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
