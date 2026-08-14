import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

try:
    from app.services.climb import integrity as climb_integrity

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

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _DummyDB:
    def __init__(self, rows):
        self._rows = list(rows)

    def query(self, *_args, **_kwargs):
        return _DummyQuery(self._rows)


def _finding(
    *,
    event_key: str,
    match_key: str,
    team_key: str,
    source: str,
    climb_success_prob: float | None,
    summary: dict | None = None,
    idx: int = 1,
):
    return SimpleNamespace(
        event_key=event_key,
        match_key=match_key,
        team_key=team_key,
        source=source,
        climb_success_prob=climb_success_prob,
        summary=summary or {},
        created_at=datetime.now(timezone.utc) - timedelta(minutes=idx),
        id=idx,
    )


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional deps unavailable: {_IMPORT_ERROR}")
class ClimbIntegrityAuditTests(unittest.TestCase):
    def test_detects_mismatch_between_official_and_video_sources(self):
        rows = [
            _finding(
                event_key="2026week0",
                match_key="2026week0_qm1",
                team_key="frc1",
                source="tba_score_breakdown",
                climb_success_prob=1.0,
                idx=1,
            ),
            _finding(
                event_key="2026week0",
                match_key="2026week0_qm1",
                team_key="frc1",
                source="video_v3_tracks",
                climb_success_prob=0.0,
                idx=2,
            ),
        ]
        db = _DummyDB(rows)
        with patch.object(climb_integrity, "persist_last_climb_integrity_result", return_value=None):
            result = climb_integrity.run_climb_integrity_audit(
                db,
                lookback_days=14,
                sample_limit=100,
                diff_threshold=0.45,
            )

        totals = result["totals"]
        self.assertEqual(totals["compared_pairs"], 1)
        self.assertEqual(totals["mismatched_pairs"], 1)
        self.assertEqual(result["severity"], "critical")
        self.assertEqual(len(result["top_mismatches"]), 1)
        self.assertEqual(result["top_mismatches"][0]["team_key"], "frc1")

    def test_tracks_missing_official_and_video_pairs(self):
        rows = [
            _finding(
                event_key="2026week0",
                match_key="2026week0_qm2",
                team_key="frc2",
                source="video_v3_tracks",
                climb_success_prob=0.7,
                idx=1,
            ),
            _finding(
                event_key="2026week0",
                match_key="2026week0_qm3",
                team_key="frc3",
                source="tba_score_breakdown",
                climb_success_prob=1.0,
                idx=2,
            ),
        ]
        db = _DummyDB(rows)
        with patch.object(climb_integrity, "persist_last_climb_integrity_result", return_value=None):
            result = climb_integrity.run_climb_integrity_audit(
                db,
                lookback_days=14,
                sample_limit=100,
                diff_threshold=0.45,
            )

        totals = result["totals"]
        self.assertEqual(totals["compared_pairs"], 0)
        self.assertEqual(totals["missing_official_pairs"], 1)
        self.assertEqual(totals["missing_video_pairs"], 1)
        self.assertEqual(totals["mismatched_pairs"], 0)

    def test_tracking_backend_context_classifies_as_video(self):
        finding = _finding(
            event_key="2026week0",
            match_key="2026week0_qm4",
            team_key="frc4",
            source="unknown_source",
            climb_success_prob=0.6,
            summary={"analysis_context": {"tracking_backend": "yolo_bytetrack"}},
            idx=1,
        )
        bucket = climb_integrity._finding_source_bucket(finding)
        self.assertEqual(bucket, "video_analyzed")


if __name__ == "__main__":
    unittest.main()
