#!/usr/bin/env python3
"""Compare local FRC detectors on an isolated copy of a reviewed evaluation cohort.

One low-confidence validation pass per resolution supplies AP, count sweeps, and
precision/recall at the requested confidence. These are detector-only metrics:
tracking, ROI filtering and downstream pruning are not exercised. Precision at
recall is the interpolated AP envelope, not a calibrated serving threshold.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import tempfile
from typing import Any, Iterator

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVAL_DIR = BACKEND_ROOT / "media/datasets/frc_einstein_2026_holdout_reviewed_yolo"
DEFAULT_MODEL = BACKEND_ROOT / "media/models/frc_robot_detector_v2.pt"
DEFAULT_IMGSZ = (640, 896, 960, 1280)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
COUNT_CONFS = (0.25, 0.20, 0.10, 0.05, 0.01)
RECALL_TARGETS = (0.50, 0.60, 0.70, 0.80, 0.90)
EVAL_CONF = 0.001


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cohort(eval_dir: Path, split: str) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", split):
        raise ValueError("split must be a single directory name")
    image_dir = eval_dir / "images" / split
    label_dir = eval_dir / "labels" / split
    images = sorted(p for p in image_dir.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    if not images:
        raise ValueError(f"No evaluation images at {image_dir}")
    entries = []
    seen_labels: set[Path] = set()
    for image in images:
        relative = image.relative_to(image_dir)
        label = label_dir / relative.with_suffix(".txt")
        if label in seen_labels:
            raise ValueError(f"Multiple images share a label: {label}")
        seen_labels.add(label)
        # A reviewed negative frame must have an explicit empty label file. Missing
        # labels mean incomplete review, not a silently assumed negative example.
        if not label.is_file():
            raise ValueError(f"Missing reviewed label: {label}")
        boxes = 0
        for line in label.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                values = [float(v) for v in line.split()]
            except ValueError as exc:
                raise ValueError(f"Invalid YOLO label in {label}") from exc
            if (
                len(values) != 5
                or not all(math.isfinite(v) for v in values)
                or values[0] != 0
                or not all(0 <= v <= 1 for v in values[1:])
                or values[3] <= 0
                or values[4] <= 0
            ):
                raise ValueError(f"Invalid single-class YOLO box in {label}: {line}")
            boxes += 1
        entries.append({
            "image": str(relative),
            "image_sha256": _sha256(image),
            "label_sha256": _sha256(label),
            "boxes": boxes,
        })
    orphan_labels = {
        p for p in label_dir.rglob("*.txt") if p.name != "classes.txt"
    } - seen_labels
    if orphan_labels:
        raise ValueError(f"Labels without matching images: {sorted(orphan_labels)[0]}")
    return entries


def _ground_truth_density(entries: list[dict[str, Any]]) -> dict[str, Any]:
    counts = [entry["boxes"] for entry in entries]
    return {
        "frames": len(counts),
        "boxes": sum(counts),
        "boxes_per_frame": round(sum(counts) / len(counts), 3),
        "median_boxes_per_frame": statistics.median(counts),
        "negative_frames": counts.count(0),
    }


@contextmanager
def _isolated_eval(
    eval_dir: Path, split: str, entries: list[dict[str, Any]]
) -> Iterator[tuple[Path, Path]]:
    # Copy both images and labels: Ultralytics writes label caches and may repair
    # corrupt JPEGs. A symlink would let that repair modify the locked source image.
    with tempfile.TemporaryDirectory(prefix="frcmob_eval_") as temporary:
        root = Path(temporary)
        for entry in entries:
            relative = Path(entry["image"])
            for kind, name, expected in (
                ("images", relative, entry["image_sha256"]),
                ("labels", relative.with_suffix(".txt"), entry["label_sha256"]),
            ):
                target = root / kind / split / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(eval_dir / kind / split / name, target)
                if _sha256(target) != expected:
                    raise ValueError("Evaluation source changed during snapshot creation")
        yaml_path = root / "dataset.yaml"
        # JSON strings are valid quoted YAML strings, including spaces/colons.
        yaml_path.write_text(
            f"path: {json.dumps(str(root))}\ntrain: images/{split}\n"
            f"val: images/{split}\nnames:\n  0: robot\n",
            encoding="utf-8",
        )
        yield yaml_path, root


def _max_recall(box: Any) -> float:
    import numpy as np

    curve = np.asarray(box.r_curve)
    return float(curve.max()) if curve.size else 0.0


def _precision_at_recall(box: Any, targets: tuple[float, ...]) -> dict[str, float | None]:
    import numpy as np

    maximum = _max_recall(box)
    axis = np.asarray(box.px).reshape(-1)
    precision = np.asarray(box.prec_values).reshape(-1)
    if not precision.size:
        return {f"precision_at_recall_{target:.2f}": None for target in targets}
    if axis.size != precision.size:
        raise ValueError("Expected single-class precision-recall curve")
    return {
        f"precision_at_recall_{target:.2f}": (
            round(float(np.interp(target, axis, precision)), 4)
            if target <= maximum and axis.size else None
        )
        for target in targets
    }


def _capture_thresholds(
    validator: Any,
    predictions: list[Any],
    batch: dict[str, Any],
    totals: dict[float, dict[str, int]],
) -> None:
    for index, prediction in enumerate(predictions):
        truth = validator._prepare_batch(index, batch)
        prepared = validator._prepare_pred(prediction)
        for threshold, counts in totals.items():
            # Re-match after filtering. A low-confidence, higher-IoU box must not
            # steal the match from a box retained at the serving threshold.
            keep = prepared["conf"] > threshold
            filtered = {key: value[keep] for key, value in prepared.items()}
            matches = validator._process_batch(filtered, truth)["tp"]
            counts["frames"] += 1
            counts["ground_truth_boxes"] += len(truth["cls"])
            counts["detections"] += len(filtered["conf"])
            counts["true_positives"] += int(matches[:, 0].sum())


def _threshold_report(threshold: float, counts: dict[str, int]) -> dict[str, Any]:
    frames = counts["frames"]
    detections = counts["detections"]
    truth = counts["ground_truth_boxes"]
    tp = counts["true_positives"]
    return {
        "conf": threshold,
        "match_iou": 0.5,
        "frames": frames,
        "ground_truth_boxes": truth,
        "true_positives": tp,
        "false_positives": detections - tp,
        "false_negatives": truth - tp,
        "precision": round(tp / detections, 4) if detections else None,
        "recall": round(tp / truth, 4) if truth else None,
        "detections_per_frame": round(detections / frames, 3),
        "total_detections": detections,
        "count_ratio_vs_ground_truth": round(detections / truth, 3) if truth else None,
    }


def evaluate(
    model_path: Path,
    eval_dir: Path,
    split: str,
    sizes: tuple[int, ...],
    serving_conf: float,
    skip_counts: bool,
    *,
    device: str = "cpu",
    batch: int = 1,
    iou: float = 0.6,
    max_det: int = 50,
    rect: bool = False,
) -> dict[str, Any]:
    from ultralytics import YOLO, __version__ as ultralytics_version
    from ultralytics.models.yolo.detect import DetectionValidator
    import torch

    if not EVAL_CONF <= serving_conf <= 1:
        raise ValueError(f"serving confidence must be between {EVAL_CONF} and 1")
    if batch < 1 or max_det < 1 or not 0 < iou <= 1:
        raise ValueError("Invalid batch, max_det, or NMS IoU")
    if not sizes or any(size < 32 or size % 32 for size in sizes):
        raise ValueError("Image sizes must be positive multiples of 32")
    entries = _cohort(eval_dir, split)
    truth = _ground_truth_density(entries)
    if not truth["boxes"]:
        raise ValueError("Evaluation cohort has no ground-truth boxes")
    model_hash = _sha256(model_path)
    thresholds = sorted({serving_conf, *(COUNT_CONFS if not skip_counts else ())}, reverse=True)
    by_size = []
    with _isolated_eval(eval_dir, split, entries) as (data_yaml, temporary):
        for imgsz in sizes:
            totals = {
                threshold: dict(frames=0, ground_truth_boxes=0, detections=0, true_positives=0)
                for threshold in thresholds
            }

            class CapturingValidator(DetectionValidator):
                def update_metrics(self, predictions: list, batch: dict) -> None:
                    _capture_thresholds(self, predictions, batch, totals)
                    super().update_metrics(predictions, batch)

            model = YOLO(str(model_path))
            if len(model.names) != 1:
                raise ValueError("Evaluation requires a single-class robot detector")
            metrics = model.val(
                validator=CapturingValidator,
                data=str(data_yaml), imgsz=imgsz, conf=EVAL_CONF, iou=iou,
                max_det=max_det, device=device, batch=batch, workers=0,
                verbose=False, plots=False, save_json=False, save_txt=False,
                rect=rect, augment=False, half=False, cache=False,
                project=str(temporary / "runs"), name=f"size_{imgsz}", exist_ok=True,
            )
            for counts in totals.values():
                if (counts["frames"], counts["ground_truth_boxes"]) != (
                    truth["frames"], truth["boxes"]
                ):
                    raise ValueError("Validator skipped or changed reviewed images/labels")
            box = metrics.box
            values = {
                "map50": float(box.map50), "map50_95": float(box.map),
                "precision_at_best_f1": float(box.mp), "recall_at_best_f1": float(box.mr),
                "max_recall_any_threshold": _max_recall(box),
            }
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError("Validator returned non-finite metrics")
            serving = _threshold_report(serving_conf, totals[serving_conf])
            row = {
                "imgsz": imgsz,
                **{key: round(value, 4) for key, value in values.items()},
                **_precision_at_recall(box, RECALL_TARGETS),
                "at_serving_conf": serving,
                "detections_per_frame_at_serving_conf": serving["detections_per_frame"],
            }
            if not skip_counts:
                row["count_bias"] = [_threshold_report(t, totals[t]) for t in thresholds]
            by_size.append(row)
    if _sha256(model_path) != model_hash or _cohort(eval_dir, split) != entries:
        raise ValueError("Model or evaluation cohort changed during evaluation")
    return {
        "model": str(model_path), "model_sha256": model_hash,
        "eval_set": str(eval_dir), "split": split,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "serving_conf": serving_conf, "ground_truth": truth,
        "dataset_sha256": hashlib.sha256(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "cohort": entries,
        "protocol": {
            "ultralytics": ultralytics_version, "torch": torch.__version__,
            "device": device, "batch": batch, "rect": rect,
            "rectangular_validation_pad_strides": 0.5 if rect else None,
            "nms_iou": iou, "max_det": max_det, "minimum_confidence": EVAL_CONF,
            "half": False, "augment": False,
            "scope": "detector only; excludes tracker, ROI and track filters",
            "max_recall_semantics": "Observed at IoU 0.5 under this confidence floor and NMS cap",
            "precision_at_recall_semantics": "Interpolated AP envelope; null if recall unattainable",
            "threshold_semantics": "Exact retained-box counts, rematched at IoU 0.5 per threshold",
        },
        "by_resolution": by_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--compare")
    parser.add_argument("--eval-dir", default=str(DEFAULT_EVAL_DIR))
    parser.add_argument("--split", default="val")
    parser.add_argument("--imgsz", type=int, nargs="+", default=list(DEFAULT_IMGSZ))
    parser.add_argument("--serving-conf", type=float, default=0.2)
    parser.add_argument("--skip-counts", action="store_true", help="Report only the serving threshold.")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--iou", type=float, default=0.6, help="NMS IoU; use .45 for server config.")
    parser.add_argument("--max-det", type=int, default=50, help="NMS cap; use 20 for server config.")
    parser.add_argument("--rect", action="store_true", help="Rectangular validation padding; "
                        "Ultralytics adds half a stride, unlike predict()/track().")
    parser.add_argument("--out")
    args = parser.parse_args()
    sizes = tuple(sorted(set(args.imgsz)))
    eval_dir = Path(args.eval_dir)
    if not eval_dir.is_absolute():
        eval_dir = BACKEND_ROOT / eval_dir
    payload: dict[str, Any] = {"ok": True, "sizes": list(sizes)}
    try:
        if args.out and Path(args.out).resolve().is_relative_to(eval_dir.resolve()):
            raise ValueError("Evaluation reports must be written outside the evaluation dataset")
        for key, raw in (("primary", args.model), ("compare", args.compare)):
            if raw is None:
                continue
            model_path = Path(raw)
            if not model_path.is_absolute():
                model_path = BACKEND_ROOT / model_path
            payload[key] = evaluate(
                model_path.resolve(), eval_dir.resolve(), args.split, sizes,
                args.serving_conf, args.skip_counts, device=args.device, batch=args.batch,
                iou=args.iou, max_det=args.max_det, rect=args.rect,
            )
        if "compare" in payload:
            if payload["primary"]["dataset_sha256"] != payload["compare"]["dataset_sha256"]:
                raise ValueError("Compared models used different dataset contents")
            payload["delta"] = [
                {"imgsz": a["imgsz"], **{
                    key: round(a[key] - b[key], 4)
                    for key in ("map50", "map50_95", "recall_at_best_f1", "precision_at_best_f1")
                }}
                for a, b in zip(payload["primary"]["by_resolution"], payload["compare"]["by_resolution"])
            ]
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    output = json.dumps(payload, indent=2, allow_nan=False)
    print(output)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
