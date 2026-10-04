#!/usr/bin/env python3
# Assemble the v3 training corpus from the existing datasets, honestly.
#
# Three things about the v2 corpus that only show up when you count it:
#
# 1. It is 4x smaller than it looks. frc_yolo_v2 presents 6,540 images, but they are
#    Roboflow augmentation copies of 1,635 unique source frames -- four copies each,
#    distinguished only by a ".rf.<hash>" suffix. Training on all four teaches the same
#    frame four times; ultralytics already applies its own augmentation on top.
#
# 2. Its split threw away half the data. 814 unique base frames in train, 821 in val --
#    a 50/50 split. (Checked: no base frame straddles the two, so there is no leakage,
#    but nearly half the unique frames were never trained on.)
#
# 3. 15% of it is simulator renders, which TRAINING.md's own v3 recipe rules out.
#    Kept here in a separate bucket so their contribution can be measured rather than
#    assumed.
#
# Output is deduplicated, source-grouped, and split so that no two frames from the same
# camera view straddle train/val. Labels are copied verbatim -- completing them is
# propose_missing_labels.py's job, and it wants this dataset as its input.
#
#   PYTHONPATH=. python scripts/build_v3_dataset.py --out media/datasets/frc_v3_base
from __future__ import annotations

# ruff: noqa: E402

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.dataset_safety import (
    assert_fresh_output,
    assert_not_holdout,
    dataset_images,
    overlaps,
)

DEFAULT_SOURCES = (
    "media/datasets/frc_yolo_v2",
    "media/datasets/frc_v2_roboflow_reviewed_grouped",
)

# Roboflow appends "_jpg.rf.<md5>" and the importer appended a second "_<hash16>".
# Stripping both recovers the identity of the original frame.
RF_SUFFIX = re.compile(r"_jpe?g\.rf\.[0-9a-f]+.*$", re.IGNORECASE)
# Two different pipelines appended two different hash lengths (the importer 16 chars,
# the mainfield cropper 10). Anything from 8 hex chars up is a hash here -- no real
# frame name ends in one, and leaving them attached makes every frame its own camera
# view, which silently disables both cross-frame label filters and the grouped split.
TRAILING_HASH = re.compile(r"_[0-9a-f]{8,32}$")


def base_name(stem: str) -> str:
    cleaned = RF_SUFFIX.sub("", stem)
    return TRAILING_HASH.sub("", cleaned)


def camera_group(base: str) -> tuple[str, bool]:
    # Returns (group, is_synthetic). The group must identify one continuous camera view:
    # it is what keeps augmented and consecutive frames on the same side of the split,
    # and it is what propose_missing_labels.py needs for its cross-frame filters.
    if base.startswith("2026_sim"):
        return "sim", True
    # "<videoid>_<eventkey>_<match>_t<ms>_mainfield"
    match = re.match(r"^(?P<view>.+?)_t\d+(?:_mainfield(?:_[0-9a-f]+)?)?$", base)
    if match:
        return match.group("view"), False
    # "match03_33m29s" -> one recorded match
    match = re.match(r"^(match\d+)_", base)
    if match:
        return match.group(1), False
    # "video_frame_0123" / "frame_0123" -> one continuous clip each
    match = re.match(r"^(.*?)frame_\d+$", base)
    if match:
        return (match.group(1).rstrip("_") or "frame_seq"), False
    # Anything else ending in a sequence index -- "2017_real_img_0042" (a 2017 season
    # clip that is still in this corpus) or "GX010280_0413" (one long GoPro recording).
    # Without this they each become a group of one, which turns the grouped split back
    # into a random split and disables the cross-frame label filters.
    match = re.match(r"^(?P<view>.+?)_\d{3,6}$", base)
    if match:
        return match.group("view"), False
    return base, False


def collect(sources: list[Path]) -> dict[str, dict]:
    # base name -> one representative image + its label, plus provenance
    seen: dict[str, dict] = {}
    for root in sources:
        assert_not_holdout(root)
        for split in ("train", "val", "test"):
            image_dir = root / "images" / split
            if not image_dir.is_dir():
                continue
            for image_path in dataset_images(root, split):
                base = base_name(image_path.stem)
                label_path = root / "labels" / split / f"{image_path.stem}.txt"
                boxes = 0
                if label_path.is_file():
                    boxes = sum(
                        1 for line in label_path.read_text().splitlines() if len(line.split()) >= 5
                    )
                existing = seen.get(base)
                # Keep the copy carrying the most labels: augmentation copies should be
                # identical, but if one pass was more complete than another, take it.
                if existing is None or boxes > existing["boxes"]:
                    group, synthetic = camera_group(base)
                    seen[base] = {
                        "image": image_path,
                        "label": label_path if label_path.is_file() else None,
                        "boxes": boxes,
                        "group": group,
                        "synthetic": synthetic,
                        "source": root.name,
                    }
    return seen


def assign_splits(groups: list[str], val_fraction: float) -> dict[str, str]:
    # Deterministic hash-based assignment, so the split is reproducible and adding a new
    # camera view never reshuffles the existing ones.
    if not math.isfinite(val_fraction) or not 0 < val_fraction < 1:
        raise ValueError("val_fraction must be finite and strictly between 0 and 1.")
    out: dict[str, str] = {}
    for group in sorted(groups):
        digest = hashlib.sha256(group.encode()).hexdigest()
        bucket = int(digest[:8], 16) / 0xFFFFFFFF
        out[group] = "val" if bucket < val_fraction else "train"
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the deduplicated v3 base corpus.")
    parser.add_argument("--sources", nargs="+", default=list(DEFAULT_SOURCES))
    parser.add_argument("--out", required=True)
    parser.add_argument("--synthetic-out", default=None, help="Where simulator frames go (default: <out>_synthetic).")
    parser.add_argument("--val-fraction", type=float, default=0.15)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sources = []
    for raw in args.sources:
        path = Path(raw)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        if not path.is_dir():
            print(json.dumps({"ok": False, "error": f"Source not found: {path}"}))
            return 2
        sources.append(path.resolve())

    out = Path(args.out)
    if not out.is_absolute():
        out = BACKEND_ROOT / out
    synthetic_out = Path(args.synthetic_out) if args.synthetic_out else out.parent / f"{out.name}_synthetic"
    if not synthetic_out.is_absolute():
        synthetic_out = BACKEND_ROOT / synthetic_out
    try:
        for target in (out, synthetic_out):
            assert_fresh_output(target, sources)
        if overlaps(out, synthetic_out):
            raise ValueError("Real and synthetic outputs must not overlap.")
        frames = collect(sources)
        if not frames:
            raise ValueError("No input images found.")
        real_groups = sorted({m["group"] for m in frames.values() if not m["synthetic"]})
        split_of = assign_splits(real_groups, float(args.val_fraction))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    stats: dict[str, dict] = defaultdict(lambda: {"frames": 0, "boxes": 0})
    per_source: dict[str, int] = defaultdict(int)
    provenance: dict[str, list[dict]] = {"real": [], "synthetic": []}
    written = 0

    if not args.dry_run:
        for target in (out, synthetic_out):
            for split in ("train", "val"):
                (target / "images" / split).mkdir(parents=True, exist_ok=True)
                (target / "labels" / split).mkdir(parents=True, exist_ok=True)

    for base, meta in sorted(frames.items()):
        if meta["synthetic"]:
            target, split = synthetic_out, "train"
            bucket = "synthetic"
        else:
            target, split = out, split_of[meta["group"]]
            bucket = split
        stats[bucket]["frames"] += 1
        stats[bucket]["boxes"] += meta["boxes"]
        per_source[meta["source"]] += 1
        provenance["synthetic" if meta["synthetic"] else "real"].append({
            "image": f"{base}{meta['image'].suffix.lower()}",
            "split": split,
            "group": meta["group"],
            "source_image": str(meta["image"]),
            # A Roboflow hash identifies a variant, not whether it was mirrored.
            "temporal_filters_safe": ".rf." not in meta["image"].name.lower(),
        })
        if args.dry_run:
            continue
        suffix = meta["image"].suffix.lower()
        shutil.copy2(meta["image"], target / "images" / split / f"{base}{suffix}")
        label_text = meta["label"].read_text() if meta["label"] else ""
        (target / "labels" / split / f"{base}.txt").write_text(label_text, encoding="utf-8")
        written += 1

    if not args.dry_run:
        for target in (out, synthetic_out):
            (target / "dataset.yaml").write_text(
                f"path: {target}\ntrain: images/train\nval: images/val\n\nnames:\n  0: robot\n",
                encoding="utf-8",
            )
            (target / "DATASET_PROVENANCE.json").write_text(
                json.dumps({"version": 1, "frames": provenance[
                    "synthetic" if target == synthetic_out else "real"
                ]}, indent=2), encoding="utf-8",
            )

    summary = {
        "ok": True,
        "sources": [s.name for s in sources],
        "out": str(out),
        "synthetic_out": str(synthetic_out),
        "input_images": sum(
            len([p for p in (s / "images" / sp).iterdir()])
            for s in sources
            for sp in ("train", "val", "test")
            if (s / "images" / sp).is_dir()
        ),
        "unique_frames": len(frames),
        "camera_groups_real": len(real_groups),
        "written": written,
        "dry_run": bool(args.dry_run),
        "buckets": {
            k: {
                "frames": v["frames"],
                "boxes": v["boxes"],
                "boxes_per_frame": round(v["boxes"] / max(1, v["frames"]), 3),
            }
            for k, v in sorted(stats.items())
        },
        "unique_frames_by_source": dict(per_source),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
