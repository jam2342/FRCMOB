from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import importlib.util
import os
from pathlib import Path
import threading
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from app.db import models
from app.db.base import Base
from app.services.ratings import prediction_log as log
from tests.test_rating_calibrations import NOW, match, prediction

pytestmark = pytest.mark.skipif(os.environ.get("RUN_POSTGRES_INTEGRATION_E2E") != "1", reason="Requires disposable local PostgreSQL *_test database.")


@pytest.fixture
def postgres():
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host in {"localhost", "127.0.0.1", "::1"} and url.database.endswith("_test")
    root = create_engine(url)
    schema = "rating_" + uuid.uuid4().hex
    with root.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema} -c statement_timeout=10000"})
    factory = sessionmaker(engine, autoflush=False)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            for name in ("match_prediction_log", "rating_gate_scores", "rating_active_bundle", "rating_calibrations"):
                models.Base.metadata.tables[name].drop(conn)
            path = Path(__file__).resolve().parents[1] / "alembic/versions/20261113_0031_rating_settings_log.py"
            spec = importlib.util.spec_from_file_location("rating_pg_migration", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        with factory() as db:
            match(db)
            db.commit()
        yield factory
    finally:
        engine.dispose()
        with root.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose()


def test_concurrent_captures_and_result_lock(postgres):
    barrier = threading.Barrier(2)
    def write(index):
        with postgres() as db:
            m = db.get(models.Match, "2027test_qm1")
            barrier.wait()
            log.record(db, m, prediction(.6+.1*index), {}, now=NOW+timedelta(seconds=index))
            db.commit()
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(write, [0, 1]))
    with postgres() as db:
        row = db.get(models.MatchPredictionLog, "2027test_qm1")
        assert abs(row.final["red_win_prob"] - .7) < 1e-9
        m = db.get(models.Match, "2027test_qm1")
        m.actual_time = int((NOW+timedelta(minutes=1)).timestamp())
        log.record(db, m, prediction(.9), {}, completed=True, now=NOW+timedelta(minutes=2))
        db.commit()
        snapshot = db.execute(text("SELECT row_to_json(match_prediction_log)::text FROM match_prediction_log")).scalar()
    with postgres() as db:
        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text("UPDATE match_prediction_log SET final='{}'"))
        db.rollback()
        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text("DELETE FROM match_prediction_log"))
        db.rollback()
        assert db.execute(text("SELECT row_to_json(match_prediction_log)::text FROM match_prediction_log")).scalar() == snapshot


def test_bundle_initialization_serializes_between_workers(postgres):
    from app.services.ratings import calibrations as c

    barrier = threading.Barrier(2)
    def bump(_):
        with postgres() as db:
            barrier.wait()
            bundle = c.ensure_bundle(db, 2027)
            bundle.bundle_version += 1
            db.commit()
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(bump, [0,1]))
    with postgres() as db:
        assert c.active_bundle(db, 2027).bundle_version == 2


@pytest.mark.skipif(os.environ.get("RUN_MULTI_INSTANCE_REDIS_E2E") != "1", reason="Requires local test Redis.")
def test_distributed_lock_contention_and_ownership():
    from urllib.parse import urlsplit
    import redis
    from app.core.config import settings
    from app.services.ratings.locks import rating_lock

    assert urlsplit(settings.redis_url).hostname in {"localhost", "127.0.0.1", "::1"}
    conn = redis.from_url(settings.redis_url)
    name = "test:" + uuid.uuid4().hex
    key = f"rating:{name}"
    try:
        with rating_lock(name) as first:
            assert first
            with rating_lock(name) as second:
                assert not second
            conn.set(key, "new-owner", ex=30)
        assert conn.get(key) == b"new-owner"
    finally:
        conn.delete(key)


def test_postgres_fit_parameters_are_immutable(postgres):
    from app.services.ratings import calibrations as c

    with postgres() as db:
        row = models.RatingCalibration(season=2027, recipe=c.RECIPE, version=1, params=c.identity(),
            fitted_through=NOW, matches=1500, code_sha="test", status="candidate", created_at=NOW)
        db.add(row)
        db.commit()
        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text("UPDATE rating_calibrations SET params='{}'"))
        db.rollback()
        row = db.query(models.RatingCalibration).one()
        row.fit_metrics = {"gate": "pending"}
        db.commit()
        assert row.fit_metrics == {"gate": "pending"}
