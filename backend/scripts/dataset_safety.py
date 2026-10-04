"""Local dataset preflights shared by the detector preparation tools.

These checks deliberately fail before model imports, inference, or filesystem writes.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
LOCKED_HOLDOUT_DIR_NAMES = {
    "frc_einstein_2026_holdout_reviewed_yolo",
    "frc_einstein_2026_holdout_reviewed_coco_raw",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SPLITS = {"train", "val", "test"}


def overlaps(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


def assert_not_holdout(path: Path) -> None:
    resolved = path.resolve()
    names = {name.lower() for name in LOCKED_HOLDOUT_DIR_NAMES}
    if names.intersection(part.lower() for part in (*path.parts, *resolved.parts)):
        raise ValueError(f"Refusing locked test-only holdout path: {path}")
    for name in LOCKED_HOLDOUT_DIR_NAMES:
        locked = BACKEND_ROOT / "media" / "datasets" / name
        if overlaps(resolved, locked) or (resolved / name).exists():
            raise ValueError(f"Path contains or aliases the locked holdout: {path}")


def assert_contained(path: Path, root: Path) -> None:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Dataset path escapes its root (possibly a symlink): {path}")


def assert_fresh_output(path: Path, sources: Sequence[Path] = ()) -> None:
    assert_not_holdout(path)
    if any(overlaps(path, source) for source in sources):
        raise ValueError(f"Output overlaps an input: {path}")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"Output must be new or empty; refusing stale output: {path}")


def dataset_images(dataset: Path, split: str) -> list[Path]:
    if split not in SPLITS:
        raise ValueError(f"Unsupported split: {split}")
    image_dir = dataset / "images" / split
    assert_contained(image_dir, dataset)
    if not image_dir.is_dir():
        raise ValueError(f"Missing images directory: {image_dir}")
    images = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    for image in images:
        assert_contained(image, dataset)
        assert_contained(dataset / "labels" / split / f"{image.stem}.txt", dataset)
        if not image.is_file():
            raise ValueError(f"Image is not a file: {image}")
    return images


def dataset_fingerprint(dataset: Path, splits: Sequence[str]) -> tuple[str, int, int]:
    """Bind audit evidence to every image and label, including missing labels."""
    digest = hashlib.sha256()
    count = boxes = 0
    for split in sorted(set(splits)):
        for image in dataset_images(dataset, split):
            count += 1
            label = dataset / "labels" / split / f"{image.stem}.txt"
            for path in (image, label):
                digest.update(path.relative_to(dataset).as_posix().encode() + b"\0")
                if path.is_file():
                    data = path.read_bytes()
                    digest.update(hashlib.sha256(data).digest())
                else:
                    digest.update(b"MISSING")
            if label.is_file():
                boxes += len([line for line in label.read_text().splitlines() if line.strip()])
    return digest.hexdigest(), count, boxes


def validate_audit(dataset: Path, splits: Sequence[str]) -> dict:
    path = dataset / "audit" / "AUDIT.json"
    assert_contained(path, dataset)
    if not path.is_file():
        raise ValueError(f"No accepted human audit at {path}; use --experiment-only for experiments.")
    audit = json.loads(path.read_text())
    fingerprint, images, boxes = dataset_fingerprint(dataset, splits)
    if not isinstance(audit, dict) or audit.get("version") != 1 or audit.get("status") != "accepted":
        raise ValueError("Dataset audit must have version=1 and status='accepted'.")
    if audit.get("dataset_sha256") != fingerprint or audit.get("splits") != sorted(set(splits)):
        raise ValueError("Dataset audit is stale or covers different splits.")
    if (audit.get("reviewer_kind") != "human" or not isinstance(audit.get("reviewer"), str)
            or not audit["reviewer"].strip()):
        raise ValueError("Dataset audit requires the human reviewer's identity.")
    reviewed = audit.get("reviewed_frames")
    if type(reviewed) is not int or not min(200, images) <= reviewed <= images or not images:
        raise ValueError("Dataset audit has insufficient reviewed frames.")
    for key, low, high in (("label_recall", 0.95, 1.0), ("false_positive_rate", 0.0, 0.02)):
        value = audit.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Dataset audit failed {key}.")
    reference = audit.get("reference_density")
    if (type(reference) not in (int, float) or not math.isfinite(reference) or reference <= 0
            or abs(boxes / images - reference) > 0.3):
        raise ValueError("Dataset audit density is outside the reference +/-0.3 band.")
    return audit
