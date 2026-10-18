# Several requests refreshing one unsaved event's schedule at once, on real PostgreSQL
# sessions (SQLite has no concurrency to test). Production hit this after a restart:
# Home's batch refreshed the same events in parallel and the slower requests died on
# matches_pkey.
from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import routes_matches
from app.db import models
from app.db.base import Base

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION_E2E") != "1",
    reason="Needs a disposable PostgreSQL *_test database (scripts/verify_postgres_multiuser.py).",
)

EVENT_KEY = "2026race"


class _FakeTBA:
    def event_matches(self, event_key):
        return [
            {
                "key": f"{event_key}_qm{n}",
                "comp_level": "qm",
                "set_number": 1,
                "match_number": n,
                "alliances": {
                    "red": {"team_keys": [f"frc{n}1", f"frc{n}2", f"frc{n}3"]},
                    "blue": {"team_keys": [f"frc{n}4", f"frc{n}5", f"frc{n}6"]},
                },
            }
            for n in range(1, 9)
        ]

    def event_teams(self, event_key):
        return []


@pytest.fixture()
def session_local():
    url = make_url(os.environ["DATABASE_URL"])
    if url.get_backend_name() != "postgresql" or not str(url.database).endswith("_test"):
        pytest.fail("Needs a disposable *_test PostgreSQL database.")
    root = create_engine(url, pool_size=10, max_overflow=4)
    schema = f"race_{uuid.uuid4().hex}"
    with root.begin() as conn:
        conn.execute(CreateSchema(schema))
    engine = root.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False)
        with factory() as db:
            db.add(models.Event(event_key=EVENT_KEY, name="Race", year=2026))
            db.commit()
        yield factory
    finally:
        with root.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        root.dispose()


def test_parallel_refreshes_of_one_event_all_succeed(session_local):
    workers = 8
    start = threading.Barrier(workers)
    errors: list[BaseException] = []

    def refresh():
        with session_local() as db:
            start.wait()
            try:
                routes_matches._refresh_event_schedule_from_tba(db, _FakeTBA(), EVENT_KEY)
            except BaseException as exc:  # collected and asserted below
                db.rollback()
                errors.append(exc)

    threads = [threading.Thread(target=refresh) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    with session_local() as db:
        assert db.query(models.Match).filter_by(event_key=EVENT_KEY).count() == 8
        assert db.query(models.MatchTeam).filter_by(event_key=EVENT_KEY).count() == 48
        assert db.query(models.Team).count() == 48
