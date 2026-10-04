#!/usr/bin/env python3
# Find the robots that were never boxed.
#
# Why this exists: frc_robot_detector_v2 was trained on a set averaging 3.31 boxes a
# frame. A REBUILT frame has six robots and the locked holdout averages 5.19. Those
# missing boxes are not merely absent supervision -- the loss reads every unlabelled
# robot as background, so the detector learns to score real robots down. Measured on
# the holdout: 2.45 detections/frame at conf 0.25, 3.41 at conf 0.05 (i.e. the
# training density), 5.41 at conf 0.01 (i.e. the truth). The robots are seen. Their
# scores are suppressed.
#
# So the model itself is already a usable proposer, provided something filters the
# junk out of the low-confidence tail. That filter is view agreement: run the detector
# over several scales and a horizontal flip, cluster the boxes, and keep only what
# several independent views agree on. A real robot survives a rescale and a mirror; a
# low-confidence artifact usually does not.
#
# Output is a review manifest plus, with --apply, an unreviewed proposal copy.
# Nothing is ever written back into the source directory, and the locked holdout is
# refused outright.
#
#   PYTHONPATH=. python scripts/propose_missing_labels.py \
#       --dataset media/datasets/frc_v2_roboflow_reviewed_grouped --split train
#   PYTHONPATH=. python scripts/propose_missing_labels.py ... --apply --out media/datasets/frc_v3_completed
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

from scripts.dataset_safety import (
    assert_fresh_output,
    assert_not_holdout,
    dataset_images,
    overlaps,
)

DEFAULT_SCALES = (1280, 1536)
# Low enough to reach into the suppressed tail; agreement is what buys precision back,
# not this threshold.
PROPOSAL_CONF_FLOOR = 0.02


@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 0.0
    votes: int = 0
    scores: list[float] = field(default_factory=list)

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


def iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def cluster_votes(views: list[list[Box]], match_iou: float) -> list[Box]:
    # Greedy agglomeration seeded by the highest-scoring box, so a cluster's geometry
    # comes from the view that was most sure about it. Cheap, deterministic, and there
    # are only ever a handful of robots per frame.
    flat: list[tuple[int, Box]] = [(i, b) for i, view in enumerate(views) for b in view]
    flat.sort(key=lambda pair: pair[1].score, reverse=True)

    clusters: list[tuple[Box, set[int], list[float], list[Box]]] = []
    for view_idx, box in flat:
        placed = False
        for seed, seen, scores, members in clusters:
            if iou(seed, box) >= match_iou:
                seen.add(view_idx)
                scores.append(box.score)
                members.append(box)
                placed = True
                break
        if not placed:
            clusters.append((box, {view_idx}, [box.score], [box]))

    merged: list[Box] = []
    for _seed, seen, scores, members in clusters:
        # Score-weighted average geometry -- the standard weighted-box-fusion move.
        # It is steadier than taking the single best box when views disagree slightly.
        total = sum(max(s, 1e-6) for s in (m.score for m in members))
        avg = Box(
            x1=sum(m.x1 * max(m.score, 1e-6) for m in members) / total,
            y1=sum(m.y1 * max(m.score, 1e-6) for m in members) / total,
            x2=sum(m.x2 * max(m.score, 1e-6) for m in members) / total,
            y2=sum(m.y2 * max(m.score, 1e-6) for m in members) / total,
            score=sum(scores) / len(scores),
            votes=len(seen),
        )
        avg.scores = sorted(scores, reverse=True)
        merged.append(avg)
    merged.sort(key=lambda b: (b.votes, b.score), reverse=True)
    return merged


def source_key(name: str) -> str:
    # Frames are named "<videoid>_<eventkey>_<match>_t<ms>_mainfield_<hash>.jpg", so
    # everything up to the timestamp identifies one continuous camera view. Frames from
    # one view share a background, which is what makes the motion test below possible.
    from scripts.build_v3_dataset import base_name, camera_group

    return camera_group(base_name(Path(name).stem))[0]


def unsafe_temporal_groups(dataset: Path, split: str, images: list[Path]) -> set[str]:
    """Unknown or augmented orientation cannot support a static-camera veto."""
    provenance_path = dataset / "DATASET_PROVENANCE.json"
    safe_images = set()
    if provenance_path.is_file():
        provenance = json.loads(provenance_path.read_text())
        safe_images = {row["image"] for row in provenance.get("frames", [])
                       if row.get("split") == split and row.get("temporal_filters_safe") is True}
    return {source_key(path.name) for path in images
            if path.name not in safe_images or ".rf." in path.name.lower()}


def build_background(paths: list[Path], sample: int, work_width: int) -> Any:
    # Per-pixel temporal median over frames from one camera view. Robots and people
    # move through; the field, the hubs and the alliance walls do not, so they survive
    # the median and everything else averages away.
    import numpy as np
    from PIL import Image

    step = max(1, len(paths) // sample)
    stack = []
    size = None
    for path in paths[::step][:sample]:
        img = Image.open(path).convert("L")
        if size is None:
            size = (work_width, max(1, round(work_width * img.height / img.width)))
        stack.append(np.asarray(img.resize(size, Image.BILINEAR), dtype=np.float32))
    if len(stack) < 4:
        return None
    return np.median(np.stack(stack), axis=0)


def load_gray(image_path: Path, size: tuple[int, int]) -> Any:
    # Loaded once per frame and reused for every box on it. Doing this per box instead
    # -- which is the obvious way to write it -- decodes and rescales the whole image
    # eight or nine times per frame and dominates the runtime.
    import numpy as np
    from PIL import Image

    return np.asarray(
        Image.open(image_path).convert("L").resize(size, Image.BILINEAR), dtype=np.float32
    )


def motion_energy(frame_gray: Any, background: Any, box: Box, width: int, height: int) -> float:
    # How different is this box from the same patch of empty field? A robot is a large
    # departure from the background; the red hub is the background. Returned in grey
    # levels (0-255), so it reads directly.
    import numpy as np

    bg_h, bg_w = background.shape
    sx, sy = bg_w / width, bg_h / height
    x1, y1 = max(0, int(box.x1 * sx)), max(0, int(box.y1 * sy))
    x2, y2 = min(bg_w, int(box.x2 * sx) + 1), min(bg_h, int(box.y2 * sy) + 1)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return 255.0
    return float(np.abs(frame_gray[y1:y2, x1:x2] - background[y1:y2, x1:x2]).mean())


def covered_fraction(inner: Box, outer: Box) -> float:
    ix1, iy1 = max(inner.x1, outer.x1), max(inner.y1, outer.y1)
    ix2, iy2 = min(inner.x2, outer.x2), min(inner.y2, outer.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    return inter / inner.area if inner.area > 0 else 0.0


def detect_people(model: Any, image_path: Path) -> list[Box]:
    # A generic COCO detector, used only as a veto. The FRC detector confidently boxes
    # referees, drive teams and spectators at low confidence, and neither the motion
    # test nor the persistence test can catch that -- people move exactly like robots
    # do. Measured on a pilot: vetoes 10% of proposals while costing 2.6% of known
    # robots, against a hand-counted proposal error rate around 14%.
    result = model.predict(
        str(image_path), imgsz=1536, conf=0.08, classes=[0], max_det=300, verbose=False
    )[0]
    return [Box(*xyxy) for xyxy in result.boxes.xyxy.tolist()]


def read_labels(path: Path, width: int, height: int) -> list[Box]:
    if not path.is_file():
        return []
    out: list[Box] = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cx, cy, bw, bh = (float(v) for v in parts[1:5])
        out.append(
            Box(
                (cx - bw / 2) * width,
                (cy - bh / 2) * height,
                (cx + bw / 2) * width,
                (cy + bh / 2) * height,
                score=1.0,
            )
        )
    return out


def to_yolo_line(box: Box, width: int, height: int) -> str:
    x1, x2 = min(max(box.x1, 0), width), min(max(box.x2, 0), width)
    y1, y2 = min(max(box.y1, 0), height), min(max(box.y2, 0), height)
    cx = ((x1 + x2) / 2) / width
    cy = ((y1 + y2) / 2) / height
    bw = (x2 - x1) / width
    bh = (y2 - y1) / height
    clamp = lambda v: min(max(v, 0.0), 1.0)  # noqa: E731
    return f"0 {clamp(cx):.6f} {clamp(cy):.6f} {clamp(bw):.6f} {clamp(bh):.6f}"


def predict_views(
    model: Any, image_path: Path, scales: tuple[int, ...], conf: float
) -> tuple[list[list[Box]], int, int]:
    # Two independent axes: input scale, and ultralytics' own TTA (which internally
    # runs a flip and extra scales and merges them). Boxes come back in original
    # image coordinates either way, so nothing needs un-flipping here.
    views: list[list[Box]] = []
    width = height = 0
    for imgsz in scales:
        for tta in (False, True):
            result = model.predict(
                str(image_path),
                imgsz=imgsz,
                conf=conf,
                iou=0.7,
                max_det=50,
                augment=tta,
                verbose=False,
            )[0]
            height, width = result.orig_shape
            views.append(
                [
                    Box(*xyxy, score=float(score))
                    for xyxy, score in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist())
                ]
            )
    return views, width, height


def process_split(
    model: Any,
    dataset: Path,
    split: str,
    scales: tuple[int, ...],
    conf: float,
    match_iou: float,
    min_votes: int,
    min_score: float,
    min_motion: float,
    max_persistence: float,
    person_model: Any,
    person_cover: float,
    bg_sample: int,
    limit: int | None,
) -> dict[str, Any]:
    import statistics as stats

    label_dir = dataset / "labels" / split
    images = dataset_images(dataset, split)
    if limit:
        images = images[:limit]

    groups: dict[str, list[Path]] = {}
    for path in images:
        groups.setdefault(source_key(path.name), []).append(path)

    unsafe_groups = unsafe_temporal_groups(dataset, split, images)

    backgrounds: dict[str, Any] = {}
    if min_motion > 0:
        for key, paths in groups.items():
            if key in unsafe_groups:
                continue
            try:
                backgrounds[key] = build_background(paths, bg_sample, 640)
            except Exception:
                backgrounds[key] = None

    # Pass 1 -- per frame. Everything the ensemble agrees on that is not already
    # labelled, kept raw so the cross-frame pass has something to work with.
    raw: list[dict[str, Any]] = []
    gt_motion: list[float] = []
    person_rejects = 0
    for index, image_path in enumerate(images):
        views, width, height = predict_views(model, image_path, scales, conf)
        if not width or not height:
            continue
        candidates = cluster_votes(views, match_iou)
        existing = read_labels(label_dir / f"{image_path.stem}.txt", width, height)
        background = backgrounds.get(source_key(image_path.name)) if min_motion > 0 else None
        frame_gray = None
        if background is not None:
            bg_h, bg_w = background.shape
            frame_gray = load_gray(image_path, (bg_w, bg_h))
            for gt in existing:
                gt_motion.append(motion_energy(frame_gray, background, gt, width, height))

        people = detect_people(person_model, image_path) if person_model is not None else []

        kept: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate.votes < min_votes or candidate.score < min_score:
                continue
            if any(iou(candidate, gt) >= match_iou for gt in existing):
                continue
            if people and max(covered_fraction(candidate, q) for q in people) >= person_cover:
                person_rejects += 1
                continue
            energy = (
                motion_energy(frame_gray, background, candidate, width, height)
                if frame_gray is not None
                else None
            )
            kept.append({"box": candidate, "motion": energy})

        raw.append(
            {
                "path": image_path,
                "group": source_key(image_path.name),
                "width": width,
                "height": height,
                "existing": existing,
                "candidates": kept,
                "suspects": [
                    gt
                    for gt in existing
                    if not any(iou(gt, c) >= match_iou for c in candidates if c.score >= conf)
                ],
            }
        )
        if index and index % 200 == 0:
            print(f"  ...{index}/{len(images)}", file=sys.stderr, flush=True)

    # Pass 2 -- across frames of one camera view. A detection that keeps firing in the
    # same place is field furniture, not a robot: the hubs, the alliance walls and the
    # scoreboard banner all read as robot-shaped at low confidence, and no amount of
    # rescaling shakes them off because the detector makes the same mistake every time.
    # Robots move. Measured on 80 frames of one view, the trusted labels never put a
    # cluster above 12% frame coverage while the two furniture clusters sat at 58% and
    # 30%, so a 25% cut removed 0 of 205 known robots.
    persistence: dict[int, float] = {}
    for group, frames in groups.items():
        rows = [r for r in raw if r["group"] == group]
        if not rows:
            continue
        clusters: list[tuple[Box, set[int], list[dict[str, Any]]]] = []
        for frame_index, row in enumerate(rows):
            for candidate in row["candidates"]:
                for seed, seen, members in clusters:
                    if iou(seed, candidate["box"]) >= match_iou:
                        seen.add(frame_index)
                        members.append(candidate)
                        break
                else:
                    clusters.append((candidate["box"], {frame_index}, [candidate]))
        for _seed, seen, members in clusters:
            coverage = len(seen) / max(1, len(rows))
            for member in members:
                persistence[id(member)] = coverage

    per_image: list[dict[str, Any]] = []
    total_existing = total_proposed = total_suspect = 0
    static_rejects = persistent_rejects = 0
    kept_motion: list[float] = []
    rejected_motion: list[float] = []

    for row in raw:
        proposals: list[tuple[Box, float | None, float]] = []
        for candidate in row["candidates"]:
            coverage = persistence.get(id(candidate), 0.0)
            if (row["group"] not in unsafe_groups and max_persistence > 0
                    and coverage >= max_persistence and len(groups[row["group"]]) >= 8):
                persistent_rejects += 1
                continue
            energy = candidate["motion"]
            if energy is not None and energy < min_motion:
                static_rejects += 1
                rejected_motion.append(energy)
                continue
            if energy is not None:
                kept_motion.append(energy)
            proposals.append((candidate["box"], energy, coverage))

        total_existing += len(row["existing"])
        total_proposed += len(proposals)
        total_suspect += len(row["suspects"])
        per_image.append(
            {
                "image": row["path"].name,
                "group": row["group"],
                "width": row["width"],
                "height": row["height"],
                "existing": len(row["existing"]),
                "proposed": len(proposals),
                "suspect_existing": len(row["suspects"]),
                "completed_total": len(row["existing"]) + len(proposals),
                "proposals": [
                    {
                        "xyxy": [round(v, 2) for v in (b.x1, b.y1, b.x2, b.y2)],
                        "score": round(b.score, 4),
                        "votes": b.votes,
                        "motion": None if e is None else round(e, 2),
                        "persistence": round(c, 3),
                    }
                    for b, e, c in proposals
                ],
            }
        )

    def spread(values: list[float]) -> dict[str, float] | None:
        if not values:
            return None
        ordered = sorted(values)
        return {
            "n": len(ordered),
            "p10": round(ordered[len(ordered) // 10], 2),
            "median": round(stats.median(ordered), 2),
            "p90": round(ordered[9 * len(ordered) // 10], 2),
        }

    frames = max(1, len(per_image))
    return {
        "split": split,
        "frames": len(per_image),
        "source_groups": len(groups),
        "temporal_veto_disabled_groups": sorted(unsafe_groups),
        "temporal_filters_disabled_groups": sorted(unsafe_groups),
        "existing_boxes": total_existing,
        "proposed_boxes": total_proposed,
        "rejected_persistent": persistent_rejects,
        "rejected_static": static_rejects,
        "rejected_person": person_rejects,
        "suspect_existing_boxes": total_suspect,
        "density_before": round(total_existing / frames, 3),
        "density_after": round((total_existing + total_proposed) / frames, 3),
        "motion_energy": {
            "known_robots": spread(gt_motion),
            "accepted_proposals": spread(kept_motion),
            "rejected_as_static": spread(rejected_motion),
        },
        "per_image": per_image,
    }


def apply_split(dataset: Path, out: Path, split: str, report: dict[str, Any]) -> None:
    assert_not_holdout(dataset)
    assert_not_holdout(out)
    if overlaps(dataset, out):
        raise ValueError("Proposal output overlaps its source.")
    (out / "images" / split).mkdir(parents=True, exist_ok=True)
    (out / "labels" / split).mkdir(parents=True, exist_ok=True)
    by_name = {row["image"]: row for row in report["per_image"]}

    for image_path in dataset_images(dataset, split):
        row = by_name.get(image_path.name)
        if row is None:
            continue
        shutil.copy2(image_path, out / "images" / split / image_path.name)

        src_label = dataset / "labels" / split / f"{image_path.stem}.txt"
        lines = [ln for ln in (src_label.read_text().splitlines() if src_label.is_file() else []) if ln.strip()]
        for proposal in row["proposals"]:
            x1, y1, x2, y2 = proposal["xyxy"]
            lines.append(to_yolo_line(Box(x1, y1, x2, y2), row["width"], row["height"]))
        (out / "labels" / split / f"{image_path.stem}.txt").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Propose the robot boxes a dataset is missing.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--split", nargs="+", choices=["train", "val", "test"], default=["train"])
    parser.add_argument("--model", default="media/models/frc_robot_detector_v2.pt")
    parser.add_argument("--scales", type=int, nargs="+", default=list(DEFAULT_SCALES))
    parser.add_argument("--conf", type=float, default=PROPOSAL_CONF_FLOOR)
    parser.add_argument("--match-iou", type=float, default=0.5)
    parser.add_argument(
        "--min-votes",
        type=int,
        default=3,
        help="Views (scales x {plain, TTA}) that must agree before a box is proposed.",
    )
    parser.add_argument("--min-score", type=float, default=0.05)
    parser.add_argument(
        "--min-motion",
        type=float,
        default=0.0,
        help="Reject proposals whose patch differs from the view's median background by "
        "less than this (grey levels). 0 disables the test. Run once at 0 and read "
        "motion_energy.known_robots off the report to choose it from data.",
    )
    parser.add_argument(
        "--max-persistence",
        type=float,
        default=0.25,
        help="Drop a proposal whose position keeps firing in this fraction of a camera "
        "view's frames -- that is field furniture, not a robot. 0 disables.",
    )
    parser.add_argument(
        "--person-model",
        default="yolo11m.pt",
        help="Generic COCO detector used to veto proposals that are people. '' disables.",
    )
    parser.add_argument("--person-cover", type=float, default=0.5)
    parser.add_argument("--bg-sample", type=int, default=40, help="Frames per view in the median background.")
    parser.add_argument("--limit", type=int, default=None, help="First N images per split, for pilots.")
    parser.add_argument("--apply", action="store_true", help="Write an unreviewed proposal copy; human review is still required.")
    parser.add_argument("--out", default=None, help="Destination for --apply.")
    parser.add_argument("--report", default=None, help="Write the review manifest here.")
    args = parser.parse_args()

    dataset = Path(args.dataset)
    if not dataset.is_absolute():
        dataset = BACKEND_ROOT / dataset
    try:
        assert_not_holdout(dataset)
        if len(set(args.split)) != len(args.split):
            raise ValueError("Each split may be requested only once.")
        for split in args.split:
            if not dataset_images(dataset, split):
                raise ValueError(f"No images found in {split}.")
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    model_path = Path(args.model)
    if not model_path.is_absolute():
        model_path = BACKEND_ROOT / model_path
    if not model_path.is_file():
        print(json.dumps({"ok": False, "error": f"Model not found: {model_path}"}))
        return 2

    out: Path | None = None
    if args.apply:
        if not args.out:
            print(json.dumps({"ok": False, "error": "--apply requires --out"}))
            return 2
        out = Path(args.out)
        if not out.is_absolute():
            out = BACKEND_ROOT / out
        try:
            assert_fresh_output(out, [dataset])
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 2

    report_path = Path(args.report).resolve() if args.report else None
    if report_path is not None:
        try:
            assert_not_holdout(report_path)
            if overlaps(report_path, dataset) or report_path.exists():
                raise ValueError("Review report must be new and outside the source dataset.")
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 2

    from ultralytics import YOLO

    model = YOLO(str(model_path))
    person_model = YOLO(str(args.person_model)) if str(args.person_model).strip() else None
    scales = tuple(int(s) for s in args.scales)

    payload: dict[str, Any] = {
        "ok": True,
        "status": "needs_review",
        "dataset": str(dataset),
        "model": str(model_path),
        "views": len(scales) * 2,
        "scales": list(scales),
        "conf": args.conf,
        "min_votes": args.min_votes,
        "min_score": args.min_score,
        "min_motion": args.min_motion,
        "max_persistence": args.max_persistence,
        "person_model": str(args.person_model),
        "person_cover": args.person_cover,
        "splits": [],
    }
    for split in args.split:
        print(f"[{split}] scanning...", file=sys.stderr, flush=True)
        report = process_split(
            model,
            dataset,
            split,
            scales,
            args.conf,
            args.match_iou,
            args.min_votes,
            args.min_score,
            args.min_motion,
            args.max_persistence,
            person_model,
            args.person_cover,
            args.bg_sample,
            args.limit,
        )
        if out is not None:
            apply_split(dataset, out, split, report)
        payload["splits"].append(report)

    if out is not None:
        (out / "dataset.yaml").write_text(
            f"path: {out}\ntrain: images/train\nval: images/val\n\nnames:\n  0: robot\n",
            encoding="utf-8",
        )
        payload["out"] = str(out)
        # Keep review provenance beside every pseudo-labelled copy, even when the
        # caller does not request a separate report. This is never an accepted audit.
        (out / "PROPOSALS.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    summary = {
        k: v
        for k, v in payload.items()
        if k != "splits"
    }
    summary["splits"] = [
        {k: v for k, v in split.items() if k != "per_image"} for split in payload["splits"]
    ]
    print(json.dumps(summary, indent=2))

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
