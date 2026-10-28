from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.db import models
from app.services.intel.snapshots import (
    _merge_missing_fields_only,
    events_in_play,
    idle_snapshot_is_current,
    invalidate_event_snapshots,
    write_snapshot,
)
from tests.conftest import DBTestCase


class IntelSnapshotBackfillTests(unittest.TestCase):
    def test_fresh_values_win_and_the_previous_snapshot_fills_gaps(self):
        existing = {
            "team": {"team_key": "frc118", "nickname": "Robonauts"},
            "rating": {"available": True, "rating_0_100": 62.1},
            "tba": {"rank": 4},
            "teams": ["frc118"],
        }
        fresh = {
            "team": {"team_key": "frc118", "nickname": "Robonauts 2"},
            "rating": {"available": True, "rating_0_100": 68.9},
            "tba": {"rank": None},
            "teams": ["frc118", "frc254"],
        }
        merged, filled = _merge_missing_fields_only(existing, fresh)
        self.assertEqual(merged["team"]["nickname"], "Robonauts 2")
        self.assertEqual(merged["rating"]["rating_0_100"], 68.9)
        self.assertEqual(merged["teams"], ["frc118", "frc254"])
        # TBA didn't answer this time: keep the last known rank.
        self.assertEqual(merged["tba"]["rank"], 4)
        self.assertEqual(filled, 1)


NOW = 1_800_000_000


class IntelSnapshotRefreshScopeTests(DBTestCase):
    def _event(self, key: str, *match_starts: int) -> None:
        self.db.add(models.Event(event_key=key, name=key, year=2026))
        for number, start in enumerate(match_starts, start=1):
            self.db.add(models.Match(
                match_key=f"{key}_qm{number}", event_key=key, comp_level="qm",
                set_number=1, match_number=number, time=start,
            ))
        self.db.commit()

    def test_only_events_with_matches_around_now_are_in_play(self):
        self._event("2026live", NOW - 3600, NOW + 600)
        self._event("2026soon", NOW + 1800)
        self._event("2026done", NOW - 30 * 86400)
        self._event("2026late", NOW - 7 * 3600)
        self.assertEqual(
            events_in_play(self.db, ["2026live", "2026soon", "2026done", "2026late"], NOW),
            {"2026live", "2026soon"},
        )

    def test_an_idle_snapshot_is_reused_until_close_to_expiry(self):
        now = datetime.fromtimestamp(NOW, tz=timezone.utc)
        fresh = models.IntelSnapshot(snapshot_key="a", scope="event", expires_at=now + timedelta(hours=10))
        ending = models.IntelSnapshot(snapshot_key="b", scope="event", expires_at=now + timedelta(seconds=100))
        self.assertTrue(idle_snapshot_is_current(fresh, now, margin_sec=240))
        self.assertFalse(idle_snapshot_is_current(ending, now, margin_sec=240))
        self.assertFalse(idle_snapshot_is_current(None, now, margin_sec=240))

    def test_recomputing_an_event_drops_only_its_snapshots(self):
        self._event("2026arc")
        self._event("2026dal")
        self.db.add_all([
            models.IntelSnapshot(snapshot_key="e1", scope="event", event_key="2026arc"),
            models.IntelSnapshot(snapshot_key="e2", scope="event", event_key="2026dal"),
        ])
        self.db.commit()
        self.assertEqual(invalidate_event_snapshots(self.db, "2026ARC"), 1)
        self.assertEqual([row.snapshot_key for row in self.db.query(models.IntelSnapshot).all()], ["e2"])


if __name__ == "__main__":
    unittest.main()


class IntelSnapshotTeamKeyTests(DBTestCase):
    def test_lowercased_b_team_key_is_stored_with_its_real_spelling(self):
        self.db.add(models.Team(team_key="frc100B", team_number=100))
        self.db.add(models.Event(event_key="2026cacg", name="cacg", year=2026))
        self.db.commit()
        for team_key in ("frc100b", "frc99999"):
            write_snapshot(
                self.db, snapshot_key=f"intel:v1:team:{team_key}", scope="team",
                payload={"ok": True}, ttl_sec=60, event_key="2026cacg", team_key=team_key,
            )
        self.db.commit()
        self.assertEqual(self.db.get(models.IntelSnapshot, "intel:v1:team:frc100b").team_key, "frc100B")
        self.assertIsNone(self.db.get(models.IntelSnapshot, "intel:v1:team:frc99999").team_key)
