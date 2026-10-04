#!/usr/bin/env python3
# Train an FRC-specific YOLO detector and gate it on the locked holdout.
#
# The gate used to validate against the dataset's own val split, which is the wrong
# instrument twice over: that split shares a labelling standard with the train split, and
# for the v2 corpus that standard was measurably incomplete (3.31 boxes/frame against a
# true 5.19). A model that reproduces incomplete labels scores well there and badly in
# production. --gate-holdout validates against media/datasets/frc_einstein_2026_holdout_
# reviewed_yolo instead: different event, different labelling pass, never trained on.
#
# Recall is gated alongside mAP. v2 passed every mAP gate it was given while missing four
# robots in ten -- precision 0.92, recall 0.59 -- because mAP@0.5 averages over the
# precision-recall curve and a high-precision, low-recall detector scores respectably.
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import math
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.build_v3_dataset import base_name, camera_group
from scripts.dataset_safety import (
    assert_contained,
    assert_not_holdout,
    dataset_images,
    overlaps,
    validate_audit,
)

LOCKED_HOLDOUT = BACKEND_ROOT / "media/datasets/frc_einstein_2026_holdout_reviewed_yolo"


@contextmanager
def _holdout_yaml():
    # Validation writes caches and can repair JPEGs. Isolate images and labels.
    images = dataset_images(LOCKED_HOLDOUT, "val")
    if not images:
        raise ValueError("Locked holdout has no validation images.")
    with tempfile.TemporaryDirectory(prefix="frcmob_train_holdout_") as raw:
        tmp = Path(raw)
        (tmp / "images" / "val").mkdir(parents=True)
        (tmp / "labels" / "val").mkdir(parents=True)
        for image in images:
            label = LOCKED_HOLDOUT / "labels" / "val" / f"{image.stem}.txt"
            if not label.is_file():
                raise ValueError(f"Locked holdout label is missing: {label}")
            shutil.copy2(image, tmp / "images" / "val" / image.name)
            shutil.copy2(label, tmp / "labels" / "val" / label.name)
        yaml_path = tmp / "dataset.yaml"
        yaml_path.write_text(
            f"path: {json.dumps(str(tmp))}\ntrain: images/val\nval: images/val\nnames:\n  0: robot\n",
            encoding="utf-8",
        )
        yield str(yaml_path)


def _training_dataset(data_path: Path, *, experiment_only: bool) -> tuple[Path, dict]:
    import hashlib

    import yaml

    assert_not_holdout(data_path)
    config = yaml.safe_load(data_path.read_text())
    if not isinstance(config, dict):
        raise ValueError("Dataset yaml must contain a mapping.")
    root = Path(config.get("path") or data_path.parent)
    if not root.is_absolute():
        root = data_path.parent / root
    root = root.resolve()
    assert_not_holdout(root)
    splits: dict[str, list[Path]] = {}
    for split in ("train", "val"):
        value = config.get(split)
        if not isinstance(value, str):
            raise ValueError(f"{split} must point to the dataset's images/{split} directory.")
        image_dir = (root / value).resolve()
        assert_not_holdout(image_dir)
        assert_contained(image_dir, root)
        if image_dir != (root / "images" / split).resolve():
            raise ValueError(f"Use the standard images/{split} layout for auditable training.")
        splits[split] = dataset_images(root, split)
        if not splits[split]:
            raise ValueError(f"Empty {split} split.")
        for image in splits[split]:
            label = root / "labels" / split / f"{image.stem}.txt"
            if not label.is_file():
                raise ValueError(f"Missing training label: {label}")
            for line in label.read_text().splitlines():
                if not line.strip():
                    continue
                values = line.split()
                if len(values) != 5 or values[0] != "0":
                    raise ValueError(f"Expected single-class YOLO detection labels: {label}")
                coords = [float(v) for v in values[1:]]
                if any(not math.isfinite(v) or not 0 <= v <= 1 for v in coords) or min(coords[2:]) <= 0:
                    raise ValueError(f"Invalid box geometry in {label}")
    if overlaps(root / "images" / "train", root / "images" / "val"):
        raise ValueError("Training and validation image directories overlap.")
    train_groups = {camera_group(base_name(p.stem))[0] for p in splits["train"]}
    val_groups = {camera_group(base_name(p.stem))[0] for p in splits["val"]}
    if train_groups & val_groups:
        raise ValueError("Source video groups overlap between train and val; rebuild a grouped split.")
    train_hashes = {hashlib.sha256(p.read_bytes()).digest() for p in splits["train"]}
    if any(hashlib.sha256(p.read_bytes()).digest() in train_hashes for p in splits["val"]):
        raise ValueError("Identical image content appears in train and val.")
    if (LOCKED_HOLDOUT / "images" / "val").is_dir():
        holdout_hashes = {hashlib.sha256(p.read_bytes()).digest()
                          for p in dataset_images(LOCKED_HOLDOUT, "val")}
        corpus_hashes = train_hashes | {hashlib.sha256(p.read_bytes()).digest()
                                       for p in splits["val"]}
        if corpus_hashes & holdout_hashes:
            raise ValueError("Locked holdout image content appears in the training corpus.")
    if not experiment_only:
        validate_audit(root, ["train", "val"])
    return root, {"path": str(root), "train": "images/train", "val": "images/val", "names": {0: "robot"}}


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
        "--gate-holdout",
        dest="gate_holdout",
        action="store_true",
        default=True,
        help="Validate against the locked Einstein holdout rather than the training "
        "set's own val split (the default, and the only honest option).",
    )
    parser.add_argument("--no-gate-holdout", dest="gate_holdout", action="store_false")
    parser.add_argument("--experiment-only", action="store_true", help="Allow unaudited training; never export/publish weights.")
    parser.add_argument(
        "--min-recall",
        type=float,
        default=0.5904,
        help="Refuse to publish unless holdout recall reaches this. v2 sits at 0.5904 "
        "at 1280; a replacement that does not beat it is not an improvement whatever "
        "mAP says.",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--lr0", type=float, default=None, help="Low value (e.g. 0.001) for a fine-tune.")
    parser.add_argument(
        "--optimizer",
        default=None,
        help="ultralytics defaults to 'auto', which picks the optimizer AND the learning "
        "rate and silently discards --lr0. Passing --lr0 therefore forces SGD unless an "
        "optimizer is named here.",
    )
    parser.add_argument("--patience", type=int, default=None)
    parser.add_argument(
        "--min-map50",
        type=float,
        default=0.7257,
        help="Refuse to publish the model unless validation mAP@0.5 reaches "
        "this (v2 at 1280, square padding, NMS IoU .6). Zero is allowed only "
        "for --experiment-only runs.",
    )
    args = parser.parse_args()

    data_path = Path(args.data).resolve()
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset yaml not found: {data_path}")

    project_path = Path(args.project).resolve()
    output_model = Path(args.output_model).resolve()
    try:
        if not args.name or Path(args.name).name != args.name or args.name in {".", ".."}:
            raise ValueError("Run name must be a single directory name.")
        for value in (args.min_map50, args.min_recall):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Quality thresholds must be finite and between 0 and 1.")
        if args.lr0 is not None and (not math.isfinite(args.lr0) or args.lr0 <= 0):
            raise ValueError("--lr0 must be finite and positive.")
        if args.lr0 is not None and str(args.optimizer).strip().lower() == "auto":
            raise ValueError("--optimizer auto ignores --lr0; choose an explicit optimizer.")
        if not args.experiment_only and (
            not args.gate_holdout or args.min_map50 <= 0 or args.min_recall <= 0
        ):
            raise ValueError("Exporting weights requires the locked holdout and positive mAP/recall gates.")
        dataset_root, dataset_config = _training_dataset(data_path, experiment_only=args.experiment_only)
        if args.gate_holdout:
            if not dataset_images(LOCKED_HOLDOUT, "val"):
                raise ValueError("Locked holdout is empty.")
            for image in dataset_images(LOCKED_HOLDOUT, "val"):
                if not (LOCKED_HOLDOUT / "labels" / "val" / f"{image.stem}.txt").is_file():
                    raise ValueError(f"Missing locked holdout label for {image.name}.")
        for target in (project_path, output_model):
            assert_not_holdout(target)
            if overlaps(target, dataset_root):
                raise ValueError("Training output overlaps the dataset.")
        if not args.experiment_only and output_model.exists():
            raise ValueError("Output model already exists; choose a new versioned filename.")
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    try:
        from ultralytics import YOLO  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("ultralytics is required to train YOLO models.") from exc

    project_path.mkdir(parents=True, exist_ok=True)

    model = YOLO(str(args.base_model))
    train_kwargs = {
        "data": str(data_path),
        "epochs": int(max(1, args.epochs)),
        "imgsz": int(max(320, args.imgsz)),
        "batch": int(max(1, args.batch)),
        "project": str(project_path),
        "name": str(args.name),
        "workers": int(max(1, args.workers)),
        "exist_ok": False,
        "verbose": True,
        "seed": int(args.seed),
    }
    if args.lr0 is not None:
        train_kwargs["lr0"] = float(args.lr0)
        # 'optimizer=auto' logs "ignoring 'lr0'" and substitutes its own. A fine-tune
        # asked for a low rate and silently getting AdamW at 0.002 is not a fine-tune --
        # that is roughly the aggressive recipe that produced the rejected v2 candidate.
        train_kwargs["optimizer"] = str(args.optimizer or "SGD")
    elif args.optimizer:
        train_kwargs["optimizer"] = str(args.optimizer)
    if args.patience is not None:
        train_kwargs["patience"] = int(args.patience)
    if str(args.device).strip():
        train_kwargs["device"] = str(args.device).strip()

    # Use resolved, local paths only; a dataset yaml's optional download hook must
    # never execute as a side effect of this training wrapper.
    import yaml

    with tempfile.TemporaryDirectory(prefix="frcmob_train_data_") as temp_dir:
        resolved_data = Path(temp_dir) / "dataset.yaml"
        resolved_data.write_text(yaml.safe_dump(dataset_config), encoding="utf-8")
        train_kwargs["data"] = str(resolved_data)
        result = model.train(**train_kwargs)
    if not getattr(result, "save_dir", None):
        raise ValueError("Training did not return a run directory.")
    save_dir = Path(str(result.save_dir)).resolve()
    assert_contained(save_dir, project_path)
    best_weight = save_dir / "weights" / "best.pt"
    last_weight = save_dir / "weights" / "last.pt"
    source_weight = best_weight if best_weight.exists() else last_weight
    assert_contained(source_weight, save_dir)
    if not source_weight.is_file() or source_weight.stat().st_size == 0:
        raise FileNotFoundError(f"No trained weights found in {save_dir}")

    # Quality gate: validate the trained weights before publishing. A model
    # that doesn't actually detect robots is worse than the honest DEGRADED
    # flag, because it looks "successful" while producing empty findings.
    min_map50 = max(0.0, float(args.min_map50))
    min_recall = max(0.0, float(args.min_recall))
    map50 = None
    recall = None
    gate_data = str(data_path)
    gate_source = "train_val_split"
    if args.gate_holdout:
        gate_source = "locked_einstein_holdout"
    if min_map50 > 0.0 or min_recall > 0.0:
        try:
            trained = YOLO(str(source_weight))
            def validate(data: str):
                return trained.val(
                    data=data,
                    imgsz=int(max(320, args.imgsz)),
                    verbose=False,
                    rect=False,
                    conf=0.001,
                    iou=0.6,
                    max_det=50,
                    batch=1,
                    workers=0,
                    device="cpu",
                    project=str(project_path),
                    name=f"{args.name}_gate",
                    exist_ok=False,
                    plots=False,
                )

            if args.gate_holdout:
                with _holdout_yaml() as gate_data:
                    metrics = validate(gate_data)
            else:
                with tempfile.TemporaryDirectory(prefix="frcmob_experiment_val_") as temp_dir:
                    resolved_data = Path(temp_dir) / "dataset.yaml"
                    resolved_data.write_text(yaml.safe_dump(dataset_config), encoding="utf-8")
                    metrics = validate(str(resolved_data))
            box = getattr(metrics, "box", metrics)
            map50 = float(getattr(box, "map50", 0.0) or 0.0)
            recall = float(getattr(box, "mr", 0.0) or 0.0)
            if any(not math.isfinite(value) or not 0 <= value <= 1 for value in (map50, recall)):
                raise ValueError("Validation returned non-finite or out-of-range metrics.")
        except Exception as exc:  # pragma: no cover - validation best-effort
            print(
                json.dumps(
                    {"ok": False, "error": f"post-train validation failed: {exc}"},
                    indent=2,
                )
            )
            return 1
        if recall is not None and min_recall > 0.0 and recall < min_recall:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": (
                            f"recall={recall:.4f} below threshold {min_recall:.2f} on "
                            f"{gate_source}. mAP alone hid exactly this for two model "
                            "generations -- refusing to publish."
                        ),
                        "save_dir": save_dir.as_posix(),
                        "map50": round(map50 or 0.0, 4),
                        "recall": round(recall, 4),
                    },
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

    if not args.experiment_only:
        output_model.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_weight, output_model)
    print(
        json.dumps(
            {
                "ok": True,
                "status": "experimental" if args.experiment_only else "validated_candidate",
                "data": data_path.as_posix(),
                "save_dir": save_dir.as_posix(),
                "source_weight": source_weight.as_posix(),
                "output_model": None if args.experiment_only else output_model.as_posix(),
                "map50": round(map50, 4) if map50 is not None else None,
                "recall": round(recall, 4) if recall is not None else None,
                "min_map50": min_map50,
                "min_recall": min_recall,
                "gate_source": gate_source,
                "imgsz": int(args.imgsz),
                "epochs": int(args.epochs),
                "seed": int(args.seed),
                "base_model": str(args.base_model),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
