import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from app.services import freshness_recovery

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - dependency-gated test import
    _IMPORT_ERROR = exc


class _DummyDB:
    def __init__(self, *, event_exists: bool = True) -> None:
        self._event_exists = event_exists

    def get(self, _model, _key):
        if not self._event_exists:
            return None
        return SimpleNamespace(event_key=_key, name="Test Event")


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional service deps unavailable: {_IMPORT_ERROR}")
class FreshnessRecoveryTests(unittest.TestCase):
    def test_recover_event_freshness_returns_not_found_for_unknown_event(self):
        db = _DummyDB(event_exists=False)
        result = freshness_recovery.recover_event_freshness(db, event_key="2026test")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "event_not_found")

    def test_recover_event_freshness_targets_missing_and_stale_teams(self):
        db = _DummyDB(event_exists=True)
        freshness_rows = [
            {"team_key": "frc10", "team_number": 10, "status": "fresh"},
            {"team_key": "frc20", "team_number": 20, "status": "stale"},
            {"team_key": "frc30", "team_number": 30, "status": "missing"},
        ]
        pipeline_response = {
            "status": "ok",
            "analysis": {
                "scheduled": [{"match_key": "2026test_qm1"}],
                "skipped": [],
                "blocked": [],
            },
        }
        with (
            patch.object(freshness_recovery, "build_event_team_freshness_rows", return_value=freshness_rows),
            patch.object(freshness_recovery, "get_queue", return_value=object()),
            patch.object(freshness_recovery, "run_event_pipeline", return_value=pipeline_response) as run_pipeline,
        ):
            result = freshness_recovery.recover_event_freshness(
                db,
                event_key="2026test",
                max_target_teams=2,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["freshness_summary"]["targeted"], 2)
        self.assertEqual(result["analysis"]["scheduled"], 1)
        allowed_team_keys = run_pipeline.call_args.kwargs["allowed_team_keys"]
        self.assertEqual(allowed_team_keys, {"frc20", "frc30"})

    def test_recover_stale_events_aggregates_totals_and_persists_summary(self):
        db = _DummyDB(event_exists=True)
        candidates = [
            {"event_key": "2026a"},
            {"event_key": "2026b"},
        ]
        event_results = [
            {
                "ok": True,
                "status": "ok",
                "analysis": {"scheduled": 2, "skipped": 1, "blocked": 0},
                "freshness_summary": {"targeted": 3},
            },
            {
                "ok": True,
                "status": "ok",
                "analysis": {"scheduled": 1, "skipped": 0, "blocked": 2},
                "freshness_summary": {"targeted": 2},
            },
        ]
        with (
            patch.object(freshness_recovery, "select_recovery_candidate_events", return_value=candidates),
            patch.object(freshness_recovery, "recover_event_freshness", side_effect=event_results),
            patch.object(freshness_recovery, "_persist_last_result") as persist_result,
        ):
            result = freshness_recovery.recover_stale_events(
                db,
                max_events=2,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["processed_event_count"], 2)
        self.assertEqual(result["totals"]["scheduled_matches"], 3)
        self.assertEqual(result["totals"]["skipped_matches"], 1)
        self.assertEqual(result["totals"]["blocked_matches"], 2)
        self.assertEqual(result["totals"]["targeted_teams"], 5)
        persist_result.assert_called_once()


if __name__ == "__main__":
    unittest.main()
