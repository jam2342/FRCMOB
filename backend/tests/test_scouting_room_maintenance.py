from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.db import models
from app.services.scouting_rooms.maintenance import _cleanup_inactive_scouting_rooms_db
from tests.conftest import DBTestCase
from tests.workspace_helpers import seed_workspace


class ScoutingRoomMaintenanceTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()

        now = datetime.now(timezone.utc)
        old_ts = now - timedelta(days=9)
        workspace, _member, _headers = seed_workspace(self.db)

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
                workspace_id=workspace.id,
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
                room_key="room-planned",
                workspace_id=workspace.id,
                event_key="2026txhou",
                title="assignments planned ahead",
                created_by="ScoutA",
                created_at=old_ts,
                updated_at=old_ts,
                last_activity_at=old_ts,
            )
        )
        for room_key in ("room-empty", "room-on-device"):
            self.db.add(
                models.ScoutingRoom(
                    room_key=room_key,
                    workspace_id=workspace.id,
                    event_key="2026txhou",
                    title=room_key,
                    created_by="ScoutA",
                    created_at=old_ts,
                    updated_at=old_ts,
                    last_activity_at=old_ts,
                )
            )
        self.db.add(
            models.ScoutingRoom(
                room_key="room-fresh",
                workspace_id=workspace.id,
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
                room_key="room-planned",
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
                room_key="room-empty",
                scout_profile="ScoutB",
                scout_profile_norm="scoutb",
                added_by_scout_profile="ScoutA",
                added_by_scout_profile_norm="scouta",
                created_at=old_ts,
                updated_at=old_ts,
            )
        )
        run = models.AnalysisRun(match_key="2026txhou_qm1", version="on_device_v1", run_kind="on_device", status="completed")
        self.db.add(run)
        self.db.flush()
        self.db.add(
            models.OnDeviceSession(
                analysis_run_id=run.id,
                match_key="2026txhou_qm1",
                event_key="2026txhou",
                room_key="room-on-device",
                principal_hash="p",
                client_session_id_hash="c",
            )
        )
        self.db.commit()

    def test_cleanup_deletes_only_abandoned_inactive_rooms_and_dependents(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        result = _cleanup_inactive_scouting_rooms_db(
            self.db,
            cutoff=cutoff,
            max_rooms_per_run=10,
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(int(result.get("deleted_rooms") or 0), 1)
        self.assertEqual(int(result.get("deleted_entries") or 0), 0)
        self.assertEqual(int(result.get("deleted_assignments") or 0), 0)
        self.assertEqual(int(result.get("deleted_leaders") or 0), 1)

        # Only the room with nothing in it (a leader row, no entries/plans/runs) goes.
        self.assertIsNone(self.db.get(models.ScoutingRoom, "room-empty"))
        self.assertIsNotNone(self.db.get(models.ScoutingRoom, "room-fresh"))
        # A room holding scouting entries is the team's record; inactivity keeps it.
        self.assertIsNotNone(self.db.get(models.ScoutingRoom, "room-old"))
        # Leaders plan shifts days before an event; those plans must survive.
        self.assertIsNotNone(self.db.get(models.ScoutingRoom, "room-planned"))
        self.assertEqual(self.db.query(models.ScoutingRoomAssignment).count(), 1)
        # On-device runs reference their room; deleting it would fail every run.
        self.assertIsNotNone(self.db.get(models.ScoutingRoom, "room-on-device"))
        self.assertEqual(self.db.query(models.ScoutingRoomEntry).count(), 1)


if __name__ == "__main__":
    unittest.main()
