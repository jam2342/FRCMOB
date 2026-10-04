# The suggested-events picker syncs TBA's event list into the database on nearly
# every page load. Across the Phoenix-to-Virginia link each statement costs ~70 ms,
# so the sync must stay a couple of bulk queries, and nothing when unchanged.
from __future__ import annotations

from sqlalchemy import event

from app.api import routes_events
from app.db import models
from tests.conftest import DBTestCase


def _rows(*events):
    return [
        {"event_key": key, "name": name, "year": 2026, "city": city, "state_prov": "TX", "country": "USA"}
        for key, name, city in events
    ]


class SuggestedEventSyncTests(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        routes_events._remote_event_synced_at.clear()
        self.statements: list[str] = []
        event.listen(self.engine, "before_cursor_execute", self._record)

    def tearDown(self) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)
        routes_events._remote_event_synced_at.clear()
        super().tearDown()

    def _record(self, _conn, _cursor, statement, *_args):
        self.statements.append(statement.split()[0].upper())

    def test_writes_new_and_changed_rows_in_bulk(self):
        self.db.add(models.Event(event_key="2026aaa", name="Old name", year=2026))
        self.db.add(models.EventProfile(event_key="2026aaa", city="Austin", state_prov="TX", country="USA"))
        self.db.add(models.Event(event_key="2026bbb", name="Same", year=2026))
        self.db.add(models.EventProfile(event_key="2026bbb", city="Dallas", state_prov="TX", country="USA"))
        self.db.commit()
        self.statements.clear()

        routes_events._upsert_remote_event_snapshots(
            self.db, _rows(("2026aaa", "New name", "Austin"), ("2026bbb", "Same", "Dallas"), ("2026ccc", "Brand new", "Waco"))
        )

        # Two bulk reads, one update (2026aaa's name), two inserts (2026ccc).
        self.assertEqual(self.statements.count("SELECT"), 2)
        self.assertEqual(self.statements.count("UPDATE"), 1)
        self.assertEqual(self.statements.count("INSERT"), 2)
        self.assertEqual(self.db.get(models.Event, "2026aaa").name, "New name")
        self.assertEqual(self.db.get(models.EventProfile, "2026ccc").city, "Waco")

    def test_unchanged_data_is_not_rewritten_and_repeats_are_skipped(self):
        rows = _rows(("2026ddd", "Event D", "Houston"))
        routes_events._upsert_remote_event_snapshots(self.db, rows)
        self.statements.clear()

        routes_events._upsert_remote_event_snapshots(self.db, rows)
        self.assertEqual(self.statements, [])  # same data within 15 min: no queries

        routes_events._remote_event_synced_at.clear()
        routes_events._upsert_remote_event_snapshots(self.db, rows)
        self.assertNotIn("UPDATE", self.statements)
        self.assertNotIn("INSERT", self.statements)
