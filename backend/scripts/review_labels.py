#!/usr/bin/env python3
"""Record exact label corrections and apply them to a separate, unaudited copy.

Export a review template, inspect the full-resolution images, then edit each frame's
action to keep/replace/skip. `boxes` is the complete replacement list in normalized
YOLO [class, cx, cy, width, height] format. Pending frames remain unchanged. Review
decisions are bound to the source bytes; an old review cannot change a newer dataset.
This is a correction tool, not evidence that the training acceptance gate passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.dataset_safety import (  # noqa: E402
    assert_fresh_output,
    assert_not_holdout,
    dataset_fingerprint,
    dataset_images,
)


def read_yolo(path: Path) -> list[list[float]]:
    return [[float(v) for v in line.split()] for line in path.read_text().splitlines()
            if line.strip()] if path.is_file() else []


def validate_boxes(boxes: object) -> list[list[float]]:
    if not isinstance(boxes, list):
        raise ValueError("boxes must be a complete list of YOLO boxes")
    for box in boxes:
        if (not isinstance(box, list) or len(box) != 5
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or box[0] != 0):
            raise ValueError("Each box must contain five finite numbers and class 0")
        _, cx, cy, width, height = box
        if (not 0 < width <= 1 or not 0 < height <= 1
                or cx - width / 2 < -1e-6 or cx + width / 2 > 1 + 1e-6
                or cy - height / 2 < -1e-6 or cy + height / 2 > 1 + 1e-6):
            raise ValueError("Replacement boxes must have positive size and lie inside the image")
    return boxes


def export_review(dataset: Path, splits: list[str], sample: int) -> dict:
    assert_not_holdout(dataset)
    if sample < 1:
        raise ValueError("sample must be positive")
    fingerprint, total, _ = dataset_fingerprint(dataset, splits)
    candidates = [(split, image) for split in sorted(set(splits))
                  for image in dataset_images(dataset, split)]
    ranked = sorted(candidates, key=lambda pair: hashlib.sha256(
        f"{pair[0]}/{pair[1].name}".encode()).hexdigest())[:sample]
    frames = []
    for split, image in sorted(ranked):
        label = dataset / "labels" / split / f"{image.stem}.txt"
        frames.append({
            "image": image.relative_to(dataset).as_posix(),
            "action": "pending", "reason": "",
            "boxes": read_yolo(label),
        })
    return {"version": 1, "dataset_sha256": fingerprint, "splits": sorted(set(splits)),
            "source_frames": total, "reviewer": "", "reviewer_kind": "assisted",
            "frames": frames}


def apply_review(dataset: Path, output: Path, review: dict) -> dict:
    """Preflight every action before creating output; never edit the source in place."""
    assert_not_holdout(dataset)
    assert_fresh_output(output, [dataset])
    if not isinstance(review, dict) or review.get("version") != 1:
        raise ValueError("Review must be a version 1 object")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ValueError("Record a reviewer before applying corrections")
    if review.get("reviewer_kind") not in {"human", "assisted"}:
        raise ValueError("reviewer_kind must be human or assisted")
    splits = review.get("splits")
    if not isinstance(splits, list) or not splits or any(not isinstance(s, str) for s in splits):
        raise ValueError("Review requires split names")
    actual_splits = sorted(p.name for p in (dataset / "images").iterdir() if p.is_dir())
    if sorted(set(splits)) != actual_splits:
        raise ValueError("Review must cover every source split so none is silently dropped")
    fingerprint, total, _ = dataset_fingerprint(dataset, splits)
    if review.get("dataset_sha256") != fingerprint:
        raise ValueError("Stale review: source images or labels changed")
    images = {image.relative_to(dataset).as_posix(): image for split in splits
              for image in dataset_images(dataset, split)}
    actions = {}
    if not isinstance(review.get("frames"), list):
        raise ValueError("Review frames must be a list")
    for frame in review["frames"]:
        if not isinstance(frame, dict):
            raise ValueError("Invalid review frame")
        key, action = frame.get("image"), frame.get("action")
        if not isinstance(key, str) or key not in images or key in actions:
            raise ValueError(f"Unknown or duplicate image: {key}")
        if action not in {"pending", "keep", "replace", "skip"}:
            raise ValueError(f"Unknown action: {action}")
        if action in {"replace", "skip"} and not str(frame.get("reason") or "").strip():
            raise ValueError("Corrections and skipped frames need a reason")
        if action == "replace":
            validate_boxes(frame.get("boxes"))
        actions[key] = frame

    counts = {"kept": 0, "replaced": 0, "skipped": 0, "pending": 0}
    # Copy only the defined image/label cohort, never old audit acceptance or caches.
    for split in splits:
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    for key, image in images.items():
        frame = actions.get(key, {"action": "pending"})
        action = frame["action"]
        counts[{"skip": "skipped", "replace": "replaced", "keep": "kept",
                "pending": "pending"}[action]] += 1
        if action == "skip":
            continue
        split = image.parent.name
        label = dataset / "labels" / split / f"{image.stem}.txt"
        target_label = output / "labels" / split / label.name
        shutil.copy2(image, output / key)
        if action == "replace":
            text = "\n".join("0 " + " ".join(f"{v:.8f}" for v in box[1:])
                             for box in frame["boxes"])
            target_label.write_text(text + ("\n" if text else ""))
        elif label.exists():
            shutil.copy2(label, target_label)
    import yaml

    config = {"path": str(output.resolve()), "names": {0: "robot"}}
    config.update({split: f"images/{split}" for split in splits})
    (output / "dataset.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    updated, _, _ = dataset_fingerprint(output, splits)
    audit = output / "audit"
    audit.mkdir()
    (audit / "corrections.json").write_text(json.dumps(review, indent=2) + "\n")
    result = {"version": 1, "status": "needs_review", "source_dataset_sha256": fingerprint,
              "dataset_sha256": updated, "splits": sorted(set(splits)),
              "reviewer": review["reviewer"], "reviewer_kind": review["reviewer_kind"],
              "source_frames": total, "actions": counts}
    (audit / "AUDIT.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True, help="JSON for export, new dataset directory for apply")
    parser.add_argument("--apply", help="Apply this edited review JSON instead of exporting")
    parser.add_argument("--splits", nargs="+", default=["train", "val"])
    parser.add_argument("--sample", type=int, default=200)
    args = parser.parse_args()
    dataset, output = Path(args.dataset).resolve(), Path(args.out).resolve()
    try:
        if args.apply:
            result = apply_review(dataset, output, json.loads(Path(args.apply).read_text()))
        else:
            if output.exists() or output.is_relative_to(dataset):
                raise ValueError("Review export must be a new file outside the source dataset")
            assert_not_holdout(output)
            result = export_review(dataset, args.splits, args.sample)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"ok": True, "output": str(output), "status": "needs_review"}))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
