import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from app.services.events import pipeline as event_pipeline
    from app.db import models as _models

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional dependency guarded import
    _IMPORT_ERROR = exc


class _DummyQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def one_or_none(self):
        if not self._rows:
            return None
        return self._rows[0]


class _DummyDB:
    def __init__(self, rows_by_model):
        self._rows_by_model = dict(rows_by_model)

    def query(self, model):
        return _DummyQuery(self._rows_by_model.get(model, []))

    def add(self, _value):
        return None

    def commit(self):
        return None

    def refresh(self, _value):
        return None


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional deps unavailable: {_IMPORT_ERROR}")
class AnalysisQueueCapTests(unittest.TestCase):
    def _db_for_three_matches(self):
        match_rows = [
            SimpleNamespace(match_key="m1", event_key="2026week0", comp_level="qm", set_number=1, match_number=1),
            SimpleNamespace(match_key="m2", event_key="2026week0", comp_level="qm", set_number=1, match_number=2),
            SimpleNamespace(match_key="m3", event_key="2026week0", comp_level="qm", set_number=1, match_number=3),
        ]
        match_team_rows = [
            SimpleNamespace(match_key="m1", team_key="frc1"),
            SimpleNamespace(match_key="m2", team_key="frc2"),
            SimpleNamespace(match_key="m3", team_key="frc3"),
        ]
        calibration_rows = [
            SimpleNamespace(match_key="m1", id=101),
            SimpleNamespace(match_key="m2", id=102),
            SimpleNamespace(match_key="m3", id=103),
        ]
        video_rows = [("m1",), ("m2",), ("m3",)]
        return _DummyDB(
            {
                _models.Match: match_rows,
                _models.MatchTeam: match_team_rows,
                _models.FieldCalibration: calibration_rows,
                _models.MatchVideo.match_key: video_rows,
            }
        )

    def test_respects_per_event_schedule_cap(self):
        db = self._db_for_three_matches()
        queue = SimpleNamespace(job_ids=[])

        with (
            patch.object(event_pipeline, "_resolve_event_perimeter_type", return_value=("welded", "event_profile")),
            patch.object(event_pipeline, "_latest_matching_run", return_value=None),
            patch.object(event_pipeline, "_pending_job_count", return_value=0),
            patch.object(
                event_pipeline,
                "_enqueue_analysis_job",
                side_effect=[
                    (object(), "job-m1", "queued"),
                    (object(), "job-m2", "queued"),
                    (object(), "job-m3", "queued"),
                ],
            ),
        ):
            result = event_pipeline._queue_event_matches_for_team_keys(
                db,
                queue,
                event_key="2026week0",
                allowed_team_keys=None,
                force=True,
                require_video=True,
                require_calibration=True,
                max_new_jobs=2,
                max_pending_jobs=100,
            )

        self.assertEqual(len(result["scheduled"]), 2)
        self.assertEqual(len(result["blocked"]), 1)
        self.assertIn("queue_schedule_cap_reached", result["blocked"][0]["reasons"])

    def test_applies_queue_backpressure_cap(self):
        db = self._db_for_three_matches()
        queue = SimpleNamespace(job_ids=[])

        with (
            patch.object(event_pipeline, "_resolve_event_perimeter_type", return_value=("welded", "event_profile")),
            patch.object(event_pipeline, "_latest_matching_run", return_value=None),
            patch.object(event_pipeline, "_pending_job_count", return_value=50),
            patch.object(event_pipeline, "_enqueue_analysis_job", return_value=(object(), "job-x", "queued")),
        ):
            result = event_pipeline._queue_event_matches_for_team_keys(
                db,
                queue,
                event_key="2026week0",
                allowed_team_keys=None,
                force=True,
                require_video=True,
                require_calibration=True,
                max_new_jobs=10,
                max_pending_jobs=50,
            )

        self.assertEqual(len(result["scheduled"]), 0)
        self.assertGreaterEqual(len(result["blocked"]), 1)
        self.assertIn("queue_backpressure", result["blocked"][0]["reasons"])

    def test_non_strict_calibration_uses_template_clone_fallback(self):
        match_rows = [
            SimpleNamespace(match_key="m1", event_key="2026week0", comp_level="qm", set_number=1, match_number=1),
            SimpleNamespace(match_key="m2", event_key="2026week0", comp_level="qm", set_number=1, match_number=2),
            SimpleNamespace(match_key="m3", event_key="2026week0", comp_level="qm", set_number=1, match_number=3),
        ]
        match_team_rows = [
            SimpleNamespace(match_key="m1", team_key="frc1"),
            SimpleNamespace(match_key="m2", team_key="frc2"),
            SimpleNamespace(match_key="m3", team_key="frc3"),
        ]
        calibration_rows = [SimpleNamespace(match_key="m1", id=101, event_key="2026week0")]
        video_rows = [("m1",), ("m2",), ("m3",)]
        db = _DummyDB(
            {
                _models.Match: match_rows,
                _models.MatchTeam: match_team_rows,
                _models.FieldCalibration: calibration_rows,
                _models.MatchVideo.match_key: video_rows,
            }
        )
        queue = SimpleNamespace(job_ids=[])

        cloned_by_match = {
            "m2": SimpleNamespace(match_key="m2", id=202, event_key="2026week0"),
            "m3": SimpleNamespace(match_key="m3", id=203, event_key="2026week0"),
        }

        with (
            patch.object(event_pipeline, "_resolve_event_perimeter_type", return_value=("welded", "event_profile")),
            patch.object(event_pipeline, "_latest_matching_run", return_value=None),
            patch.object(event_pipeline, "_pending_job_count", return_value=0),
            patch.object(
                event_pipeline,
                "_clone_template_calibration_to_match",
                side_effect=lambda *_args, **kwargs: cloned_by_match.get(kwargs.get("match_key")),
            ),
            patch.object(event_pipeline, "_enqueue_analysis_job", return_value=(object(), "job-x", "queued")),
        ):
            result = event_pipeline._queue_event_matches_for_team_keys(
                db,
                queue,
                event_key="2026week0",
                allowed_team_keys=None,
                force=True,
                require_video=True,
                require_calibration=False,
                max_new_jobs=10,
                max_pending_jobs=100,
            )

        self.assertEqual(len(result["scheduled"]), 3)
        self.assertEqual(len(result["blocked"]), 0)
        self.assertEqual(int(result.get("template_clone_count") or 0), 2)


if __name__ == "__main__":
    unittest.main()
