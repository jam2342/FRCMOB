#!/usr/bin/env python3
# Seed the FRC detector training set from an EXTERNAL YOLO-format dataset.
#
# Why this exists — the bootstrap paradox:
#   export_yolo_dataset_from_tracks.py builds labels from RobotTrack rows, but
#   those rows are produced BY the YOLO model. With no FRC model, tracking uses
#   generic yolo11n.pt, finds ~no robots, writes ~no tracks -> empty dataset ->
#   can't train. You must break the cycle with labels from outside the pipeline:
#   Roboflow export, CVAT, label-studio, or hand-annotated frames.
#
# Input: a standard YOLO detection dataset directory:
#     <src>/images/{train,val}/*.jpg|png
#     <src>/labels/{train,val}/*.txt        (YOLO: "<cls> cx cy w h", normalized)
# Single-class is assumed (class id is remapped to 0 = "robot").
#
# Output: merged into the SAME layout train_frc_yolo.py consumes, so after
# importing you run the normal train step. Once a v1 model exists, the
# self-improving loop (analyze matches -> RobotTrack -> export_yolo_dataset)
# takes over and external labels become optional.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.vision.video_extraction import MEDIA_ROOT

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _remap_label_text(raw: str) -> str | None:
    # Force every box to class 0 (single "robot" class) and validate geometry.
    out: list[str] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            _, cx, cy, bw, bh = parts
            vals = [float(cx), float(cy), float(bw), float(bh)]
        except ValueError:
            continue
        if any(not (0.0 <= v <= 1.0) for v in vals[:2]):
            continue
        if vals[2] <= 0.0 or vals[3] <= 0.0:
            continue
        out.append(f"0 {vals[0]:.6f} {vals[1]:.6f} {vals[2]:.6f} {vals[3]:.6f}")
    return ("\n".join(out) + "\n") if out else None


def _iter_split(src: Path, split: str):
    img_dir = src / "images" / split
    lbl_dir = src / "labels" / split
    if not img_dir.is_dir():
        return
    for image in sorted(img_dir.iterdir()):
        if image.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        label = lbl_dir / f"{image.stem}.txt"
        if not label.is_file():
            continue
        yield image, label


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import an external YOLO-format dataset to seed FRC training."
    )
    parser.add_argument("--src", required=True, help="External YOLO dataset root.")
    parser.add_argument(
        "--output",
        default=str((MEDIA_ROOT / "datasets" / "frc_yolo").resolve()),
        help="Target dataset root (same one train_frc_yolo.py uses).",
    )
    parser.add_argument(
        "--default-split",
        choices=["train", "val"],
        default="train",
        help="Where to put images if the source has no train/val split.",
    )
    args = parser.parse_args()

    src = Path(args.src).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"Source dataset not found: {src}")
    # Same code-enforced lock as the splitter: never import a test-only holdout
    # as training data.
    from scripts.source_grouped_split import _assert_not_locked_holdout

    _assert_not_locked_holdout(src)

    output_root = Path(args.output).resolve()
    dirs = {
        ("images", "train"): output_root / "images" / "train",
        ("images", "val"): output_root / "images" / "val",
        ("labels", "train"): output_root / "labels" / "train",
        ("labels", "val"): output_root / "labels" / "val",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    splits = ["train", "val"]
    has_split_layout = any((src / "images" / s).is_dir() for s in splits)
    pairs: list[tuple[Path, Path, str]] = []
    if has_split_layout:
        for split in splits:
            for image, label in _iter_split(src, split):
                pairs.append((image, label, split))
    else:
        # Flat layout: <src>/images/*, <src>/labels/*
        img_dir = src / "images"
        lbl_dir = src / "labels"
        if img_dir.is_dir():
            for image in sorted(img_dir.iterdir()):
                if image.suffix.lower() not in _IMAGE_SUFFIXES:
                    continue
                label = lbl_dir / f"{image.stem}.txt"
                if label.is_file():
                    pairs.append((image, label, args.default_split))

    imported = 0
    boxes = 0
    skipped_empty_labels = 0
    for image, label, split in pairs:
        remapped = _remap_label_text(label.read_text(encoding="utf-8", errors="ignore"))
        if remapped is None:
            skipped_empty_labels += 1
            continue
        digest = hashlib.sha1(str(image).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        stem = f"ext_{image.stem}_{digest}"
        suffix = image.suffix.lower() or ".jpg"
        shutil.copy2(image, dirs[("images", split)] / f"{stem}{suffix}")
        (dirs[("labels", split)] / f"{stem}.txt").write_text(remapped, encoding="utf-8")
        imported += 1
        boxes += remapped.count("\n")

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

    ok = imported > 0
    print(
        json.dumps(
            {
                "ok": ok,
                "src": src.as_posix(),
                "output": output_root.as_posix(),
                "dataset_yaml": dataset_yaml.as_posix(),
                "imported_images": imported,
                "imported_boxes": boxes,
                "skipped_empty_labels": skipped_empty_labels,
                "error": None if ok else "No valid image/label pairs found in --src.",
            },
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
