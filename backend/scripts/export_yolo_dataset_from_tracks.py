#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path
import sys

import cv2

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import models
from app.db.session import SessionLocal
from app.services.vision.video_extraction import MEDIA_ROOT


def _safe_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except Exception:
        return float(default)


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _split_bucket(key: str, val_ratio: float) -> str:
    digest = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()
    bucket = int(digest[:8], 16) / float(0xFFFFFFFF)
    return "val" if bucket < val_ratio else "train"


def _build_frame_path_by_key(db, event_key: str | None, run_limit: int | None) -> dict[tuple[int, int], Path]:
    query = (
        db.query(models.Artifact)
        .filter(models.Artifact.kind == "sample_frame")
        .order_by(models.Artifact.analysis_run_id.asc(), models.Artifact.id.asc())
    )
    if event_key:
        query = query.filter(models.Artifact.analysis_run_id.in_(
            db.query(models.AnalysisRun.id).filter(models.AnalysisRun.match_key.like(f"{event_key}%"))
        ))
    if run_limit and run_limit > 0:
        run_ids = (
            db.query(models.Artifact.analysis_run_id)
            .filter(models.Artifact.kind == "sample_frame")
            .distinct()
            .order_by(models.Artifact.analysis_run_id.desc())
            .limit(run_limit)
            .all()
        )
        run_id_values = [int(item[0]) for item in run_ids]
        query = query.filter(models.Artifact.analysis_run_id.in_(run_id_values))

    mapping: dict[tuple[int, int], Path] = {}
    for artifact in query.all():
        meta = artifact.meta if isinstance(artifact.meta, dict) else {}
        index_raw = meta.get("index")
        if not isinstance(index_raw, (int, float)):
            continue
        frame_index = int(index_raw) - 1
        if frame_index < 0:
            continue
        path = Path(str(artifact.path or "").strip())
        if not path:
            continue
        full_path = path if path.is_absolute() else (MEDIA_ROOT / path)
        mapping[(int(artifact.analysis_run_id), frame_index)] = full_path
    return mapping


def _yolo_label_line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: int,
    height: int,
) -> str | None:
    if width <= 0 or height <= 0:
        return None
    x1 = max(0.0, min(float(width), x1))
    x2 = max(0.0, min(float(width), x2))
    y1 = max(0.0, min(float(height), y1))
    y2 = max(0.0, min(float(height), y2))
    if x2 <= x1 or y2 <= y1:
        return None
    cx = _clip01(((x1 + x2) / 2.0) / float(width))
    cy = _clip01(((y1 + y2) / 2.0) / float(height))
    bw = _clip01((x2 - x1) / float(width))
    bh = _clip01((y2 - y1) / float(height))
    if bw <= 0.0 or bh <= 0.0:
        return None
    return f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export YOLO dataset from robot_tracks bounding boxes.")
    parser.add_argument("--output", default=str((MEDIA_ROOT / "datasets" / "frc_yolo").resolve()))
    parser.add_argument("--event-key", default="", help="Optional event key filter, e.g. 2025txhou")
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--run-limit", type=int, default=0, help="Optional latest run cap.")
    parser.add_argument("--only-yolo", action="store_true", help="Use only tracks produced by yolo_bytetrack.")
    parser.add_argument(
        "--min-images",
        type=int,
        default=200,
        help="Fail loudly if fewer than this many labeled images are exported "
        "(an empty/tiny dataset trains a useless model). Set 0 to disable.",
    )
    args = parser.parse_args()

    output_root = Path(args.output).resolve()
    images_train = output_root / "images" / "train"
    images_val = output_root / "images" / "val"
    labels_train = output_root / "labels" / "train"
    labels_val = output_root / "labels" / "val"
    for path in (images_train, images_val, labels_train, labels_val):
        path.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        event_key = args.event_key.strip() or None
        run_limit = int(args.run_limit) if args.run_limit and int(args.run_limit) > 0 else None
        frame_path_by_key = _build_frame_path_by_key(db, event_key, run_limit)

        query = db.query(models.RobotTrack).filter(
            models.RobotTrack.bbox_x1.isnot(None),
            models.RobotTrack.bbox_y1.isnot(None),
            models.RobotTrack.bbox_x2.isnot(None),
            models.RobotTrack.bbox_y2.isnot(None),
            models.RobotTrack.frame_index.isnot(None),
        )
        if event_key:
            query = query.filter(models.RobotTrack.event_key == event_key)
        if run_limit:
            run_rows = (
                db.query(models.RobotTrack.analysis_run_id)
                .distinct()
                .order_by(models.RobotTrack.analysis_run_id.desc())
                .limit(run_limit)
                .all()
            )
            run_ids = [int(row[0]) for row in run_rows]
            query = query.filter(models.RobotTrack.analysis_run_id.in_(run_ids))
        if args.only_yolo:
            query = query.filter(models.RobotTrack.source == "yolo_bytetrack")

        labels_by_image: dict[Path, list[str]] = defaultdict(list)
        image_dims: dict[Path, tuple[int, int]] = {}
        skipped_missing_frames = 0
        skipped_invalid_boxes = 0

        for row in query.all():
            key = (int(row.analysis_run_id), int(row.frame_index))
            frame_path = frame_path_by_key.get(key)
            if frame_path is None or not frame_path.exists():
                skipped_missing_frames += 1
                continue
            dims = image_dims.get(frame_path)
            if dims is None:
                image = cv2.imread(str(frame_path))
                if image is None:
                    skipped_missing_frames += 1
                    continue
                height, width = image.shape[:2]
                dims = (int(width), int(height))
                image_dims[frame_path] = dims
            width, height = dims
            label_line = _yolo_label_line(
                _safe_float(row.bbox_x1),
                _safe_float(row.bbox_y1),
                _safe_float(row.bbox_x2),
                _safe_float(row.bbox_y2),
                width,
                height,
            )
            if label_line is None:
                skipped_invalid_boxes += 1
                continue
            labels_by_image[frame_path].append(label_line)

        exported_images = 0
        exported_labels = 0
        for source_image, label_lines in labels_by_image.items():
            if not label_lines:
                continue
            split = _split_bucket(str(source_image), max(0.01, min(0.49, float(args.val_ratio))))
            target_images_dir = images_val if split == "val" else images_train
            target_labels_dir = labels_val if split == "val" else labels_train
            suffix = source_image.suffix.lower() or ".jpg"
            image_hash = hashlib.sha1(
                str(source_image).encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()[:16]
            stem = f"{source_image.stem}_{image_hash}"
            target_image = target_images_dir / f"{stem}{suffix}"
            target_label = target_labels_dir / f"{stem}.txt"

            shutil.copy2(source_image, target_image)
            unique_lines = sorted(set(label_lines))
            target_label.write_text("\n".join(unique_lines) + "\n", encoding="utf-8")
            exported_images += 1
            exported_labels += len(unique_lines)

        dataset_yaml = output_root / "dataset.yaml"
        dataset_yaml.write_text(
            "\n".join(
                [
                    f"path: {output_root.as_posix()}",
                    "train: images/train",
                    "val: images/val",
                    "",
                    "names:",
                    "  0: robot",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        min_images = max(0, int(args.min_images))
        dataset_ok = min_images == 0 or exported_images >= min_images
        result = {
            "ok": dataset_ok,
            "output": output_root.as_posix(),
            "dataset_yaml": dataset_yaml.as_posix(),
            "exported_images": exported_images,
            "exported_labels": exported_labels,
            "skipped_missing_frames": skipped_missing_frames,
            "skipped_invalid_boxes": skipped_invalid_boxes,
            "event_key": event_key,
            "only_yolo": bool(args.only_yolo),
            "min_images_required": min_images,
        }
        if not dataset_ok:
            result["error"] = (
                f"Only {exported_images} labeled images exported "
                f"(need >= {min_images}). Training on this would produce a "
                "useless detector. This is the bootstrap paradox: RobotTrack "
                "rows are produced BY the YOLO model, so with no FRC model "
                "there are ~no tracks to learn from. Seed the first dataset "
                "from external labels (see scripts/import_external_yolo_labels.py "
                "and backend/scripts/TRAINING.md), then retrain and let the "
                "self-improving loop take over."
            )
            print(json.dumps(result, indent=2))
            return 1
        print(json.dumps(result, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
