"""Concurrent requests through real HTTP, auth middleware, and PostgreSQL sessions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import socket
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema
import uvicorn

from app.api import routes_scouting_rooms, routes_tracks
from app.api.routes_picklists import router as picklists_router
from app.api.routes_pit_scouting import router as pit_router
from app.api.routes_tracks import router as tracks_router
from app.api.routes_workspaces import router as workspaces_router
from app.core.config import settings
from app.core.security import enforce_write_request_access
from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.services import workspaces
from tests.test_routes_tracks_on_device_sync import _seed_match, _session_body, MATCH_KEY, EVENT_KEY

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION_E2E") != "1",
    reason="Run scripts/verify_postgres_multiuser.py for disposable PostgreSQL coverage.",
)


@pytest.fixture()
def beta(monkeypatch):
    url = make_url(os.environ["DATABASE_URL"])
    host = str(url.query.get("host") or url.host or "")
    local_socket = host.startswith(("/tmp/frcmob-pg-", "/private/tmp/frcmob-pg-")) and Path(host).is_dir()
    if url.get_backend_name() != "postgresql" or not (
        host in {"127.0.0.1", "localhost", "::1"} or local_socket
    ) or not str(url.database).endswith("_test"):
        pytest.fail("Concurrency tests require a local disposable *_test database.")
    root_engine = create_engine(url, pool_size=8, max_overflow=4, connect_args={"options": "-c statement_timeout=10000"})
    schema = f"beta_{uuid.uuid4().hex}"
    with root_engine.begin() as conn:
        conn.execute(CreateSchema(schema))
    engine = root_engine.execution_options(schema_translate_map={None: schema})
    session_local = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(settings, "public_readonly_mode", False)
    monkeypatch.setattr(settings, "enforce_admin_auth_for_writes", True)
    monkeypatch.setattr(settings, "admin_api_key", "disposable-local-beta-only")
    monkeypatch.setattr(settings, "admin_session_token_secret", "disposable-local-beta-signing-secret-only")
    monkeypatch.setattr(routes_scouting_rooms.scouting_room_hub, "_redis_presence_enabled", False)
    monkeypatch.setattr(routes_scouting_rooms, "SessionLocal", session_local)
    app = FastAPI()

    @app.middleware("http")
    async def access(request, call_next):
        try:
            enforce_write_request_access(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    for router in (workspaces_router, tracks_router, picklists_router, pit_router):
        app.include_router(router)

    def override_db():
        with session_local() as db:
            try:
                yield db
            finally:
                db.rollback()

    app.dependency_overrides[get_db] = override_db
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        Base.metadata.create_all(engine)
        with session_local() as db:
            _seed_match(db)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Disposable HTTP server did not start"
        with httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=15) as client:
            yield client, session_local, engine
    finally:
        server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=10)
        sock.close()
        with root_engine.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
        root_engine.dispose()


def create_workspace(client, name="Beta team"):
    response = client.post("/workspaces", json={"name": name, "display_name": "Leader"})
    assert response.status_code == 200, response.text
    return response.json()


def headers(member):
    return {"X-Workspace-Access": member["access"]["token"]}


def join(client, workspace, name):
    response = client.post("/workspaces/join", json={"join_code": workspace["join_code"], "display_name": name})
    assert response.status_code == 200, response.text
    return response.json()


def concurrently(*requests):
    start = threading.Barrier(len(requests))

    def run(request):
        start.wait(timeout=5)
        return request()

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        return list(executor.map(run, requests))


@contextmanager
def overlap_unlocked_reads(engine, table):
    """Force the old unlocked reads to overlap; row-locked transactions serialize.

    This schedules actual database queries, rather than mocking their results.
    Once a transaction has acquired FOR UPDATE, no barrier is inserted behind it.
    """
    barrier = threading.Barrier(2)
    seen = set()
    guard = threading.Lock()

    def after(conn, cursor, statement, parameters, context, executemany):
        sql = statement.upper()
        if "FOR UPDATE" in sql:
            conn.info["beta_row_locked"] = True
            with guard:
                seen.add(threading.get_ident())
        if not sql.startswith("SELECT") or f".{table.upper()} " not in sql or "COUNT(" in sql:
            return
        if conn.info.get("beta_row_locked"):
            return
        key = threading.get_ident()
        with guard:
            if key in seen:
                return
            seen.add(key)
        barrier.wait(timeout=5)

    def reset(conn):
        conn.info.pop("beta_row_locked", None)

    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "commit", reset)
    event.listen(engine, "rollback", reset)
    try:
        yield
    finally:
        event.remove(engine, "after_cursor_execute", after)
        event.remove(engine, "commit", reset)
        event.remove(engine, "rollback", reset)


def test_simultaneous_picklist_saves_return_one_conflict(beta):
    client, sessions, engine = beta
    lead = create_workspace(client)
    scouts = [join(client, lead, name) for name in ("Scout A", "Scout B")]
    response = client.post("/picklists", headers=headers(lead), json={"event_key": EVENT_KEY})
    assert response.status_code == 200, response.text
    picklist_id = response.json()["picklist"]["id"]
    with overlap_unlocked_reads(engine, "event_picklists"):
        results = concurrently(*[
            lambda scout=scout, title=title: client.put(
                f"/picklists/{picklist_id}", headers=headers(scout), json={"version": 1, "title": title}
            ) for scout, title in zip(scouts, ("A's draft", "B's draft"))
        ])
    assert [r.status_code for r in results] == [200, 200]
    payloads = [r.json() for r in results]
    assert sorted(p["ok"] for p in payloads) == [False, True], payloads
    winner = next(p["picklist"] for p in payloads if p["ok"])
    conflict = next(p for p in payloads if not p["ok"])
    assert conflict["conflict"] is True
    assert conflict["picklist"]["title"] == winner["title"]
    with sessions() as db:
        row = db.get(models.EventPicklist, picklist_id)
        assert row.version == 2 and row.title == winner["title"]
    retry = client.put(f"/picklists/{picklist_id}", headers=headers(scouts[0]), json={"version": 2, "title": "Reconciled"})
    assert retry.json()["picklist"]["version"] == 3


def test_concurrent_same_name_joins_create_one_member(beta):
    client, sessions, engine = beta
    lead = create_workspace(client)
    with overlap_unlocked_reads(engine, "team_workspace_members"):
        results = concurrently(*[
            lambda: client.post("/workspaces/join", json={"join_code": lead["join_code"], "display_name": "Same Scout"})
            for _ in range(2)
        ])
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    with sessions() as db:
        members = db.scalars(select(models.TeamWorkspaceMember).where(
            models.TeamWorkspaceMember.workspace_id == lead["workspace"]["id"]
        )).all()
        assert len(members) == 2


def test_simultaneous_first_pit_saves_do_not_error(beta):
    client, sessions, engine = beta
    lead = create_workspace(client)
    scouts = [join(client, lead, name) for name in ("Scout A", "Scout B")]
    with overlap_unlocked_reads(engine, "pit_scouting_entries"):
        results = concurrently(*[
            lambda scout=scout: client.post("/pit-scouting", headers=headers(scout), json={
                "event_key": EVENT_KEY, "team_key": "frc254", "payload": {"notes": scout["me"]["display_name"]}
            }) for scout in scouts
        ])
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert len({r.json()["entry"]["id"] for r in results}) == 1
    with sessions() as db:
        assert db.query(models.PitScoutingEntry).count() == 1


def test_recording_retries_review_and_workspace_isolation(beta):
    client, sessions, _engine = beta
    lead = create_workspace(client)
    scout_a, scout_b = [join(client, lead, name) for name in ("Scout A", "Scout B")]
    outsider = create_workspace(client, "Other team")
    body = _session_body("retry-after-lost-response")
    body["workspaceId"] = lead["workspace"]["id"]
    # Treat the first successful response as lost; the server has committed it.
    first = client.post("/tracks/on-device-session", headers=headers(scout_a), json=body)
    assert first.status_code == 200, first.text
    results = concurrently(*[
        lambda: client.post("/tracks/on-device-session", headers=headers(scout_a), json=body)
        for _ in range(3)
    ])
    assert all(r.status_code == 200 and r.json()["reused_run"] for r in results)
    assert {r.json()["run_id"] for r in results} == {first.json()["run_id"]}
    distinct = client.post("/tracks/on-device-session", headers=headers(scout_b), json=body)
    assert distinct.status_code == 200 and distinct.json()["run_id"] != first.json()["run_id"]
    mismatch = client.post("/tracks/on-device-session", headers=headers(outsider), json=body)
    assert mismatch.status_code == 409
    session_id = first.json()["on_device_session_id"]
    reviewed = client.post(f"/tracks/on-device-session/{session_id}/review", headers={
        "X-Admin-Key": "disposable-local-beta-only"
    }, json={"status": "accepted", "force": True})
    assert reviewed.status_code == 200, reviewed.text
    replay = client.post("/tracks/on-device-session", headers=headers(scout_a), json=body)
    assert replay.status_code == 200 and replay.json()["status"] == "accepted"
    changed = copy.deepcopy(body)
    changed["payload"]["points_by_team"]["frc118"][0]["fieldX"] = 3.5
    assert client.post("/tracks/on-device-session", headers=headers(scout_a), json=changed).status_code == 409
    with sessions() as db:
        assert db.query(models.OnDeviceSession).filter_by(match_key=MATCH_KEY).count() == 2
        assert db.query(models.RobotTrack).filter_by(match_key=MATCH_KEY).count() == 6


def test_removed_member_cannot_read_or_upload(beta):
    client, sessions, _engine = beta
    lead = create_workspace(client)
    scout = join(client, lead, "Scout")
    response = client.post(f"/workspaces/me/members/{scout['me']['id']}/remove", headers=headers(lead), json={})
    assert response.status_code == 200, response.text
    assert client.get("/workspaces/me", headers=headers(scout)).status_code == 401
    assert client.get(f"/picklists?event_key={EVENT_KEY}", headers=headers(scout)).status_code == 401
    body = _session_body("revoked")
    body["workspaceId"] = lead["workspace"]["id"]
    assert client.post("/tracks/on-device-session", headers=headers(scout), json=body).status_code == 401
    with sessions() as db:
        assert db.query(models.OnDeviceSession).count() == 0


def test_private_notes_and_picklists_stay_in_workspace(beta):
    client, _sessions, _engine = beta
    first = create_workspace(client)
    other = create_workspace(client, "Other team")
    response = client.post("/picklists", headers=headers(first), json={"event_key": EVENT_KEY, "title": "Private"})
    assert response.status_code == 200
    picklist_id = response.json()["picklist"]["id"]
    assert client.get(f"/picklists/{picklist_id}", headers=headers(other)).status_code == 404
    assert client.put(f"/picklists/{picklist_id}", headers=headers(other), json={"version": 1, "title": "Overwrite"}).status_code == 404
    assert client.post("/pit-scouting", headers=headers(first), json={
        "event_key": EVENT_KEY, "team_key": "frc254", "payload": {"notes": "Private notes"}
    }).status_code == 200
    assert client.get(f"/pit-scouting/{EVENT_KEY}/frc254", headers=headers(other)).json()["entry"] is None


@pytest.mark.parametrize("write_kind", ["pit", "picklist", "recording"])
def test_write_authenticated_before_removal_is_rechecked_before_save(beta, monkeypatch, write_kind):
    client, sessions, _engine = beta
    lead = create_workspace(client)
    scout = join(client, lead, "Scout")
    response = client.post("/picklists", headers=headers(lead), json={"event_key": EVENT_KEY, "title": "Original"})
    assert response.status_code == 200
    picklist_id = response.json()["picklist"]["id"]
    authenticated = threading.Event()
    resume = threading.Event()
    resolve = workspaces.resolve_workspace_actor

    def pause_after_auth(connection, db):
        actor = resolve(connection, db)
        if connection.headers.get("X-Workspace-Access") == scout["access"]["token"]:
            authenticated.set()
            assert resume.wait(timeout=5)
        return actor

    # Scheduling hook only: auth, row reads, removal and persistence stay real.
    monkeypatch.setattr(workspaces, "resolve_workspace_actor", pause_after_auth)
    monkeypatch.setattr(routes_tracks, "resolve_workspace_actor", pause_after_auth)

    def write():
        if write_kind == "pit":
            return client.post("/pit-scouting", headers=headers(scout), json={
                "event_key": EVENT_KEY, "team_key": "frc254", "payload": {"notes": "Revoked"}
            })
        if write_kind == "picklist":
            return client.put(f"/picklists/{picklist_id}", headers=headers(scout), json={"version": 1, "title": "Revoked"})
        body = _session_body("revoked-in-flight")
        body["workspaceId"] = lead["workspace"]["id"]
        return client.post("/tracks/on-device-session", headers=headers(scout), json=body)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(write)
        try:
            assert authenticated.wait(timeout=5)
            removed = client.post(f"/workspaces/me/members/{scout['me']['id']}/remove", headers=headers(lead), json={})
            assert removed.status_code == 200, removed.text
        finally:
            resume.set()
        result = pending.result(timeout=10)
    assert result.status_code == 401, result.text
    with sessions() as db:
        assert db.query(models.PitScoutingEntry).count() == 0
        assert db.query(models.OnDeviceSession).count() == 0
        assert db.get(models.EventPicklist, picklist_id).title == "Original"
