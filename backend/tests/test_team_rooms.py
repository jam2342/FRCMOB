from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_scouting_rooms as rooms
from app.api import routes_workspaces
from app.core.config import settings
from app.core.security import ROOM_ACCESS_HEADER, enforce_write_request_access, parse_room_access_token
from app.db import models
from app.db.base import Base
from app.db.session import get_db
from app.services import workspaces
from app.services.scouting_rooms.maintenance import _cleanup_inactive_scouting_rooms_db
from tests.workspace_helpers import add_seeded_member, headers_for, seed_workspace

EVENT = "2026arc"
MATCH = EVENT + "_qm1"
PATH = "/scouting/rooms/team/" + EVENT


@pytest.fixture()
def team(monkeypatch):
    for key, value in {"app_env": "test", "public_readonly_mode": False,
                       "admin_api_key": "test-only", "admin_session_token_secret": "test-only-signing-secret",
                       "enforce_admin_auth_for_writes": True}.items():
        monkeypatch.setattr(settings, key, value)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sessions = sessionmaker(bind=engine, autoflush=False)
    Base.metadata.create_all(engine)
    with sessions() as db:
        workspace, lead, lead_headers = seed_workspace(db)
        scout = add_seeded_member(db, workspace, display_name="Amy")
        other_workspace, outsider, outsider_headers = seed_workspace(db, name="Other")
        scout_headers = headers_for(scout)
        ids = {"lead": lead.id, "scout": scout.id, "outsider": outsider.id, "workspace": workspace.id}
        db.add_all([models.Event(event_key=EVENT, name="Arc", year=2026),
                    models.Event(event_key="2026other", name="Other", year=2026)])
        db.add_all([models.Team(team_key="frc1", team_number=1), models.Team(team_key="frc2", team_number=2)])
        db.add_all([models.Match(match_key=key, event_key=event, comp_level="qm", set_number=1,
                                 match_number=number) for key, event, number in
                    [(MATCH, EVENT, 1), (EVENT + "_qm2", EVENT, 2), ("2026other_qm1", "2026other", 1)]])
        db.flush()
        db.add_all([models.MatchTeam(match_key=key, team_key=robot, event_key=EVENT, alliance="red")
                    for key in (MATCH, EVENT + "_qm2") for robot in ("frc1", "frc2")])
        db.commit()
    monkeypatch.setattr(rooms, "_safe_touch_http_presence", AsyncMock(return_value=[]))
    monkeypatch.setattr(rooms, "_safe_presence_snapshot", AsyncMock(return_value=[]))
    monkeypatch.setattr(rooms, "_broadcast_room_message", AsyncMock())
    monkeypatch.setattr(rooms, "_broadcast_presence_message", AsyncMock())
    monkeypatch.setattr(rooms, "disconnect_member_from_workspace_rooms", AsyncMock())
    monkeypatch.setattr(routes_workspaces, "disconnect_member_from_workspace_rooms", AsyncMock())
    app = FastAPI()

    @app.middleware("http")
    async def access(request, call_next):
        try:
            enforce_write_request_access(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    app.include_router(rooms.router)
    app.include_router(routes_workspaces.router)

    def db_override():
        with sessions() as db:
            try:
                yield db
            finally:
                db.rollback()

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        yield client, sessions, ids, lead_headers, scout_headers, outsider_headers
    engine.dispose()


def create(team, headers=None, event=EVENT):
    return team[0].post("/scouting/rooms/team", headers=headers or team[3], json={"event_key": event})


def change(member_id, *, match=MATCH, robot="frc1"):
    return {"match_key": match, "team_key": robot, "assigned_member_id": member_id}


def save(team, changes, headers=None):
    return team[0].put(PATH + "/assignments", headers=headers or team[3], json={"changes": changes})


def test_create_idempotent_roles_presence_and_snapshot(team):
    first = create(team, team[4])
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["created"] is True
    assert payload["access"]["room_role"] == "member"
    assert parse_room_access_token(payload["access"]["room_access_token"])["role"] == "member"
    again = create(team).json()
    assert again["created"] is False and again["room_key"] == payload["room_key"]
    assert again["access"]["room_role"] == "owner"
    snapshot = team[0].get(PATH, headers=team[4]).json()
    assert set(snapshot) == {"ok", "room_key", "event_key", "me", "members", "assignments"}
    assert snapshot["members"] == [
        {"member_id": team[2]["scout"], "display_name": "Amy", "role": "member"},
        {"member_id": team[2]["lead"], "display_name": "Lead", "role": "leader"},
    ]
    rooms._safe_touch_http_presence.assert_awaited()
    with team[1]() as db:
        assert db.query(models.ScoutingRoom).count() == 1


def test_unique_constraint_fallback_reloads_existing_room(team, monkeypatch):
    first = create(team).json()
    original = rooms.load_team_room
    calls = 0

    def initially_missing(db, workspace_id, event_key):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPException(status_code=404, detail="Simulate another creator winning the race.")
        return original(db, workspace_id, event_key)

    monkeypatch.setattr(rooms, "load_team_room", initially_missing)
    retried = create(team)
    assert retried.status_code == 200, retried.text
    assert retried.json()["created"] is False
    assert retried.json()["room_key"] == first["room_key"]


def test_missing_event_room_auth_and_tenant_isolation(team):
    assert create(team, event="missing").status_code == 404
    assert team[0].get(PATH, headers=team[3]).status_code == 404
    assert save(team, [change(team[2]["scout"])]).status_code == 404
    create(team)
    assert team[0].get(PATH).status_code == 401
    assert team[0].get(PATH, headers=team[5]).status_code == 404
    assert save(team, [change(team[2]["outsider"])]).status_code == 422
    assert save(team, [change(team[2]["scout"])], team[5]).status_code == 404
    assert save(team, [change(team[2]["scout"])], team[4]).status_code == 403
    other = create(team, team[5]).json()
    assert other["assignments"] == []
    assert other["room_key"] != create(team).json()["room_key"]


@pytest.mark.parametrize("bad", [
    {"match": "2026other_qm1"}, {"match": "missing"}, {"robot": "frc999"},
])
def test_validation_is_atomic(team, bad):
    create(team)
    good = change(team[2]["scout"])
    assert save(team, [good]).status_code == 200
    response = save(team, [change(None), change(team[2]["scout"], **bad)])
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)
    assert team[0].get(PATH, headers=team[3]).json()["assignments"][0]["assigned_member_id"] == team[2]["scout"]


def test_double_booking_delete_and_final_state_swap(team):
    create(team)
    scout, lead = team[2]["scout"], team[2]["lead"]
    assert save(team, [change(scout), change(lead, robot="frc2")]).status_code == 200
    assert save(team, [change(scout, robot="frc2")]).status_code == 422
    assert save(team, [change(lead), change(scout, robot="frc2")]).status_code == 200
    assert save(team, [change(scout, match=EVENT + "_qm2")]).status_code == 200
    assert save(team, [change(None), change(None, robot="frc2"),
                       change(None, match=EVENT + "_qm2")]).json()["assignments"] == []
    assert save(team, []).status_code == 422
    assert save(team, [change(None)] * 2001).status_code == 422
    rooms._broadcast_room_message.assert_awaited()


def test_rename_remove_name_reuse_and_coverage(team):
    create(team)
    scout = team[2]["scout"]
    save(team, [change(scout)])
    with team[1]() as db:
        db.add(models.ScoutingRoom(room_key="other-room", workspace_id=team[2]["workspace"], event_key=EVENT))
        db.add(models.ScoutingRoom(room_key="foreign-room", workspace_id=db.get(models.TeamWorkspaceMember, team[2]["outsider"]).workspace_id, event_key=EVENT))
        db.flush()
        db.add(models.ScoutingRoomEntry(room_key="foreign-room", match_key=MATCH, team_key="frc1", scout_profile="Amy"))
        db.commit()
    assert team[0].get(PATH, headers=team[4]).json()["assignments"][0]["covered"] is False
    with team[1]() as db:
        db.add(models.ScoutingRoomEntry(room_key="other-room", match_key=MATCH, team_key="frc1", scout_profile="Amy"))
        db.commit()
    assignment = team[0].get(PATH, headers=team[4]).json()["assignments"][0]
    assert assignment["covered"] and assignment["covered_by_me"]
    assert team[0].get(PATH, headers=team[3]).json()["assignments"][0]["covered_by_me"] is False
    renamed = team[0].patch("/workspaces/me/profile", headers=team[4], json={"display_name": "Zoe"})
    assert renamed.status_code == 200, renamed.text
    assignment = team[0].get(PATH, headers=team[4]).json()["assignments"][0]
    assert assignment["assigned_member_id"] == scout and assignment["assigned_display_name"] == "Zoe"
    assert assignment["covered_by_me"]
    casing = team[0].patch("/workspaces/me/profile", headers=team[4], json={"display_name": "ZOE"})
    assert casing.status_code == 200, casing.text
    assert team[0].get(PATH, headers=team[4]).json()["assignments"][0]["covered_by_me"]
    removed = team[0].post(f"/workspaces/me/members/{scout}/remove", headers=team[3], json={})
    assert removed.status_code == 200, removed.text
    assignment = team[0].get(PATH, headers=team[3]).json()["assignments"][0]
    assert not assignment["member_active"] and not assignment["covered"]
    assert save(team, [change(scout)]).status_code == 422
    assert create(team, team[4]).status_code == 401
    assert team[0].get(PATH, headers=team[4]).status_code == 401
    with team[1]() as db:
        # The old ZOE's report on QM2, saved before anyone else took the name.
        db.add(models.ScoutingRoomEntry(room_key="other-room", match_key=EVENT + "_qm2", team_key="frc1",
                                        scout_profile="ZOE", created_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
        db.flush()
        member = add_seeded_member(db, db.get(models.TeamWorkspace, team[2]["workspace"]), display_name="ZOE")
        new_headers = headers_for(member)
        new_member = member.id
        db.commit()
    # The new ZOE inherits the name, not the old ZOE's report.
    assert save(team, [change(new_member, match=EVENT + "_qm2")]).status_code == 200
    reused = next(row for row in team[0].get(PATH, headers=new_headers).json()["assignments"]
                  if row["match_key"] == EVENT + "_qm2")
    assert reused["assigned_member_id"] == new_member and reused["covered"] and not reused["covered_by_me"]
    team[0].patch("/workspaces/me/profile", headers=new_headers, json={"display_name": "New Scout"})
    assert team[0].get(PATH, headers=team[3]).json()["assignments"][0]["assigned_display_name"] == "ZOE"


def test_writer_rechecks_removal_and_demotion_under_lock(team, monkeypatch):
    create(team)
    original = workspaces.lock_workspace_row

    def remove(db, workspace_id):
        original(db, workspace_id)
        db.query(models.TeamWorkspaceMember).filter_by(id=team[2]["lead"]).update(
            {"removed_at": datetime.now(timezone.utc)}, synchronize_session=False)

    monkeypatch.setattr(workspaces, "lock_workspace_row", remove)
    assert save(team, [change(team[2]["scout"])]).status_code == 401
    with team[1]() as db:
        assert db.query(models.ScoutingRoomAssignment).count() == 0

    def demote(db, workspace_id):
        original(db, workspace_id)
        db.query(models.TeamWorkspaceMember).filter_by(id=team[2]["lead"]).update(
            {"role": "member"}, synchronize_session=False)

    monkeypatch.setattr(workspaces, "lock_workspace_row", demote)
    assert save(team, [change(team[2]["scout"])]).status_code == 403


def test_cleanup_preserves_empty_canonical_room(team):
    key = create(team).json()["room_key"]
    old = datetime.now(timezone.utc) - timedelta(days=100)
    with team[1]() as db:
        db.get(models.ScoutingRoom, key).last_activity_at = old
        db.add(models.ScoutingRoom(room_key="stale", workspace_id=team[2]["workspace"], last_activity_at=old))
        db.commit()
        result = _cleanup_inactive_scouting_rooms_db(db, cutoff=datetime.now(timezone.utc), max_rooms_per_run=100)
        assert result["room_keys"] == ["stale"]
        assert db.get(models.ScoutingRoom, key).archived is False


def test_existing_entries_and_keyed_rooms_work_with_team_access(team):
    payload = create(team, team[4]).json()
    access = {**team[4], ROOM_ACCESS_HEADER: payload["access"]["room_access_token"]}
    saved = team[0].post(f"/scouting/rooms/{payload['room_key']}/entries", headers=access,
                         json={"entry": {"match_key": MATCH, "team_key": "frc1"}, "client_entry_id": "one"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["entry"]["scout_profile"] == "Amy"
    keyed = team[0].post("/scouting/rooms", headers=team[3], json={
        "room_key": "keyed", "event_key": EVENT, "create_if_missing": True,
    })
    assert keyed.status_code == 200, keyed.text
    with team[1]() as db:
        assert db.get(models.ScoutingRoom, "keyed").team_event_key is None
    assert team[0].get("/scouting/rooms/keyed", headers=team[3]).status_code == 200
    wrong_event = team[0].post("/scouting/rooms", headers=team[3], json={
        "room_key": payload["room_key"], "event_key": "2026other",
    })
    assert wrong_event.status_code == 422
