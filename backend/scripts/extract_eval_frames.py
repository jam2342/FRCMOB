#!/usr/bin/env python3
# Turn match videos into an unlabelled YOLO-layout frame set.
#
# Two jobs, same mechanics:
#
#   * A second evaluation set. The locked Einstein holdout is 224 frames from one venue,
#     and every go/no-go decision about every future model rests on it. All five events
#     in the training corpus are Texas district gyms; the holdout is a championship arena
#     under stage lighting. Nothing currently measures a model against a third kind of
#     room, so venue overfit would be invisible.
#   * A self-training corpus. Every FRC event is streamed and the labelled set covers 21
#     match videos. The constraint on more training data has never been footage.
#
# Frames are sampled evenly across the middle of the clip, skipping the opening and
# closing seconds where broadcasts show graphics, replays and empty fields rather than a
# match in progress.
#
#   PYTHONPATH=. python scripts/extract_eval_frames.py \
#       --clips /tmp/clips --out media/datasets/frc_venue_shift_eval --per-clip 25
from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import math
import subprocess  # nosec B404 - fixed argv, no shell, local ffmpeg only
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.dataset_safety import assert_contained, assert_fresh_output, assert_not_holdout


def probe_duration(clip: Path) -> float:
    try:
        out = subprocess.run(  # nosec B603 - fixed argv, no shell
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(clip),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        duration = float((out.stdout or "0").strip())
        return duration if out.returncode == 0 and math.isfinite(duration) else 0.0
    except Exception:
        return 0.0


def extract(clip: Path, out_dir: Path, count: int, skip_head: float, skip_tail: float) -> list[Path]:
    duration = probe_duration(clip)
    if duration <= 0:
        return []
    start = min(skip_head, duration * 0.15)
    end = max(start + 1.0, duration - min(skip_tail, duration * 0.15))
    step = (end - start) / max(1, count)
    written: list[Path] = []
    for index in range(count):
        timestamp = start + step * (index + 0.5)
        target = out_dir / f"{clip.stem}_t{int(timestamp * 1000):07d}.jpg"
        if target.exists():
            written.append(target)
            continue
        result = subprocess.run(  # nosec B603 - fixed argv, no shell
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(clip),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(target),
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            written.append(target)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract evenly spaced frames from match clips.")
    parser.add_argument("--clips", required=True, help="Directory of video files.")
    parser.add_argument("--out", required=True, help="Destination dataset directory.")
    parser.add_argument("--split", choices=["train", "val", "test"], default="val")
    parser.add_argument("--per-clip", type=int, default=25)
    parser.add_argument("--skip-head-sec", type=float, default=25.0)
    parser.add_argument("--skip-tail-sec", type=float, default=20.0)
    args = parser.parse_args()

    clips_dir = Path(args.clips)
    if not clips_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"No clips directory: {clips_dir}"}))
        return 2
    out = Path(args.out)
    if not out.is_absolute():
        out = BACKEND_ROOT / out
    try:
        assert_not_holdout(clips_dir)
        assert_fresh_output(out, [clips_dir])
        if args.per_clip <= 0 or any(
            not math.isfinite(v) or v < 0 for v in (args.skip_head_sec, args.skip_tail_sec)
        ):
            raise ValueError("Frame count must be positive; skip times must be finite and nonnegative.")
        clips = sorted(
            p for p in clips_dir.iterdir() if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
        )
        if not clips or len({p.stem for p in clips}) != len(clips):
            raise ValueError("Need clips with distinct stems; duplicate names would overwrite frames.")
        for clip in clips:
            assert_contained(clip, clips_dir)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    image_dir = out / "images" / args.split
    label_dir = out / "labels" / args.split
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)

    per_clip: list[dict] = []
    for clip in clips:
        frames = extract(
            clip, image_dir, int(args.per_clip), float(args.skip_head_sec), float(args.skip_tail_sec)
        )
        if len(frames) != args.per_clip:
            print(json.dumps({"ok": False, "error": f"Incomplete extraction for {clip.name}: "
                              f"{len(frames)}/{args.per_clip} frames", "out": str(out)}))
            return 1
        for frame in frames:
            # Empty label files, so the layout is a valid YOLO dataset before labelling.
            # A missing file and an empty file mean the same thing to ultralytics, but an
            # explicit empty file makes "not labelled yet" visible in a directory listing.
            (label_dir / f"{frame.stem}.txt").touch()
        per_clip.append({"clip": clip.name, "duration_sec": round(probe_duration(clip), 1), "frames": len(frames)})

    (out / "dataset.yaml").write_text(
        f"path: {out}\ntrain: images/train\nval: images/val\ntest: images/test\n\nnames:\n  0: robot\n",
        encoding="utf-8",
    )
    (out / "UNLABELLED.json").write_text(
        json.dumps({"status": "unlabelled", "split": args.split, "per_clip": per_clip}, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(out),
                "split": args.split,
                "clips": len(clips),
                "frames": sum(row["frames"] for row in per_clip),
                "per_clip": per_clip,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
