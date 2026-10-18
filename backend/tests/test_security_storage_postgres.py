"""Cross-workspace capacity checks must serialize across real PostgreSQL sessions."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import uuid
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import routes_pit_scouting, routes_push
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.services.push import subscriptions
from tests.test_picklists_pit_push_insights import PUSH_TEST_KEYS
from tests.workspace_helpers import seed_workspace
from tests.image_helpers import jpeg_bytes

pytestmark = pytest.mark.skipif(os.environ.get("RUN_POSTGRES_INTEGRATION_E2E") != "1", reason="Requires disposable local PostgreSQL *_test database.")


@pytest.fixture()
def security_api():
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host in {"localhost", "127.0.0.1", "::1"} and url.database.endswith("_test")
    root = create_engine(url, connect_args={"options": "-c statement_timeout=10000"})
    schema = "security_" + uuid.uuid4().hex
    with root.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = root.execution_options(schema_translate_map={None: schema})
    factory = sessionmaker(bind=engine, autoflush=False)
    app = FastAPI()
    app.include_router(routes_pit_scouting.router)
    app.include_router(routes_push.router)

    def database():
        with factory() as db:
            try:
                yield db
            finally:
                db.rollback()

    app.dependency_overrides[get_db] = database
    try:
        Base.metadata.create_all(engine)
        with factory() as db:
            _, _, first = seed_workspace(db, name="First team")
            _, _, second = seed_workspace(db, name="Second team")
        with TestClient(app) as client:
            yield client, [first, second]
    finally:
        with root.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        root.dispose()


@pytest.mark.parametrize("capacity", ["bytes", "files"])
def test_parallel_photo_uploads_cannot_overshoot_shared_capacity(security_api, monkeypatch, tmp_path, capacity):
    client, headers = security_api
    image = jpeg_bytes()
    monkeypatch.setattr(routes_pit_scouting, "PIT_PHOTOS_ROOT", tmp_path)
    if capacity == "bytes":
        monkeypatch.setattr(settings, "pit_photo_quota_bytes_total", len(image))
    else:
        monkeypatch.setattr(settings, "pit_photo_max_files_total", 1)
    monkeypatch.setattr(settings, "pit_photo_minimum_free_bytes", 1)
    start = threading.Barrier(2)

    def upload(index):
        start.wait()
        return client.post("/pit-scouting/photo", headers=headers[index], json={
            "event_key": f"2026invented{index}", "team_key": "frc1",
            "image_base64": base64.b64encode(image).decode(),
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(upload, range(2)))
    assert sorted(statuses) == [200, 413]
    assert sum(path.stat().st_size for path in Path(tmp_path).rglob("*") if path.is_file()) == len(image)


def test_parallel_push_registrations_cannot_overshoot_retained_cap(security_api, monkeypatch):
    client, _ = security_api
    monkeypatch.setattr(settings, "push_subscriptions_max_total", 1)
    monkeypatch.setattr(subscriptions, "get_cache", lambda: mock.Mock(redis_client=mock.Mock(eval=lambda *_: 1)))
    start = threading.Barrier(2)

    def subscribe(index):
        start.wait()
        return client.post("/push/subscribe", json={
            "endpoint": f"https://fcm.googleapis.com/fcm/send/disposable-{index}", "keys": PUSH_TEST_KEYS,
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(subscribe, range(2)))
    assert sorted(statuses) == [200, 429]
