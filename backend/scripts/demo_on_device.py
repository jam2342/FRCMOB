#!/usr/bin/env python3
# Reference demonstration of the on-device track-production layer (Part B core) on
# synthetic data — no DB, no video, no model weights, no device. It runs the full
# device-free chain end to end:
#
#   per-frame homography (RANSAC)  ->  floor-contact projection  ->  field coords + zone
#   ->  TrackPoints  ->  closed-set-of-6 bumper-OCR vote  ->  shift-play engine
#
# This proves the wiring is exact and consumable by the existing shift-play engine.
# It does NOT de-risk the device-bound pieces (in-browser YOLO, AprilTag solvePnP,
# real bumper-OCR read rate) — those still need real handheld footage + weights.
#
#   PYTHONPATH=. python scripts/demo_on_device.py
from __future__ import annotations

# ruff: noqa: E402

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np

from app.services.auto_scout.on_device import (
    Detection,
    Frame,
    OcrRead,
    assemble_points_by_team,
    estimate_homography_ransac,
    project_point,
    produce_track_points,
    reprojection_rmse,
    vote_track_identity,
)
from app.services.auto_scout.shift_play import analyze_match_shift_play

# Verified zone anchors (field metres) from season_template.json.
RED_SCORE = (11.902, 4.021)
RED_DEPOT = (14.839, 7.168)
BLUE_SCORE = (4.611, 4.021)
NEUTRAL = (8.271, 4.035)

# A plausible per-frame perspective map (image px -> field m). On a real device this
# is rebuilt every frame from AprilTag corners via solvePnP; here it is fixed.
H_TRUTH = np.array([[0.008, 0.001, 0.5], [0.0005, 0.007, 0.3], [1e-5, 2e-5, 1.0]])
IMG_PTS = [(100, 100), (1800, 120), (1750, 1000), (120, 980), (960, 540), (500, 300)]


def _bbox_for_field(field_xy, h):
    # Invert the homography for the floor-contact point so a detection's bbox
    # bottom-centre projects back to the desired field location under H.
    x, y = field_xy
    h_inv = np.linalg.inv(h)
    u, v = project_point(h_inv, x, y)
    return (u - 20, v - 60, u + 20, v)  # ~40x60 px bbox, bottom-centre at (u, v)


def main() -> None:
    # 1) Recover the per-frame homography from AprilTag-style correspondences, with
    #    one corner deliberately misdetected (a real detector hands us these). RANSAC
    #    drops it automatically instead of letting it corrupt the whole frame's pose.
    field_pts = [project_point(H_TRUTH, u, v) for u, v in IMG_PTS]
    field_pts_noisy = list(field_pts)
    field_pts_noisy[2] = (field_pts_noisy[2][0] + 2.5, field_pts_noisy[2][1] - 1.8)  # bad tag
    h, inliers = estimate_homography_ransac(IMG_PTS, field_pts_noisy, seed=7)
    print("== Per-frame homography (RANSAC) ==")
    print(f"inliers: {len(inliers)}/{len(IMG_PTS)} (dropped the misdetected tag corner)")
    inlier_img = [IMG_PTS[i] for i in inliers]
    inlier_fld = [field_pts[i] for i in inliers]
    print(f"reprojection RMSE on inliers: {reprojection_rmse(h, inlier_img, inlier_fld):.2e} m")

    # 2) Synthesize a match: red attacker (track 1) + blue opponent (track 2).
    attack_windows = [(0, 30), (30, 55), (80, 105), (130, 160)]  # both + red-active shifts

    def attacking(t):
        return any(ws <= t < we for ws, we in attack_windows)

    frames: list[Frame] = []
    t = 0.0
    while t < 160.0:
        dets = []
        red_zone = (RED_SCORE if int(t) % 5 < 4 else RED_DEPOT) if attacking(t) else NEUTRAL
        dets.append(Detection(track_id=1, bbox=_bbox_for_field(red_zone, h)))
        dets.append(Detection(track_id=2, bbox=_bbox_for_field(BLUE_SCORE, h)))
        frames.append(Frame(time_sec=round(t, 2), homography=h, detections=dets))
        t += 0.5

    # 3) Detections -> field-coordinate TrackPoints.
    tracks = produce_track_points(frames)
    print("\n== Track production ==")
    for tid, pts in sorted(tracks.items()):
        zones = {p.zone_key for p in pts}
        print(f"track {tid}: {len(pts)} points, zones touched: {sorted(z for z in zones if z)}")

    # 4) Resolve identity from shaky bumper OCR as a closed-set-of-6 temporal vote.
    candidates = ["frc1111", "frc2222", "frc3333", "frc4444", "frc5555", "frc6666"]
    noisy_reads_red = [OcrRead("11"), OcrRead("111"), OcrRead("1111"), OcrRead("1i11"), OcrRead("1111")]
    noisy_reads_blue = [OcrRead("22"), OcrRead("222"), OcrRead("2222"), OcrRead("z222")]
    v1 = vote_track_identity(noisy_reads_red, candidates)
    v2 = vote_track_identity(noisy_reads_blue, candidates)
    print("\n== Bumper-OCR identity vote (closed set of 6) ==")
    print(f"track 1 -> {v1.team_key} (conf {v1.confidence}, resolved={v1.resolved})")
    print(f"track 2 -> {v2.team_key} (conf {v2.confidence}, resolved={v2.resolved})")

    identities = {}
    if v1.team_key:
        identities[1] = v1.team_key
    if v2.team_key:
        identities[2] = v2.team_key

    # 5) Feed the identical shift-play engine the on-device output.
    points_by_team = assemble_points_by_team(tracks, identities)
    results = analyze_match_shift_play(
        points_by_team=points_by_team,
        alliance_by_team={"frc1111": "red", "frc2222": "blue"},
    )
    print("\n== Shift-play engine over on-device tracks ==")
    for team, res in sorted(results.items()):
        off = res["offense"]
        deff = res["defense"]
        attack_cells = sum(sum(row) for row in res["heatmaps"]["attack"])
        print(
            f"{team} ({res['alliance']}): offense {off['level_1_5']}/5 (conf {off['confidence_0_1']}) | "
            f"defense {deff['level_1_5']}/5 assessable={deff['assessable']} | attack heatmap cells={attack_cells}"
        )


if __name__ == "__main__":
    main()
