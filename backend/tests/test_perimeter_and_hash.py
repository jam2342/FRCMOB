import unittest

from app.services.analysis.hash import build_analysis_params_hash
from app.services.vision.perimeter_resolver import (
    normalize_perimeter_type,
    resolve_perimeter_type_for_event_profile,
)


class DummyEventProfile:
    def __init__(self, state_prov: str | None, country: str | None):
        self.state_prov = state_prov
        self.country = country


class PerimeterResolverTests(unittest.TestCase):
    def test_texas_resolves_to_andymark(self):
        profile = DummyEventProfile(state_prov="TX", country="USA")
        perimeter_type, source = resolve_perimeter_type_for_event_profile(profile)
        self.assertEqual(perimeter_type, "andymark")
        self.assertEqual(source, "state:tx")

    def test_canada_resolves_to_welded(self):
        profile = DummyEventProfile(state_prov="ON", country="Canada")
        perimeter_type, source = resolve_perimeter_type_for_event_profile(profile)
        self.assertEqual(perimeter_type, "welded")
        self.assertEqual(source, "country:canada")

    def test_unknown_profile_defaults_to_welded(self):
        perimeter_type, source = resolve_perimeter_type_for_event_profile(None)
        self.assertEqual(perimeter_type, "welded")
        self.assertEqual(source, "default:no_event_profile")

    def test_normalize_perimeter_type_fallback(self):
        self.assertEqual(normalize_perimeter_type("welded"), "welded")
        self.assertEqual(normalize_perimeter_type("andymark"), "andymark")
        self.assertEqual(normalize_perimeter_type(""), "welded")
        self.assertEqual(normalize_perimeter_type("unknown"), "welded")


class AnalysisHashTests(unittest.TestCase):
    def test_hash_changes_with_perimeter_type(self):
        welded_hash = build_analysis_params_hash(
            analysis_version="video_v3_tracks",
            calibration_id=42,
            sampling={"sample_interval_sec": 2.0},
            tracking={"mode": "motion_v1"},
            perimeter_type="welded",
        )
        andymark_hash = build_analysis_params_hash(
            analysis_version="video_v3_tracks",
            calibration_id=42,
            sampling={"sample_interval_sec": 2.0},
            tracking={"mode": "motion_v1"},
            perimeter_type="andymark",
        )
        self.assertNotEqual(welded_hash, andymark_hash)

    def test_invalid_perimeter_normalizes_to_welded_hash(self):
        welded_hash = build_analysis_params_hash(
            analysis_version="video_v3_tracks",
            calibration_id=42,
            sampling={"sample_interval_sec": 2.0},
            tracking={"mode": "motion_v1"},
            perimeter_type="welded",
        )
        invalid_hash = build_analysis_params_hash(
            analysis_version="video_v3_tracks",
            calibration_id=42,
            sampling={"sample_interval_sec": 2.0},
            tracking={"mode": "motion_v1"},
            perimeter_type="bad-value",
        )
        self.assertEqual(welded_hash, invalid_hash)


if __name__ == "__main__":
    unittest.main()
