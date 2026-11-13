from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.api import routes_rating_calibrations as api
from app.core.config import settings
from app.db import models
from app.db.session import get_db
from app.services.ratings import calibrations as c, prediction_log as log
from tests.test_research_win import _table

NOW = datetime(2027, 3, 1, tzinfo=timezone.utc)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "rating_autotune", True)
    monkeypatch.setattr(settings, "rating_prediction_log_enabled", True)
    @contextmanager
    def unlocked(*args, **kwargs):
        yield True
    monkeypatch.setattr("app.services.ratings.locks.rating_lock", unlocked)


@pytest.fixture
def migrated(db_engine, db_session):
    # Exercise the actual migration's trigger, not a Python imitation.
    with db_engine.begin() as conn:
        for name in ("match_prediction_log", "rating_gate_scores", "rating_active_bundle", "rating_calibrations"):
            models.Base.metadata.tables[name].drop(conn)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/20261113_0031_rating_settings_log.py"
        spec = importlib.util.spec_from_file_location("rating_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
    return db_session


def match(db, *, key="2027test_qm1", when=None):
    if not db.get(models.Event, "2027test"):
        db.add(models.Event(event_key="2027test", name="Test", year=2027))
        db.flush()
    m = models.Match(match_key=key, event_key="2027test", comp_level="qm", match_number=1, set_number=1,
                     time=int((when or NOW + timedelta(hours=1)).timestamp()))
    db.add(m)
    db.flush()
    return m


def prediction(p):
    return {"available": True, "red_win_prob": p, "blue_win_prob": 1-p, "red_points": 120, "blue_points": 100}


def live_table():
    t = _table()
    t["red_fouls"], t["blue_fouls"] = np.zeros(len(t["y"])), np.zeros(len(t["y"]))
    t["p_live"] = t["p_bucket"]
    return t


def test_capture_first_final_lock_and_backfill(migrated):
    db = migrated
    m = match(db)
    row = log.record(db, m, prediction(.6), {}, now=NOW)
    log.record(db, m, prediction(.7), {}, now=NOW+timedelta(minutes=1))
    assert row.first["red_win_prob"] == .6 and row.final["red_win_prob"] == .7
    before = json.dumps(row.final, sort_keys=True)
    m.actual_time = int((NOW+timedelta(minutes=2)).timestamp())
    log.record(db, m, prediction(.9), {}, now=NOW+timedelta(minutes=3), completed=True)
    assert row.locked_at is not None
    log.record(db, m, prediction(.1), {}, now=NOW+timedelta(days=1))
    assert json.dumps(row.final, sort_keys=True) == before
    old = match(db, key="2027test_qm2", when=NOW-timedelta(hours=2))
    backfilled = log.backfill(db, old, prediction(.5), now=NOW)
    assert backfilled.backfilled and backfilled.locked_at


@pytest.mark.parametrize("statement", [
    "UPDATE match_prediction_log SET final='{}' WHERE match_key='2027test_qm1'",
    "DELETE FROM match_prediction_log WHERE match_key='2027test_qm1'",
])
def test_locked_history_is_database_immutable(migrated, statement):
    db = migrated
    m = match(db)
    log.record(db, m, prediction(.6), {}, now=NOW)
    log.record(db, m, prediction(.7), {}, now=NOW+timedelta(hours=2), completed=True)
    db.commit()
    with pytest.raises(DatabaseError, match="immutable"):
        db.execute(text(statement))
        db.flush()
    db.rollback()


def test_first_capture_cannot_be_changed_before_lock(migrated):
    db = migrated
    m = match(db)
    log.record(db, m, prediction(.6), {}, now=NOW)
    db.commit()
    with pytest.raises(DatabaseError, match="immutable"):
        db.execute(text("UPDATE match_prediction_log SET first='{}'"))
    db.rollback()


def test_late_capture_and_retrospective_early_start(migrated):
    m = match(migrated, when=NOW-timedelta(seconds=1))
    assert log.record(migrated, m, prediction(.6), {}, now=NOW) is None
    m.time = int((NOW+timedelta(hours=1)).timestamp())
    row = log.record(migrated, m, prediction(.6), {}, now=NOW)
    log.record(migrated, m, prediction(.7), {}, now=NOW+timedelta(minutes=10))
    m.actual_time = int((NOW+timedelta(minutes=5)).timestamp())
    assert log.stored_prediction(row, m)["red_win_prob"] == .6
    m.actual_time = int((NOW-timedelta(seconds=1)).timestamp())
    assert not log.stored_prediction(row, m)["available"]


@pytest.mark.parametrize("field,value", [("temperatures", [5]*4), ("weights", [2]*12), ("score_coef", [2]*11), ("blend", 2)])
def test_hard_bounds(field, value):
    params = c.identity()
    params[field] = value
    with pytest.raises(ValueError):
        c.validate_params(params)


def test_steps_and_nonfinite_values():
    params = c.identity()
    params["weights"][0] = .3
    with pytest.raises(ValueError, match="step"):
        c.validate_params(params, c.identity())
    params["weights"][0] = float("nan")
    with pytest.raises(ValueError):
        c.validate_params(params)
    params = c.identity()
    params["temperatures"][0] = 1.6
    with pytest.raises(ValueError, match="step"):
        c.validate_params(params, c.identity())


def test_missing_or_one_week_evidence_cannot_promote():
    assert not c.compare_evidence({}, {})["passed"]
    rows = {str(i): {"time": int(NOW.timestamp()), "event": "one"} for i in range(1500)}
    assert c.compare_evidence(rows, rows)["reason"] == "need two weeks"


def test_event_gate_guards_and_effect_size():
    candidate, baseline = {}, {}
    for i in range(1600):
        r = {"time": int((NOW+timedelta(days=i//100)).timestamp()), "event": f"event{i//100}",
             "brier": .1, "log_loss": .3, "mae": 20, "coverage": .8,
             "first": True, "playoff": True, "late": True, "score_error": 0, "score_total": 200}
        candidate[str(i)] = r
        baseline[str(i)] = {**r, "brier": .11}
    assert c.compare_evidence(candidate, baseline)["passed"]
    for r in candidate.values():
        r["coverage"] = .9
    assert not c.compare_evidence(candidate, baseline)["passed"]


def test_activation_off_missing_evidence_and_rollback_preserve_log(migrated, enabled, monkeypatch):
    db = migrated
    m = match(db)
    row = log.record(db, m, prediction(.6), {}, now=NOW)
    row.locked_at = NOW
    cal = models.RatingCalibration(season=2027, recipe=c.RECIPE, version=1, params=c.identity(), fitted_through=NOW,
                                  matches=2000, code_sha="test", status="candidate", created_at=NOW)
    db.add(cal)
    db.commit()
    before = db.execute(text("SELECT first, final, features, locked_at FROM match_prediction_log")).all()
    monkeypatch.setattr(settings, "rating_autotune", False)
    with pytest.raises(ValueError, match="off"):
        c.activate(db, cal.id, now=NOW+timedelta(days=7))
    monkeypatch.setattr(settings, "rating_autotune", True)
    assert c.activate(db, cal.id, now=NOW+timedelta(days=7))["status"] == "gate_failed"
    bundle = c.ensure_bundle(db, 2027)
    bundle.calibration_id = cal.id
    db.flush()
    assert c.rollback(db, 2027)["status"] == "rolled_back"
    after = db.execute(text("SELECT first, final, features, locked_at FROM match_prediction_log")).all()
    assert before == after


def test_admin_reads_and_writes_are_private_and_authenticated(db_session, monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(settings, "admin_api_key", "test-rating-admin")
    with TestClient(app) as client:
        assert client.get("/admin/rating/calibrations?season=2027").status_code == 403
        assert client.post("/admin/rating/freeze", json={"season": 2027}).status_code == 403
        headers = {"X-Admin-Key": "test-rating-admin"}
        assert client.post("/admin/rating/freeze", headers=headers, json={"season": 2027}).json()["frozen"]
        assert client.get("/admin/rating/calibrations?season=2027", headers=headers).json()["frozen"]


def test_fit_cutoff_and_idempotence(db_session, monkeypatch):
    t = live_table()
    calls = []
    def captured(db, season, *, through):
        calls.append(through)
        return t
    monkeypatch.setattr(c, "captured_table", captured)
    result = c.fit_week(db_session, 2027, NOW, "test")
    first = db_session.get(models.RatingCalibration, result["id"])
    params = json.dumps(first.params, sort_keys=True)
    assert c.fit_week(db_session, 2027, NOW, "test")["status"] == "already_fitted"
    assert json.dumps(first.params, sort_keys=True) == params
    assert calls == [NOW]
    with pytest.raises(ValueError, match="Monday"):
        c.fit_week(db_session, 2027, NOW+timedelta(days=1), "test")


def test_coverage_counts_both_alliances():
    t = live_table()
    t["ra"], t["ba"] = t["rm"].copy(), t["bm"].copy()
    assert all(row["coverage"] == 1.0 for row in c.loss_rows(t, c.identity()).values())


def seed_captured(db, *, n=1600):
    for i in range(n):
        time = NOW+timedelta(days=1+i//100, seconds=i%100+60)
        key = f"2027test_qm{i+1}"
        m = match(db, key=key, when=time)
        m.actual_time = int(time.timestamp())
        event = f"2027test{i//100}"
        if not db.get(models.Event, event):
            db.add(models.Event(event_key=event, name=event, year=2027))
            db.flush()
        m.event_key = event
        noise = 30 if i%10 == 0 else -30 if i%10 == 1 else 0
        feature = {"schema": c.SCHEMA, "row": {"rm": 200, "bm": 100, "rv": 100, "bv": 100, "mv": 200,
            "red_fouls": 0, "blue_fouls": 0, "min_here": 0, "min_before": 10, "u": 50,
            "best_r": 2, "worst_r": 1, "best_b": 1, "worst_b": .5, "sd_r": .5, "sd_b": .5,
            "playoff": float(i%2), "season_frac": .6, "p_live": .5}}
        capture = time-timedelta(seconds=30)
        db.add(models.MatchPredictionLog(match_key=key, event_key=event, season=2027,
            first_shown_at=capture, first=prediction(.5), final_at=capture, final=prediction(.5),
            features=feature, bundle_version=0, backfilled=False, locked_at=time+timedelta(minutes=1)))
        for alliance, score in (("red", 200+noise), ("blue", 100+noise)):
            db.add(models.MatchAllianceResult(match_key=key, event_key=event, season_year=2027,
                alliance=alliance, total_score=score, score_without_fouls=score))
    db.flush()


def test_prospective_activation_preserves_history_and_version_is_immutable(migrated, enabled):
    db = migrated
    seed_captured(db)
    cal = models.RatingCalibration(season=2027, recipe=c.RECIPE, version=1, params=c.identity(), fitted_through=NOW,
        matches=1500, code_sha="test", status="candidate", created_at=NOW)
    db.add(cal)
    db.commit()
    before = db.execute(text("SELECT first, final, features, locked_at FROM match_prediction_log ORDER BY match_key")).all()
    result = c.activate(db, cal.id, now=NOW+timedelta(days=20))
    assert result["status"] == "active", result
    assert c.activate(db, cal.id, now=NOW+timedelta(days=20))["status"] == "already_active"
    assert before == db.execute(text("SELECT first, final, features, locked_at FROM match_prediction_log ORDER BY match_key")).all()
    db.commit()
    with pytest.raises(DatabaseError, match="immutable"):
        db.execute(text("UPDATE rating_calibrations SET params='{}'"))
    db.rollback()


def test_capture_cutoffs_backfills_and_future_outcome_mutation(db_session):
    db = db_session
    seed_captured(db, n=200)
    through = NOW+timedelta(days=2)
    t = c.captured_table(db, 2027, through=through)
    assert len(t["y"]) == 100
    params_before = c.fit_score_calibration(c.side_arrays(t), terms=True).coef
    db.query(models.MatchAllianceResult).filter(models.MatchAllianceResult.match_key.in_(
        [f"2027test_qm{i+1}" for i in range(100,200)])).update({"total_score": 999, "score_without_fouls": 999}, synchronize_session=False)
    db.flush()
    after = c.captured_table(db, 2027, through=through)
    np.testing.assert_array_equal(params_before, c.fit_score_calibration(c.side_arrays(after), terms=True).coef)
    # A deliberately leaky full-history fit must notice the mutation.
    later = c.captured_table(db, 2027, through=NOW+timedelta(days=3))
    assert not np.allclose(params_before, c.fit_score_calibration(c.side_arrays(later), terms=True).coef)
    row = db.get(models.MatchPredictionLog, "2027test_qm1")
    row.backfilled = True
    db.flush()
    assert len(c.captured_table(db, 2027, through=through)["y"]) == 99
    cal = models.RatingCalibration(season=2027, recipe=c.RECIPE, version=1, params=c.identity(), fitted_through=NOW,
        matches=1500, code_sha="test", status="candidate", created_at=NOW+timedelta(days=4))
    db.add(cal)
    db.flush()
    assert c.evaluate_version(db, cal, NOW+timedelta(days=3))["status"] == "no_evidence"


def test_zero_residual_variance_is_finite():
    t = live_table()
    t["ra"], t["ba"] = t["rm"].copy(), t["bm"].copy()
    coef = c.fit_score_calibration(c.side_arrays(t), terms=True).coef
    assert np.isfinite(coef).all()
    np.testing.assert_allclose(coef, 0)


def test_schedule_capture_promotion_history_and_matchup(db_session, enabled, monkeypatch):
    from app.api.routes_events import get_event_schedule_with_synergy
    from app.services.events.win_probability import custom_matchup
    from tests.test_win_probability import add_result, RED, BLUE

    db = db_session
    event = "2026test"
    db.add(models.Event(event_key=event, name="Test", year=2026))
    for number in range(1,7):
        db.add(models.Team(team_key=f"frc{number}", team_number=number))
        db.add(models.EventTeam(event_key=event, team_key=f"frc{number}"))
    for number in (1,2):
        key = f"{event}_qm{number}"
        db.add(models.Match(match_key=key, event_key=event, comp_level="qm", match_number=number, set_number=1,
                            time=int((NOW+timedelta(hours=number)).timestamp())))
        for alliance, keys in (("red",RED),("blue",BLUE)):
            for team in keys:
                db.add(models.MatchTeam(match_key=key, event_key=event, team_key=team, alliance=alliance))
    db.commit()
    original = log.schedule
    clock = [NOW]
    def capture(*args, **kwargs):
        return original(*args, **kwargs, now=clock[0])
    monkeypatch.setattr(log, "schedule", capture)
    first = get_event_schedule_with_synergy(event, db=db)["matches"][0]["prediction"]
    stored = db.get(models.MatchPredictionLog, event+"_qm1")
    assert stored.first["red_win_prob"] == first["red_win_prob"]
    db.get(models.Match, event+"_qm1").actual_time = int((NOW+timedelta(hours=1)).timestamp())
    db.commit()
    add_result(db, event+"_qm1", event, 300, 100)
    clock[0] = NOW+timedelta(hours=1,minutes=1)
    params = c.identity()
    params["weights"][0] = .2
    cal = models.RatingCalibration(season=2026, recipe=c.RECIPE, version=1, params=params, fitted_through=NOW,
                                  matches=1500, code_sha="test", status="active", created_at=NOW)
    db.add(cal)
    db.flush()
    bundle = c.ensure_bundle(db, 2026)
    bundle.calibration_id, bundle.bundle_version = cal.id, 1
    db.commit()
    rows = get_event_schedule_with_synergy(event, db=db)["matches"]
    assert rows[0]["prediction"] == {**first, "backfilled": False}
    assert rows[1]["prediction"]["source_label"] == "versioned_live_forecast_v1"
    snapshot = json.dumps(db.get(models.MatchPredictionLog, event+"_qm1").final, sort_keys=True)
    matchup = custom_matchup(db, event, RED, BLUE)
    assert matchup["red_win_prob"] == rows[1]["prediction"]["red_win_prob"]
    c.rollback(db, 2026)
    db.commit()
    assert get_event_schedule_with_synergy(event, db=db)["matches"][0]["prediction"] == {**first, "backfilled": False}
    assert json.dumps(db.get(models.MatchPredictionLog, event+"_qm1").final, sort_keys=True) == snapshot
    db.get(models.Match, event+"_qm2").actual_time = int((NOW+timedelta(hours=2)).timestamp())
    db.commit()
    add_result(db, event+"_qm2", event, 250, 150)
    clock[0] = NOW+timedelta(hours=2,minutes=1)
    get_event_schedule_with_synergy(event, db=db)
    def no_replay(*args, **kwargs):
        raise AssertionError("locked history was replayed")
    monkeypatch.setattr("app.api.routes_events.match_predictions", no_replay)
    historical = get_event_schedule_with_synergy(event, db=db)
    assert historical["points_prediction_count"] == 2
    assert historical["matches"][0]["prediction"] == {**first, "backfilled": False}


def test_weekly_job_can_fit_but_cannot_self_validate(db_session, enabled):
    db = db_session
    seed_captured(db, n=200)
    result = c.weekly_job(db, 2027, now=NOW+timedelta(days=7,hours=9), code_sha="test")
    assert result["status"] in ("candidate", "rejected")
    bundle = c.active_bundle(db, 2027)
    assert bundle.calibration_id is None
    stored = db.get(models.RatingCalibration, result["id"])
    assert c.evidence(db, stored.id) == {}
    again = c.weekly_job(db, 2027, now=NOW+timedelta(days=7,hours=10), code_sha="test")
    assert again["status"] == "already_fitted"
    bundle.frozen = True
    db.commit()
    assert c.weekly_job(db, 2027, now=NOW+timedelta(days=14,hours=9), code_sha="test")["status"] == "frozen"


def test_cache_fingerprint_tracks_active_bundle_and_rebuild_contention(db_session, enabled, monkeypatch):
    from app.services.ratings import team_model_service as service
    from app.services.ratings import locks
    from fastapi import HTTPException

    db = db_session
    before = service._fingerprint(db, 2027)
    bundle = c.ensure_bundle(db, 2027)
    bundle.bundle_version = 1
    db.flush()
    assert before != service._fingerprint(db, 2027)
    @contextmanager
    def busy(*args, **kwargs):
        yield False
    monkeypatch.setattr(locks, "rating_lock", busy)
    sentinel = object()
    service._cache[2027] = (before, sentinel)
    assert service._compute(db, 2027, before) is sentinel
    service._cache.clear()
    with pytest.raises(HTTPException) as error:
        service._compute(db, 2027, before)
    assert error.value.status_code == 503


def test_safety_baseline_rolls_back_bad_active_fit(migrated, enabled):
    db = migrated
    seed_captured(db)
    params = c.identity()
    params["weights"][10] = -1
    cal = models.RatingCalibration(season=2027, recipe=c.RECIPE, version=1, params=params, fitted_through=NOW,
        matches=1500, code_sha="test", status="active", created_at=NOW, activated_at=NOW)
    db.add(cal)
    db.flush()
    bundle = c.ensure_bundle(db, 2027)
    bundle.calibration_id, bundle.bundle_version = cal.id, 1
    db.commit()
    snapshot = db.execute(text("SELECT first, final, features, locked_at FROM match_prediction_log ORDER BY match_key")).all()
    result = c.weekly_job(db, 2027, now=NOW+timedelta(days=18), code_sha="test")
    assert result["status"] == "rolled_back"
    assert bundle.calibration_id is None and bundle.bundle_version == 2
    assert db.query(models.RatingCalibration).count() == 1
    assert snapshot == db.execute(text("SELECT first, final, features, locked_at FROM match_prediction_log ORDER BY match_key")).all()
