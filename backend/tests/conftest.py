# Shared pytest fixtures for backend tests.
#
# Provides reusable database engine / session fixtures so individual test
# files no longer need to duplicate SQLAlchemy boilerplate.
#
# Usage in unittest-based tests
# -----------------------------
# Subclass ``DBTestCase`` (below) instead of ``unittest.TestCase`` to get
# ``self.engine``, ``self.SessionLocal``, and ``self.db`` set up
# automatically with an in-memory SQLite database::
#
# from tests.conftest import DBTestCase
#
# class MyTest(DBTestCase):
# def test_something(self):
# self.db.add(models.Event(...))
# ...
#
# Usage with pure pytest
# ----------------------
# Use the ``db_session`` fixture::
#
# def test_something(db_session):
# db_session.add(models.Event(...))
# ...
from __future__ import annotations

import unittest

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base

def _make_test_engine():
    # Create an in-memory SQLite engine suitable for tests.
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_team_model_cache():
    # The season run is cached per process; every test starts from its own database.
    from app.services.ratings import team_model_service

    team_model_service.clear_cache()
    from app.services.ratings import win_correction

    # Tests change data and read predictions straight after: no background rebuilds.
    previous, team_model_service.STALE_READS = team_model_service.STALE_READS, False
    # Tests check the deterministic probabilities; the learned correction has its own.
    previous_blend, win_correction.CORRECTION_BLEND = win_correction.CORRECTION_BLEND, 0.0
    yield
    team_model_service.STALE_READS = previous
    win_correction.CORRECTION_BLEND = previous_blend


@pytest.fixture(autouse=True)
def _no_workspace_creation_budget():
    # With a real Redis (CI) the hourly budget is shared by every test that creates
    # a workspace; the budget's own tests set a small one.
    from app.core.config import settings

    previous = settings.workspace_create_max_per_hour
    settings.workspace_create_max_per_hour = 1_000_000
    yield
    settings.workspace_create_max_per_hour = previous


@pytest.fixture(autouse=True)
def _no_first_live_results():
    # A local .env carries real FRC Events credentials; tests must never call it.
    # The overlay's own tests switch it back on with a fake client.
    from app.core.config import settings

    previous = settings.first_live_results_enabled
    settings.first_live_results_enabled = False
    yield
    settings.first_live_results_enabled = previous


@pytest.fixture()
def db_engine():
    # Yield an in-memory SQLite engine with all tables created.
    engine = _make_test_engine()
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()

@pytest.fixture()
def db_session_factory(db_engine):
    # Yield a sessionmaker bound to the test engine.
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

@pytest.fixture()
def db_session(db_session_factory):
    # Yield a database session that is rolled back after the test.
    session: Session = db_session_factory()
    yield session
    session.rollback()
    session.close()

# ---------------------------------------------------------------------------
# unittest base class
# ---------------------------------------------------------------------------

class DBTestCase(unittest.TestCase):
    # Base class providing in-memory SQLite DB for unittest tests.
    #
    # Subclasses get ``self.engine``, ``self.SessionLocal``, and
    # ``self.db`` (a ready-to-use session).  Schema is created in
    # ``setUp`` and torn down in ``tearDown``.

    engine = None
    SessionLocal = None
    db: Session | None = None

    def setUp(self) -> None:
        super().setUp()
        self.engine = _make_test_engine()
        self.SessionLocal = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False
        )
        Base.metadata.create_all(self.engine)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        if self.db is not None:
            self.db.rollback()
            self.db.close()
        if self.engine is not None:
            Base.metadata.drop_all(self.engine)
            self.engine.dispose()
        super().tearDown()
