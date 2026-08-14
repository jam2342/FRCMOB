from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.db import models
from app.services.scouting_rooms.maintenance import _cleanup_inactive_scouting_rooms_db
from tests.conftest import DBTestCase


class ScoutingRoomMaintenanceTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()

        now = datetime.now(timezone.utc)
        old_ts = now - timedelta(days=9)

        self.db.add(models.Event(event_key="2026txhou", name="Houston", year=2026))
        self.db.add(models.Team(team_key="frc118", team_number=118, nickname="Robonauts"))
        self.db.add(
            models.Match(
                match_key="2026txhou_qm1",
                event_key="2026txhou",
                comp_level="qm",
                set_number=1,
                match_number=1,
                time=1700000000,
            )
        )
        self.db.add(
            models.ScoutingRoom(
                room_key="room-old",
                event_key="2026txhou",
                title="old room",
                created_by="ScoutA",
                created_at=old_ts,
                updated_at=old_ts,
                last_activity_at=old_ts,
            )
        )
        self.db.add(
            models.ScoutingRoom(
                room_key="room-fresh",
                event_key="2026txhou",
                title="fresh room",
                created_by="ScoutA",
                created_at=now,
                updated_at=now,
                last_activity_at=now,
            )
        )
        self.db.commit()

        self.db.add(
            models.ScoutingRoomEntry(
                room_key="room-old",
                event_key="2026txhou",
                match_key="2026txhou_qm1",
                team_key="frc118",
                scout_profile="ScoutA",
                client_entry_id="entry-1",
                payload={"id": "entry-1"},
                created_at=old_ts,
                updated_at=old_ts,
            )
        )
        self.db.add(
            models.ScoutingRoomAssignment(
                room_key="room-old",
                event_key="2026txhou",
                match_key="2026txhou_qm1",
                team_key="frc118",
                assigned_scout_profile="ScoutB",
                assigned_scout_profile_norm="scoutb",
                assigned_by_scout_profile="ScoutA",
                assigned_by_scout_profile_norm="scouta",
                created_at=old_ts,
                updated_at=old_ts,
            )
        )
        self.db.add(
            models.ScoutingRoomLeader(
                room_key="room-old",
                scout_profile="ScoutB",
                scout_profile_norm="scoutb",
                added_by_scout_profile="ScoutA",
                added_by_scout_profile_norm="scouta",
                created_at=old_ts,
                updated_at=old_ts,
            )
        )
        self.db.commit()

    def test_cleanup_deletes_only_inactive_rooms_and_dependents(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        result = _cleanup_inactive_scouting_rooms_db(
            self.db,
            cutoff=cutoff,
            max_rooms_per_run=10,
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(int(result.get("deleted_rooms") or 0), 1)
        self.assertEqual(int(result.get("deleted_entries") or 0), 1)
        self.assertEqual(int(result.get("deleted_assignments") or 0), 1)
        self.assertEqual(int(result.get("deleted_leaders") or 0), 1)

        self.assertIsNone(self.db.get(models.ScoutingRoom, "room-old"))
        self.assertIsNotNone(self.db.get(models.ScoutingRoom, "room-fresh"))


if __name__ == "__main__":
    unittest.main()
