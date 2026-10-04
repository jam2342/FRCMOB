from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "build_v3_dataset", BACKEND_ROOT / "scripts" / "build_v3_dataset.py"
)
build_v3_dataset = importlib.util.module_from_spec(_spec)
sys.modules["build_v3_dataset"] = build_v3_dataset
assert _spec.loader is not None
_spec.loader.exec_module(build_v3_dataset)

base_name = build_v3_dataset.base_name
camera_group = build_v3_dataset.camera_group
assign_splits = build_v3_dataset.assign_splits


class BaseNameTests(unittest.TestCase):
    # frc_yolo_v2 presents 6,540 images that are really 1,635 frames copied four times by
    # Roboflow augmentation. Recovering the original frame identity is what stops the
    # same frame being taught four times and what keeps its copies on one side of a split.
    def test_roboflow_augmentation_copies_collapse_to_one_frame(self):
        copies = [
            "frame_0000_jpg.rf.3307c808eff261f7ea20939dba24b69a_407a21bd550ee9c9",
            "frame_0000_jpg.rf.3f9776330ba58ff27bacaebc5285d547_daf6468d8437ab15",
            "frame_0000_jpg.rf.70192ba0bedc36ee245971dd46616bf7_f400dbf6010bd4fe",
        ]
        self.assertEqual({base_name(c) for c in copies}, {"frame_0000"})

    def test_two_different_frames_stay_different(self):
        a = base_name("frame_0000_jpg.rf.3307c808eff261f7ea20939dba24b69a_407a21bd550ee9c9")
        b = base_name("frame_0001_jpg.rf.3307c808eff261f7ea20939dba24b69a_407a21bd550ee9c9")
        self.assertNotEqual(a, b)

    def test_the_ten_character_cropper_hash_is_stripped_too(self):
        # Two pipelines appended two hash lengths. Missing the shorter one made every
        # mainfield frame its own camera view, which silently turned the grouped split
        # back into a random one.
        self.assertEqual(
            base_name("GKa6VyIp3Kc_2026txhou_qm13_t01988_mainfield_8ceb962329"),
            "GKa6VyIp3Kc_2026txhou_qm13_t01988_mainfield",
        )

    def test_a_name_without_a_hash_is_untouched(self):
        self.assertEqual(base_name("match01_21m08s"), "match01_21m08s")


class CameraGroupTests(unittest.TestCase):
    def test_frames_from_one_match_video_share_a_group(self):
        stems = [
            "GKa6VyIp3Kc_2026txhou_qm13_t01988_mainfield",
            "GKa6VyIp3Kc_2026txhou_qm13_t02364_mainfield",
            "GKa6VyIp3Kc_2026txhou_qm13_t13262_mainfield",
        ]
        groups = {camera_group(s)[0] for s in stems}
        self.assertEqual(groups, {"GKa6VyIp3Kc_2026txhou_qm13"})

    def test_different_matches_are_different_groups(self):
        a = camera_group("GKa6VyIp3Kc_2026txhou_qm13_t01988_mainfield")[0]
        b = camera_group("cuiTG0R22Ng_2026txhou_qm16_t01988_mainfield")[0]
        self.assertNotEqual(a, b)

    def test_simulator_frames_are_flagged_synthetic(self):
        group, synthetic = camera_group("2026_sim_sim_frame_0000")
        self.assertTrue(synthetic)
        self.assertEqual(group, "sim")

    def test_real_frames_are_not_flagged_synthetic(self):
        self.assertFalse(camera_group("GKa6VyIp3Kc_2026txhou_qm13_t01988_mainfield")[1])

    def test_sequence_clips_group_by_their_prefix(self):
        cases = {
            "frame_0091": "frame_seq",
            "video_frame_0000": "video",
            "match03_33m29s": "match03",
            # A 2017-season clip and one long GoPro recording, both still in this corpus.
            "2017_real_img_0042": "2017_real_img",
            "GX010280_0413": "GX010280",
        }
        for stem, expected in cases.items():
            self.assertEqual(camera_group(stem)[0], expected, stem)


class SplitAssignmentTests(unittest.TestCase):
    def test_the_split_is_deterministic(self):
        groups = [f"view_{i}" for i in range(40)]
        self.assertEqual(assign_splits(groups, 0.15), assign_splits(groups, 0.15))

    def test_adding_a_view_does_not_reshuffle_the_others(self):
        # Hash-based rather than index-based, so tomorrow's extra event does not silently
        # move last week's frames across the split and invalidate the comparison.
        before = assign_splits([f"view_{i}" for i in range(20)], 0.15)
        after = assign_splits([f"view_{i}" for i in range(21)], 0.15)
        for group, split in before.items():
            self.assertEqual(after[group], split, group)

    def test_a_camera_view_never_straddles_train_and_val(self):
        groups = [f"view_{i}" for i in range(60)]
        assigned = assign_splits(groups, 0.2)
        self.assertEqual(set(assigned), set(groups))
        self.assertTrue(set(assigned.values()) <= {"train", "val"})

    def test_the_val_fraction_is_roughly_honoured(self):
        groups = [f"view_{i}" for i in range(500)]
        assigned = assign_splits(groups, 0.2)
        share = sum(1 for v in assigned.values() if v == "val") / len(groups)
        self.assertGreater(share, 0.15)
        self.assertLess(share, 0.25)


if __name__ == "__main__":
    unittest.main()
