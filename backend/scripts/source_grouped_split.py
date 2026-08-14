#!/usr/bin/env python3
# Source-grouped train/val splitter for FRC YOLO datasets.
#
# Why this exists: the v1 model reported mAP@0.5=0.98, which was a LIE caused by
# leakage. Roboflow exports N augmented copies of each source frame, and
# consecutive video frames are near-identical. Splitting by augmented filename
# (the old behavior) puts siblings of the same scene on BOTH sides of train/val,
# so the model is scored on near-copies of what it memorized.
#
# This splitter groups by *source identity* and keeps every augmentation /
# consecutive-frame sibling of a group entirely on one side. It also handles
# Roboflow polygon labels (variable-length point lists) by converting them to
# YOLO detection boxes, and forces a single class (0 = robot).
#
# Output layout matches train_frc_yolo.py expectations:
#   <out>/images/{train,val}/*  <out>/labels/{train,val}/*  <out>/dataset.yaml
from __future__ import annotations

# ruff: noqa: E402

import argparse
import collections
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# Directories that are LOCKED test-only benchmarks. Training on these destroys
# the only honest generalization measurement we have (see each dir's
# LOCKED_TEST_ONLY.md). The splitter hard-refuses to read from them — this is
# the code-enforced backstop for the v1 leakage class of mistakes.
LOCKED_HOLDOUT_DIR_NAMES = {"frc_einstein_2026_holdout_reviewed_yolo"}


def _assert_not_locked_holdout(src: Path) -> None:
    # Refuse if src IS, is INSIDE, or CONTAINS a locked holdout directory.
    parts = {p.lower() for p in src.parts}
    if parts & {n.lower() for n in LOCKED_HOLDOUT_DIR_NAMES}:
        raise SystemExit(
            f"REFUSING: {src} is within a LOCKED test-only holdout "
            f"({LOCKED_HOLDOUT_DIR_NAMES}). It must never be used for "
            "training/splitting — it is the honest benchmark."
        )
    for locked in LOCKED_HOLDOUT_DIR_NAMES:
        if (src / locked).exists():
            raise SystemExit(
                f"REFUSING: {src} contains the LOCKED holdout '{locked}'. "
                "Point --src at a directory that does not include it."
            )
# Roboflow appends "_jpg.rf.<hash>" (and our importer adds "_<16hex>").
_RF_AUG_RE = re.compile(r"(.+?)_jpg\.rf\.[0-9a-f]+", re.IGNORECASE)
# Trailing _NNNN frame index on video-derived stems (GX010280_0277 -> GX010280).
_FRAME_IDX_RE = re.compile(r"_\d{2,}$")


def source_group_key(filename: str) -> str:
    # Collapse a filename to the identity that must NOT be split across
    # train/val: strip Roboflow aug hash, then strip the video frame index so
    # consecutive near-identical frames of one clip stay together.
    match = _RF_AUG_RE.match(filename)
    stem = match.group(1) if match else filename.rsplit(".", 1)[0]
    return _FRAME_IDX_RE.sub("", stem)


def _polygon_to_box(values: list[float]) -> str | None:
    xs, ys = values[0::2], values[1::2]
    if len(xs) < 2 or len(ys) < 2:
        return None
    x1, x2 = max(0.0, min(xs)), min(1.0, max(xs))
    y1, y2 = max(0.0, min(ys)), min(1.0, max(ys))
    bw, bh = x2 - x1, y2 - y1
    if bw <= 0.0 or bh <= 0.0:
        return None
    return f"0 {(x1 + x2) / 2.0:.6f} {(y1 + y2) / 2.0:.6f} {bw:.6f} {bh:.6f}"


def _normalize_label(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            vals = [float(v) for v in parts[1:]]
        except ValueError:
            continue
        if len(parts) == 5:
            cx, cy, bw, bh = vals
            if bw > 0.0 and bh > 0.0 and 0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0:
                out.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
        elif len(vals) % 2 == 0:
            box = _polygon_to_box(vals)
            if box:
                out.append(box)
    return out


def _find_image_label_dirs(src: Path) -> list[tuple[Path, Path]]:
    # Support both split layout (images/train, images/val) and flat
    # (images/, labels/) and Roboflow's single-"train" export.
    candidates = [
        (src / "images" / "train", src / "labels" / "train"),
        (src / "images" / "val", src / "labels" / "val"),
        (src / "train" / "images", src / "train" / "labels"),
        (src / "valid" / "images", src / "valid" / "labels"),
        (src / "images", src / "labels"),
    ]
    return [(i, lbl) for i, lbl in candidates if i.is_dir() and lbl.is_dir()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Leak-free source-grouped split for FRC YOLO datasets."
    )
    parser.add_argument("--src", required=True, help="Raw dataset root.")
    parser.add_argument("--output", required=True, help="Clean output dataset root.")
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument(
        "--min-images",
        type=int,
        default=200,
        help="Fail loudly if fewer labeled images than this are written.",
    )
    args = parser.parse_args()

    src = Path(args.src).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"--src not found: {src}")
    _assert_not_locked_holdout(src)
    pairs_dirs = _find_image_label_dirs(src)
    if not pairs_dirs:
        raise FileNotFoundError(f"No images/labels dirs found under {src}")

    # Collect every (image, label) and its source group.
    by_group: dict[str, list[tuple[Path, Path]]] = collections.defaultdict(list)
    for img_dir, lbl_dir in pairs_dirs:
        for image in sorted(img_dir.iterdir()):
            if image.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            label = lbl_dir / f"{image.stem}.txt"
            if label.is_file():
                by_group[source_group_key(image.name)].append((image, label))

    val_ratio = max(0.01, min(0.49, float(args.val_ratio)))
    # Deterministic per-GROUP assignment -> all siblings share a side.
    val_groups = {
        g
        for g in by_group
        if int(hashlib.sha1(g.encode(), usedforsecurity=False).hexdigest()[:8], 16)
        / float(0xFFFFFFFF)
        < val_ratio
    }

    out = Path(args.output).resolve()
    dirs = {
        (kind, split): out / kind / split
        for kind in ("images", "labels")
        for split in ("train", "val")
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    counts = {"train": 0, "val": 0}
    boxes = {"train": 0, "val": 0}
    skipped_no_boxes = 0
    for group, items in by_group.items():
        split = "val" if group in val_groups else "train"
        for image, label in items:
            norm = _normalize_label(label.read_text(encoding="utf-8", errors="ignore"))
            if not norm:
                skipped_no_boxes += 1
                continue
            digest = hashlib.sha1(
                str(image).encode(), usedforsecurity=False
            ).hexdigest()[:16]
            stem = f"{image.stem}_{digest}"
            shutil.copy2(image, dirs[("images", split)] / f"{stem}{image.suffix.lower()}")
            (dirs[("labels", split)] / f"{stem}.txt").write_text(
                "\n".join(norm) + "\n", encoding="utf-8"
            )
            counts[split] += 1
            boxes[split] += len(norm)

    (out / "dataset.yaml").write_text(
        "\n".join(
            [
                f"path: {out.as_posix()}",
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

    total = counts["train"] + counts["val"]
    min_images = max(0, int(args.min_images))
    ok = (
        total >= min_images
        and counts["train"] > 0
        and counts["val"] > 0
        and not (val_groups and counts["val"] == 0)
    )
    result = {
        "ok": ok,
        "output": out.as_posix(),
        "dataset_yaml": (out / "dataset.yaml").as_posix(),
        "source_groups_total": len(by_group),
        "val_groups": len(val_groups),
        "train_images": counts["train"],
        "val_images": counts["val"],
        "train_boxes": boxes["train"],
        "val_boxes": boxes["val"],
        "skipped_no_boxes": skipped_no_boxes,
        "leakage": "none_by_construction (whole source groups held out)",
        "min_images_required": min_images,
    }
    if not ok:
        result["error"] = (
            f"Split unusable: total={total} (need >= {min_images}), "
            f"train={counts['train']}, val={counts['val']}. With very few "
            "source groups a clean split can starve one side — add more "
            "diverse source data."
        )
        print(json.dumps(result, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
