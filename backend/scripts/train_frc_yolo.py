#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Train an FRC-specific YOLO detector.")
    parser.add_argument("--data", required=True, help="Path to YOLO dataset yaml.")
    parser.add_argument("--base-model", default="yolo11s.pt")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--batch", type=int, default=12)
    parser.add_argument("--device", default="")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--project", default="media/models/yolo_runs")
    parser.add_argument("--name", default="frc_robot_detector")
    parser.add_argument("--output-model", default="media/models/frc_robot_detector_v1.pt")
    parser.add_argument(
        "--min-map50",
        type=float,
        default=0.30,
        help="Refuse to publish the model unless validation mAP@0.5 reaches "
        "this. Guards against shipping a detector that finds nothing. Set 0 "
        "to disable (NOT recommended for production weights).",
    )
    args = parser.parse_args()

    try:
        from ultralytics import YOLO  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("ultralytics is required to train YOLO models.") from exc

    data_path = Path(args.data).resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset yaml not found: {data_path}")

    project_path = Path(args.project).resolve()
    project_path.mkdir(parents=True, exist_ok=True)
    output_model = Path(args.output_model).resolve()
    output_model.parent.mkdir(parents=True, exist_ok=True)

    model = YOLO(str(args.base_model))
    train_kwargs = {
        "data": str(data_path),
        "epochs": int(max(1, args.epochs)),
        "imgsz": int(max(320, args.imgsz)),
        "batch": int(max(1, args.batch)),
        "project": str(project_path),
        "name": str(args.name),
        "workers": int(max(1, args.workers)),
        "exist_ok": True,
        "verbose": True,
    }
    if str(args.device).strip():
        train_kwargs["device"] = str(args.device).strip()

    result = model.train(**train_kwargs)
    save_dir = Path(str(getattr(result, "save_dir", ""))).resolve()
    best_weight = save_dir / "weights" / "best.pt"
    last_weight = save_dir / "weights" / "last.pt"
    source_weight = best_weight if best_weight.exists() else last_weight
    if not source_weight.exists():
        raise FileNotFoundError(f"No trained weights found in {save_dir}")

    # Quality gate: validate the trained weights before publishing. A model
    # that doesn't actually detect robots is worse than the honest DEGRADED
    # flag, because it looks "successful" while producing empty findings.
    min_map50 = max(0.0, float(args.min_map50))
    map50 = None
    if min_map50 > 0.0:
        try:
            trained = YOLO(str(source_weight))
            metrics = trained.val(data=str(data_path), imgsz=int(max(320, args.imgsz)), verbose=False)
            map50 = float(getattr(getattr(metrics, "box", metrics), "map50", 0.0) or 0.0)
        except Exception as exc:  # pragma: no cover - validation best-effort
            print(
                json.dumps(
                    {"ok": False, "error": f"post-train validation failed: {exc}"},
                    indent=2,
                )
            )
            return 1
        if map50 < min_map50:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": (
                            f"validation mAP@0.5={map50:.4f} below threshold "
                            f"{min_map50:.2f}. Refusing to publish "
                            f"{output_model.name} — it would ship a detector "
                            "that barely works. Add more/better labeled data "
                            "or train longer, then retry."
                        ),
                        "save_dir": save_dir.as_posix(),
                        "map50": round(map50, 4),
                        "min_map50": min_map50,
                    },
                    indent=2,
                )
            )
            return 1

    shutil.copy2(source_weight, output_model)
    print(
        json.dumps(
            {
                "ok": True,
                "data": data_path.as_posix(),
                "save_dir": save_dir.as_posix(),
                "source_weight": source_weight.as_posix(),
                "output_model": output_model.as_posix(),
                "map50": round(map50, 4) if map50 is not None else None,
                "min_map50": min_map50,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
