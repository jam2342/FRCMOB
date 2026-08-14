from __future__ import annotations

import unittest

import numpy as np

from app.services.auto_scout.on_device import (
    Detection,
    Frame,
    OcrRead,
    assemble_points_by_team,
    bbox_to_field,
    calibrate_from_taps,
    carry_pose,
    estimate_homography,
    estimate_homography_ransac,
    field_reference_corners,
    floor_contact_point,
    produce_track_points,
    project_point,
    reprojection_rmse,
    vote_track_identity,
)
from app.services.game_config import load_game_config
from app.services.auto_scout.shift_play import analyze_match_shift_play

# A known, non-degenerate perspective homography (image px -> field metres) used to
# generate exact synthetic correspondences for the recovery tests.
_H_TRUTH = np.array(
    [[0.008, 0.001, 0.5], [0.0005, 0.007, 0.3], [1e-5, 2e-5, 1.0]],
    dtype=np.float64,
)
_IMG_PTS = [
    (100, 100),
    (1800, 120),
    (1750, 1000),
    (120, 980),
    (960, 540),
    (500, 300),
    (300, 800),
    (1500, 650),
]

# Verified zone anchors (field metres) from season_template.json.
RED_SCORE = (11.902, 4.021)
RED_DEPOT = (14.839, 7.168)
BLUE_SCORE = (4.611, 4.021)
NEUTRAL = (8.271, 4.035)


def _bbox_at(field_xy: tuple[float, float]) -> tuple[float, float, float, float]:
    # bbox whose floor-contact (bottom-centre) is exactly field_xy under identity H.
    x, y = field_xy
    return (x - 0.5, y - 1.0, x + 0.5, y)


class GeometryTests(unittest.TestCase):
    def test_homography_recovers_known_perspective_map(self):
        field_pts = [project_point(_H_TRUTH, u, v) for u, v in _IMG_PTS]
        h_est = estimate_homography(_IMG_PTS, field_pts)
        # A held-out point must project to the same place under the recovered map.
        truth = project_point(_H_TRUTH, 1000, 700)
        est = project_point(h_est, 1000, 700)
        self.assertAlmostEqual(truth[0], est[0], places=6)
        self.assertAlmostEqual(truth[1], est[1], places=6)

    def test_homography_recovers_from_minimum_four_points(self):
        img = _IMG_PTS[:4]
        field_pts = [project_point(_H_TRUTH, u, v) for u, v in img]
        h_est = estimate_homography(img, field_pts)
        est = project_point(h_est, 1000, 700)
        truth = project_point(_H_TRUTH, 1000, 700)
        self.assertAlmostEqual(truth[0], est[0], places=4)
        self.assertAlmostEqual(truth[1], est[1], places=4)

    def test_homography_requires_four_correspondences(self):
        with self.assertRaises(ValueError):
            estimate_homography(_IMG_PTS[:3], [(0, 0), (1, 0), (0, 1)])

    def test_homography_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            estimate_homography(_IMG_PTS[:4], [(0, 0), (1, 0)])

    def test_floor_contact_is_bbox_bottom_center(self):
        self.assertEqual(floor_contact_point((10.0, 20.0, 30.0, 40.0)), (20.0, 40.0))

    def test_bbox_projects_to_field_and_zone_tags(self):
        h = np.eye(3)
        fx, fy = bbox_to_field(h, _bbox_at(RED_SCORE))
        self.assertAlmostEqual(fx, RED_SCORE[0], places=6)
        self.assertAlmostEqual(fy, RED_SCORE[1], places=6)

    def test_reprojection_rmse_zero_for_exact_fit_high_for_outlier(self):
        field_pts = [project_point(_H_TRUTH, u, v) for u, v in _IMG_PTS]
        h_est = estimate_homography(_IMG_PTS, field_pts)
        self.assertLess(reprojection_rmse(h_est, _IMG_PTS, field_pts), 1e-6)
        # Corrupt one correspondence: the plain-DLT homography can no longer satisfy all
        # of them, so RMSE rises -> the on-device loop would reject this pose.
        corrupted = list(field_pts)
        corrupted[0] = (corrupted[0][0] + 3.0, corrupted[0][1] - 2.0)
        h_bad = estimate_homography(_IMG_PTS, corrupted)
        self.assertGreater(reprojection_rmse(h_bad, _IMG_PTS, corrupted), 0.1)

    def test_ransac_drops_outlier_correspondence(self):
        # One misdetected AprilTag corner would wreck a plain DLT fit; RANSAC rejects it.
        field_pts = [project_point(_H_TRUTH, u, v) for u, v in _IMG_PTS]
        noisy = list(field_pts)
        noisy[2] = (noisy[2][0] + 3.0, noisy[2][1] - 2.0)
        h_est, inliers = estimate_homography_ransac(_IMG_PTS, noisy, seed=3)
        self.assertNotIn(2, inliers)  # the outlier was rejected
        self.assertGreaterEqual(len(inliers), 4)
        # Recovered pose still matches truth despite the bad correspondence.
        truth = project_point(_H_TRUTH, 1000, 700)
        est = project_point(h_est, 1000, 700)
        self.assertAlmostEqual(truth[0], est[0], places=3)
        self.assertAlmostEqual(truth[1], est[1], places=3)

    def test_ransac_survives_two_outliers(self):
        field_pts = [project_point(_H_TRUTH, u, v) for u, v in _IMG_PTS]
        noisy = list(field_pts)
        noisy[1] = (noisy[1][0] - 4.0, noisy[1][1] + 1.5)
        noisy[4] = (noisy[4][0] + 2.0, noisy[4][1] + 2.5)
        h_est, inliers = estimate_homography_ransac(_IMG_PTS, noisy, seed=11)
        self.assertEqual(set(inliers), {0, 2, 3, 5, 6, 7})  # both outliers dropped
        truth = project_point(_H_TRUTH, 800, 600)
        est = project_point(h_est, 800, 600)
        self.assertAlmostEqual(truth[0], est[0], places=3)
        self.assertAlmostEqual(truth[1], est[1], places=3)


class TrackProductionTests(unittest.TestCase):
    def test_produce_track_points_projects_zone_tags_and_speeds(self):
        h = np.eye(3)
        frames = [
            Frame(time_sec=0.0, homography=h, detections=[Detection(1, _bbox_at(RED_SCORE))]),
            Frame(time_sec=1.0, homography=h, detections=[Detection(1, _bbox_at(RED_DEPOT))]),
        ]
        out = produce_track_points(frames)
        self.assertIn(1, out)
        pts = out[1]
        self.assertEqual(pts[0].zone_key, "red_alliance_scoring_zone")
        self.assertEqual(pts[1].zone_key, "red_loading_depot_zone")
        self.assertIsNone(pts[0].speed_mps)  # first sample has no predecessor
        expected = float(np.hypot(RED_DEPOT[0] - RED_SCORE[0], RED_DEPOT[1] - RED_SCORE[1]))
        self.assertAlmostEqual(pts[1].speed_mps, expected, places=4)

    def test_blind_frame_before_any_pose_is_skipped(self):
        # A pose-lost frame with no prior good pose to carry has nothing to project with.
        frames = [
            Frame(time_sec=0.0, homography=None, detections=[Detection(1, _bbox_at(RED_SCORE))]),
            Frame(time_sec=1.0, homography=np.eye(3), detections=[Detection(1, _bbox_at(RED_SCORE))]),
        ]
        out = produce_track_points(frames)
        self.assertEqual(len(out[1]), 1)  # only the frame with a valid pose contributes

    def test_low_confidence_detections_are_dropped(self):
        h = np.eye(3)
        frames = [
            Frame(
                0.0,
                h,
                [
                    Detection(1, _bbox_at(RED_SCORE), confidence=0.9),
                    Detection(2, _bbox_at(NEUTRAL), confidence=0.1),  # weak box -> dropped
                ],
            )
        ]
        out = produce_track_points(frames, min_detection_confidence=0.5)
        self.assertIn(1, out)
        self.assertNotIn(2, out)

    def test_assemble_drops_unresolved_and_merges_by_team(self):
        h = np.eye(3)
        frames = [
            Frame(0.0, h, [Detection(1, _bbox_at(RED_SCORE)), Detection(9, _bbox_at(NEUTRAL))]),
            Frame(1.0, h, [Detection(2, _bbox_at(RED_SCORE))]),
        ]
        tracks = produce_track_points(frames)
        # track 1 and 2 are the same robot (fragmented); track 9 never resolved.
        by_team = assemble_points_by_team(tracks, {1: "frc1111", 2: "frc1111"})
        self.assertEqual(set(by_team), {"frc1111"})
        self.assertEqual(len(by_team["frc1111"]), 2)
        self.assertNotIn("frc9999", by_team)

    def test_end_to_end_feeds_shift_play_engine(self):
        # Produce tracks for a clear red attacker + a blue opponent, then run the
        # real shift-play engine over them: the on-device output must be consumable
        # and yield a sensible offense rating for the attacker.
        h = np.eye(3)
        frames: list[Frame] = []
        attack_windows = [(0, 30), (30, 55), (80, 105), (130, 160)]  # both + red-active

        def in_window(t: float) -> bool:
            return any(ws <= t < we for ws, we in attack_windows)

        t = 0.0
        while t < 160.0:
            dets = []
            if in_window(t):
                # alternate scoring zone / depot to register cycles
                zone = RED_SCORE if int(t) % 5 < 4 else RED_DEPOT
                dets.append(Detection(1, _bbox_at(zone)))
            else:
                dets.append(Detection(1, _bbox_at(NEUTRAL)))
            dets.append(Detection(2, _bbox_at(BLUE_SCORE)))  # opponent present throughout
            frames.append(Frame(time_sec=round(t, 2), homography=h, detections=dets))
            t += 0.5

        tracks = produce_track_points(frames)
        points_by_team = assemble_points_by_team(tracks, {1: "frc1111", 2: "frc2222"})
        results = analyze_match_shift_play(
            points_by_team=points_by_team,
            alliance_by_team={"frc1111": "red", "frc2222": "blue"},
        )
        self.assertIn("frc1111", results)
        attacker = results["frc1111"]
        self.assertGreaterEqual(attacker["offense"]["level_1_5"], 3)
        self.assertGreater(sum(sum(row) for row in attacker["heatmaps"]["attack"]), 0)


class PoseFallbackAndSmoothingTests(unittest.TestCase):
    def test_pose_fallback_carries_last_good_within_staleness(self):
        h = np.eye(3)
        frames = [
            Frame(0.0, h, [Detection(1, _bbox_at(RED_SCORE))]),
            Frame(0.3, None, [Detection(1, _bbox_at(RED_SCORE))]),  # blind, within 0.5s cap
            Frame(2.0, None, [Detection(1, _bbox_at(RED_SCORE))]),  # blind, beyond cap
        ]
        out = produce_track_points(frames, max_pose_staleness_sec=0.5)
        self.assertEqual([round(p.time_sec, 1) for p in out[1]], [0.0, 0.3])

    def test_pose_fallback_disabled_drops_blind_frames(self):
        h = np.eye(3)
        frames = [
            Frame(0.0, h, [Detection(1, _bbox_at(RED_SCORE))]),
            Frame(0.3, None, [Detection(1, _bbox_at(RED_SCORE))]),
        ]
        out = produce_track_points(frames, max_pose_staleness_sec=None)
        self.assertEqual(len(out[1]), 1)

    def test_velocity_spike_is_rejected(self):
        h = np.eye(3)
        # Robot sits at RED_SCORE; one frame snaps ~7m away and back -> projection glitch.
        seq = [RED_SCORE, RED_SCORE, BLUE_SCORE, RED_SCORE, RED_SCORE]
        frames = [Frame(round(i * 0.1, 2), h, [Detection(1, _bbox_at(p))]) for i, p in enumerate(seq)]
        out = produce_track_points(frames, smooth_window=1)  # isolate spike rejection
        self.assertEqual(len(out[1]), 4)
        self.assertNotIn("blue_alliance_scoring_zone", [p.zone_key for p in out[1]])

    def test_sustained_move_is_preserved(self):
        h = np.eye(3)
        # Robot drives to the depot and STAYS: a real move, must not be dropped as a spike.
        seq = [RED_SCORE, RED_SCORE, RED_DEPOT, RED_DEPOT, RED_DEPOT]
        frames = [Frame(round(i * 0.1, 2), h, [Detection(1, _bbox_at(p))]) for i, p in enumerate(seq)]
        out = produce_track_points(frames, smooth_window=1)
        self.assertEqual(len(out[1]), 5)
        self.assertTrue(any(p.zone_key == "red_loading_depot_zone" for p in out[1]))

    def test_median_smoothing_reduces_jitter(self):
        h = np.eye(3)
        jitter = [(0.2, 0.0), (-0.2, 0.0), (0.25, 0.0), (-0.25, 0.0), (0.2, 0.0)]
        seq = [(RED_SCORE[0] + dx, RED_SCORE[1] + dy) for dx, dy in jitter]
        frames = [Frame(round(i * 0.1, 2), h, [Detection(1, _bbox_at(p))]) for i, p in enumerate(seq)]
        out = produce_track_points(frames, max_speed_mps=None, smooth_window=3)
        raw_spread = max(s[0] for s in seq) - min(s[0] for s in seq)
        smoothed_spread = max(p.field_x for p in out[1]) - min(p.field_x for p in out[1])
        self.assertLess(smoothed_spread, raw_spread)


class CalibrationAndPoseCarryTests(unittest.TestCase):
    def test_field_reference_corners_match_config_dims(self):
        f = load_game_config().field
        corners = field_reference_corners()
        self.assertEqual(corners[0], (0.0, 0.0))
        self.assertAlmostEqual(corners[2][0], float(f.length_m))
        self.assertAlmostEqual(corners[2][1], float(f.width_m))

    def test_calibrate_from_four_taps_recovers_pose(self):
        field = [project_point(_H_TRUTH, u, v) for u, v in _IMG_PTS[:4]]
        cal = calibrate_from_taps(_IMG_PTS[:4], field)
        self.assertLess(cal.rmse_m, 1e-6)
        self.assertEqual(set(cal.inliers), {0, 1, 2, 3})
        est = project_point(cal.homography, 1000, 700)
        truth = project_point(_H_TRUTH, 1000, 700)
        self.assertAlmostEqual(est[0], truth[0], places=4)
        self.assertAlmostEqual(est[1], truth[1], places=4)

    def test_calibrate_ransac_drops_a_sloppy_tap(self):
        field = [project_point(_H_TRUTH, u, v) for u, v in _IMG_PTS]
        field[2] = (field[2][0] + 3.0, field[2][1] - 2.0)  # one sloppy tap
        cal = calibrate_from_taps(_IMG_PTS, field)
        self.assertNotIn(2, cal.inliers)
        self.assertLess(cal.rmse_m, 0.05)

    def test_calibrate_requires_four_taps(self):
        with self.assertRaises(ValueError):
            calibrate_from_taps(_IMG_PTS[:3], [(0, 0), (1, 0), (0, 1)])

    def test_carry_pose_keeps_static_point_fixed_under_camera_motion(self):
        # base pose: pixels/100 -> field metres; a static field point F sits at px (500,300)
        base = np.array([[0.01, 0, 0], [0, 0.01, 0], [0, 0, 1]], dtype=np.float64)
        F = project_point(base, 500, 300)
        # frame 1: camera pans, so F's pixel moves by the same motion m1
        m1 = np.array([[1, 0, 50.0], [0, 1, 20.0], [0, 0, 1]])
        p1 = (550.0, 320.0)  # m1 @ (500,300)
        h1 = carry_pose(base, m1)
        self.assertAlmostEqual(project_point(h1, *p1)[0], F[0], places=6)
        self.assertAlmostEqual(project_point(h1, *p1)[1], F[1], places=6)
        # frame 2: another motion; F must still resolve to the same field coord
        m2 = np.array([[1.0, 0, -30], [0, 1, 10], [0, 0, 1]])
        p2v = m2 @ np.array([p1[0], p1[1], 1.0])
        p2 = (p2v[0] / p2v[2], p2v[1] / p2v[2])
        h2 = carry_pose(h1, m2)
        self.assertAlmostEqual(project_point(h2, *p2)[0], F[0], places=6)
        self.assertAlmostEqual(project_point(h2, *p2)[1], F[1], places=6)


class IdentityVotingTests(unittest.TestCase):
    CANDIDATES = ["frc1234", "frc5678", "frc1812", "frc254", "frc118", "frc2056"]

    def test_clean_reads_resolve_to_correct_team(self):
        vote = vote_track_identity([OcrRead("1234")] * 5, self.CANDIDATES)
        self.assertTrue(vote.resolved)
        self.assertEqual(vote.team_key, "frc1234")
        self.assertGreater(vote.confidence, 0.45)
        # scores is a normalized vote-share distribution (rounded for display) with the
        # winner on top; rounded shares sum to ~1.0.
        self.assertAlmostEqual(sum(vote.scores.values()), 1.0, places=2)
        self.assertEqual(max(vote.scores, key=lambda k: vote.scores[k]), "frc1234")

    def test_noisy_partial_reads_resolve_via_temporal_voting(self):
        reads = [OcrRead("12"), OcrRead("123"), OcrRead("234"), OcrRead("1234"), OcrRead("1z34")]
        vote = vote_track_identity(reads, self.CANDIDATES)
        self.assertTrue(vote.resolved)
        self.assertEqual(vote.team_key, "frc1234")

    def test_ambiguous_reads_defer_to_tap_id(self):
        # "123" / "12" are equally close to both candidates -> no confident winner.
        vote = vote_track_identity([OcrRead("123"), OcrRead("12")], ["frc1234", "frc1235"])
        self.assertFalse(vote.resolved)
        self.assertIsNone(vote.team_key)

    def test_empty_reads_unresolved(self):
        self.assertFalse(vote_track_identity([], self.CANDIDATES).resolved)
        self.assertFalse(vote_track_identity([OcrRead("")], self.CANDIDATES).resolved)

    def test_out_of_set_reads_are_rejected(self):
        # A read matching no candidate in the closed set casts no vote.
        vote = vote_track_identity([OcrRead("9999")] * 4, ["frc1234", "frc5678"])
        self.assertFalse(vote.resolved)
        self.assertIsNone(vote.team_key)

    def test_confident_reads_outweigh_noisy_wrong_reads(self):
        reads = [OcrRead("5678", confidence=0.1)] * 3 + [OcrRead("1234", confidence=1.0)] * 3
        vote = vote_track_identity(reads, self.CANDIDATES)
        self.assertTrue(vote.resolved)
        self.assertEqual(vote.team_key, "frc1234")


if __name__ == "__main__":
    unittest.main()
