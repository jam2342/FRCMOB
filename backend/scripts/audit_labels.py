#!/usr/bin/env python3
# Make a labelled dataset reviewable by eye, and record the verdict.
#
# docs/LABELING_STANDARD.md sets a quality gate -- 200 frames audited, >=95% label
# recall, density within +-0.3 of the reference, <=2% false positives -- which is
# unenforceable without a way to actually look at the labels. This produces that:
#
#   * frames/   full frames with every box drawn, for spotting robots that were MISSED.
#               Density statistics cannot see a missing box; only a person can.
#   * boxes/    every box cropped and magnified into a contact sheet, for spotting boxes
#               that are NOT robots. At field scale a referee and a robot are both twenty
#               pixels tall; magnified they are obvious. Hand-counting a sheet like this
#               is what measured the proposer's error rate at ~3-8%.
#   * AUDIT.md  the automatic statistics, plus the two counts a human has to supply.
#
# Read-only with respect to the dataset. Safe to point at the locked holdout.
#
#   PYTHONPATH=. python scripts/audit_labels.py \
#       --dataset media/datasets/frc_v3_completed --split train --sample 200
from __future__ import annotations

# ruff: noqa: E402

import argparse
import csv
import hashlib
import statistics
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

GREEN = (0, 235, 70)
CELL = 150
COLS = 8


def read_boxes(label_path: Path, width: int, height: int) -> list[tuple[float, float, float, float]]:
    if not label_path.is_file():
        return []
    out = []
    for line in label_path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cx, cy, bw, bh = (float(v) for v in parts[1:5])
        out.append(
            ((cx - bw / 2) * width, (cy - bh / 2) * height, (cx + bw / 2) * width, (cy + bh / 2) * height)
        )
    return out


def pick(images: list[Path], sample: int) -> list[Path]:
    # Deterministic spread rather than a random sample, so re-running the audit on the
    # same dataset shows the same frames and a fix can be checked against the finding.
    if sample >= len(images):
        return images
    ranked = sorted(images, key=lambda p: hashlib.sha256(p.name.encode()).hexdigest())
    return sorted(ranked[:sample])


def render_frames(images: list[Path], label_dir: Path, out_dir: Path, per_sheet: int, width: int) -> int:
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = 0
    for start in range(0, len(images), per_sheet):
        tiles = []
        for image_path in images[start : start + per_sheet]:
            image = Image.open(image_path).convert("RGB")
            draw = ImageDraw.Draw(image)
            boxes = read_boxes(label_dir / f"{image_path.stem}.txt", image.width, image.height)
            for box in boxes:
                draw.rectangle(box, outline=GREEN, width=max(2, image.width // 500))
            scaled = image.resize((width, max(1, round(width * image.height / image.width))))
            header = Image.new("RGB", (width, 24), (16, 16, 19))
            ImageDraw.Draw(header).text(
                (8, 6), f"{image_path.name[:60]}   boxes={len(boxes)}", fill=(232, 232, 232)
            )
            tile = Image.new("RGB", (width, scaled.height + 24))
            tile.paste(header, (0, 0))
            tile.paste(scaled, (0, 24))
            tiles.append(tile)
        if not tiles:
            continue
        canvas = Image.new("RGB", (width, sum(t.height + 6 for t in tiles)), (16, 16, 19))
        y = 0
        for tile in tiles:
            canvas.paste(tile, (0, y))
            y += tile.height + 6
        canvas.save(out_dir / f"frames_{sheets:03d}.jpg", quality=82)
        sheets += 1
    return sheets


def render_boxes(images: list[Path], label_dir: Path, out_dir: Path, per_sheet: int) -> int:
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[tuple[Path, int, tuple[float, float, float, float]]] = []
    for image_path in images:
        with Image.open(image_path) as probe:
            size = probe.size
        for label_index, box in enumerate(read_boxes(label_dir / f"{image_path.stem}.txt", *size)):
            items.append((image_path, label_index, box))

    # Sheet numbers alone cannot be applied back to labels. Preserve their exact
    # source mapping, including the zero-based index in the original label file.
    with (out_dir / "index.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["box_id", "image", "label", "label_index", "x1", "y1", "x2", "y2"])
        for index, (image_path, label_index, box) in enumerate(items):
            writer.writerow([index, image_path.name, f"{image_path.stem}.txt", label_index, *box])

    sheets = 0
    for start in range(0, len(items), per_sheet):
        chunk = items[start : start + per_sheet]
        rows = (len(chunk) + COLS - 1) // COLS
        canvas = Image.new("RGB", (COLS * CELL, rows * (CELL + 16)), (12, 12, 14))
        label_layer = ImageDraw.Draw(canvas)
        cache: dict[Path, "Image.Image"] = {}
        for index, (image_path, _label_index, box) in enumerate(chunk):
            image = cache.get(image_path)
            if image is None:
                image = Image.open(image_path).convert("RGB")
                cache[image_path] = image
            x1, y1, x2, y2 = box
            bw, bh = x2 - x1, y2 - y1
            # Padding matters: a box shown edge to edge gives no context, and context is
            # what separates a robot from a referee.
            px, py = bw * 0.45, bh * 0.45
            crop_x, crop_y = int(max(0, x1 - px)), int(max(0, y1 - py))
            crop = image.crop((crop_x, crop_y, int(min(image.width, x2 + px)),
                               int(min(image.height, y2 + py))))
            if crop.width < 4 or crop.height < 4:
                continue
            scale = min(CELL / crop.width, CELL / crop.height)
            crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))))
            ox = (index % COLS) * CELL + (CELL - crop.width) // 2
            oy = (index // COLS) * (CELL + 16) + (CELL - crop.height) // 2
            canvas.paste(crop, (ox, oy))
            ImageDraw.Draw(canvas).rectangle(
                [ox + int((x1 - crop_x) * scale), oy + int((y1 - crop_y) * scale),
                 ox + int((x2 - crop_x) * scale), oy + int((y2 - crop_y) * scale)],
                outline=GREEN,
                width=2,
            )
            label_layer.text(
                ((index % COLS) * CELL + 4, (index // COLS) * (CELL + 16) + CELL + 2),
                f"#{start + index}",
                fill=(196, 196, 202),
            )
        canvas.save(out_dir / f"boxes_{sheets:03d}.jpg", quality=86)
        sheets += 1
    return sheets


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a labelled dataset for human audit.")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--split", default="train")
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--out", default=None, help="Default: <dataset>/audit")
    parser.add_argument("--frames-per-sheet", type=int, default=4)
    parser.add_argument("--boxes-per-sheet", type=int, default=40)
    parser.add_argument("--frame-width", type=int, default=900)
    parser.add_argument(
        "--reference-density",
        type=float,
        default=5.19,
        help="Boxes/frame the split should land near. Default is the locked Einstein "
        "holdout's, which is the reference for broadcast field-wide footage.",
    )
    args = parser.parse_args()
    if min(args.sample, args.frames_per_sheet, args.boxes_per_sheet, args.frame_width) < 1:
        parser.error("sample and sheet dimensions must be positive")

    dataset = Path(args.dataset)
    if not dataset.is_absolute():
        dataset = BACKEND_ROOT / dataset
    image_dir = dataset / "images" / args.split
    label_dir = dataset / "labels" / args.split
    if not image_dir.is_dir():
        print(f"No images at {image_dir}", file=sys.stderr)
        return 2

    images = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    counts = [len(read_boxes(label_dir / f"{p.stem}.txt", 1, 1)) for p in images]
    total_boxes = sum(counts)
    density = total_boxes / max(1, len(images))
    empty = sum(1 for c in counts if c == 0)

    sample = pick(images, int(args.sample))
    out = Path(args.out) if args.out else dataset / "audit"
    if not out.is_absolute():
        out = BACKEND_ROOT / out
    from scripts.dataset_safety import assert_fresh_output

    # A rerun used to erase the reviewer's filled-in verdict. New output also keeps
    # audit renders and indices paired, and prevents writing inside a locked holdout.
    try:
        assert_fresh_output(out)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    frame_sheets = render_frames(sample, label_dir, out / "frames", int(args.frames_per_sheet), int(args.frame_width))
    box_sheets = render_boxes(sample, label_dir, out / "boxes", int(args.boxes_per_sheet))
    sample_boxes = sum(len(read_boxes(label_dir / f"{p.stem}.txt", 1, 1)) for p in sample)

    delta = density - float(args.reference_density)
    verdict = "PASS" if abs(delta) <= 0.3 else "OUT OF BAND"

    (out / "AUDIT.md").write_text(
        f"""# Label audit — `{dataset.name}` / `{args.split}`

Generated by `scripts/audit_labels.py`. Written against `docs/LABELING_STANDARD.md`.

## Measured automatically

| | |
|---|---|
| Frames | {len(images)} |
| Boxes | {total_boxes} |
| Boxes / frame | **{density:.3f}** |
| Median boxes / frame | {statistics.median(counts) if counts else 0} |
| Frames with no boxes | {empty} ({100 * empty / max(1, len(images)):.1f}%) |
| Reference density | {args.reference_density:.2f} |
| Difference | {delta:+.3f} → **{verdict}** (gate is ±0.30) |

## To review

- `frames/` — {frame_sheets} sheets, {len(sample)} frames. **Look for robots with no box.**
  Density cannot detect a missing label; only a person can.
- `boxes/` — {box_sheets} sheets, {sample_boxes} boxes magnified. **Look for boxes that
  are not robots** — referees, drive teams, spectators, the hubs, alliance walls.
- `boxes/index.csv` maps every numbered crop to its original image and label row.

## To fill in

| Check | Target | Found |
|---|---|---|
| Robots visible in the `frames/` sample | — | |
| Of those, carrying a box | — | |
| **Label recall** | ≥ 95% | |
| Boxes in the `boxes/` sample that are not robots | ≤ 2% | |

Verdict:
Reviewer:
Date:

An unfilled table means this dataset has not been audited. Do not train a model you
intend to deploy on an unaudited dataset — that is exactly how the v2 corpus shipped at
3.31 boxes/frame against a true 5.19.
""",
        encoding="utf-8",
    )
    print(f"frames={len(images)} boxes={total_boxes} density={density:.3f} ({verdict})")
    print(f"wrote {out}/AUDIT.md, {frame_sheets} frame sheets, {box_sheets} box sheets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
