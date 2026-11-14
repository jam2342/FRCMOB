from datetime import datetime, timedelta
import copy

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.core.config import settings
from app.db import models
from app.services.ratings import calibrations, rp_predictions as rp, rp_weekly as weekly
from app.services.ratings.component_spec import BonusRp, Component, ComponentSpec
from tests.test_rating_calibrations import NOW, match
from tests import test_rating_calibrations as shared

SPEC = ComponentSpec(2027,"A",1,(Component("total",("score_without_fouls",)),),
                     (BonusRp("a","bonusA"),BonusRp("b","bonusB")))
TEAMS = {"red":["frc1","frc2","frc3"],"blue":["frc4","frc5","frc6"]}


@pytest.fixture
def migrated(db_engine,db_session):
    return shared.migrated.__wrapped__(db_engine,db_session)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings,"rating_rp_enabled",True)
    monkeypatch.setattr(settings,"rating_prediction_log_enabled",True)
    monkeypatch.setattr(rp,"get_spec",lambda year:SPEC)


def seed(db, n=1, *, start=0, now=NOW):
    for teams in TEAMS.values():
        for team in teams:
            if not db.get(models.Team,team):
                db.add(models.Team(team_key=team,team_number=int(team[3:])))
    db.flush()
    for i in range(start,start+n):
        m = match(db,key=f"2027test_qm{i+1}",when=now+timedelta(hours=1))
        for color,teams in TEAMS.items():
            for team in teams:
                db.add(models.MatchTeam(match_key=m.match_key,event_key=m.event_key,alliance=color,team_key=team))
        prediction = {"bonus_rp_chances":{c:{"a":.6,"b":.2} for c in TEAMS}}
        db.add(models.MatchPredictionLog(match_key=m.match_key,event_key=m.event_key,season=2027,
            first_shown_at=now,first=prediction,final_at=now,final=prediction,backfilled=False,bundle_version=0,
            features={"schema":calibrations.SCHEMA,"scheduled_start":m.time,
                      "red_teams":TEAMS["red"],"blue_teams":TEAMS["blue"]}))
    db.flush()


def capture(db, **kwargs):
    return rp.capture(db,2027,["2027test"],event_types={"2027test":0},now=NOW,**kwargs)


def complete(db, *, n=1):
    for i in range(n):
        m = db.get(models.Match,f"2027test_qm{i+1}")
        m.actual_time = m.time
        for color,teams in TEAMS.items():
            db.add(models.MatchAllianceResult(match_key=m.match_key,alliance=color,event_key=m.event_key,season_year=2027,
                total_score=100,team_keys=teams,dq_team_keys=[],raw_breakdown={"bonusA":i%3 != 0,"bonusB":i%4 == 0},
                updated_at=NOW+timedelta(hours=2)))
    db.flush()


def test_capture_defaults_and_undeclared_season(db_session,monkeypatch):
    monkeypatch.setattr(settings,"rating_rp_enabled",False)
    assert capture(db_session) == {"status":"disabled"}
    monkeypatch.setattr(settings,"rating_rp_enabled",True)
    monkeypatch.setattr(settings,"rating_prediction_log_enabled",True)
    assert capture(db_session)["status"] == "undeclared_season"
    assert db_session.query(models.RatingCalibration).count() == 0


def test_capture_rechecks_clock_after_calculating_forecasts(db_session,enabled,monkeypatch):
    seed(db_session)
    ticks = iter((NOW,NOW+timedelta(hours=2)))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return next(ticks)
    monkeypatch.setattr(rp,"datetime",Clock)
    result = rp.capture(db_session,2027,["2027test"],event_types={"2027test":0})
    assert result["forecasts"] == 0


def test_first_forecasts_immutable_and_labelled_without_replay(migrated,enabled,monkeypatch):
    db = migrated
    seed(db)
    assert capture(db)["forecasts"] == 1
    version = db.query(models.RatingCalibration).one()
    saved = db.query(models.RatingRpPrediction).one()
    original = copy.deepcopy(saved.prediction)
    assert original["raw"]["red"]["a"]["probability"] == original["corrected"]["red"]["a"] == .5
    assert original["served"]["red"]["a"] == .6
    assert capture(db)["forecasts"] == 0
    complete(db)
    monkeypatch.setattr(rp,"replay_flags",lambda *a:pytest.fail("scoring must use immutable capture"))
    assert rp.score(db,version,through=NOW+timedelta(days=1))["labels"] == 4
    assert saved.prediction == original
    db.commit()
    for sql in ("UPDATE rating_rp_predictions SET prediction='{}'","DELETE FROM rating_rp_predictions"):
        with pytest.raises(DatabaseError,match="immutable"):
            db.execute(text(sql))
        db.rollback()


@pytest.mark.parametrize("change",["late","scored","backfilled","stale","future_source","lineup","start","unofficial","frozen"])
def test_capture_rejects_bad_evidence(db_session,enabled,change):
    db = db_session
    seed(db)
    m = db.query(models.Match).one()
    source = db.query(models.MatchPredictionLog).one()
    types = {"2027test":0}
    if change == "late":
        m.actual_time = m.time
    elif change == "scored":
        complete(db)
        m.actual_time = None
    elif change == "backfilled":
        source.backfilled = True
    elif change == "stale":
        source.final_at = NOW-timedelta(seconds=61)
    elif change == "future_source":
        source.final_at = NOW+timedelta(seconds=1)
    elif change == "lineup":
        source.features = {**source.features,"red_teams":["frc7","frc2","frc3"]}
    elif change == "start":
        m.time += 1
    elif change == "unofficial":
        types["2027test"] = 99
    elif change == "frozen":
        calibrations.ensure_bundle(db,2027).frozen = True
    db.flush()
    rp.capture(db,2027,["2027test"],event_types=types,now=NOW)
    assert db.query(models.RatingRpPrediction).count() == 0


def test_label_cutoffs_dq_strict_flags_and_lineup(db_session,enabled):
    db = db_session
    seed(db)
    capture(db)
    complete(db)
    version = db.query(models.RatingCalibration).one()
    through = NOW+timedelta(days=1)
    assert len(rp.labelled_rows(db,version,through)) == 4
    red = db.get(models.MatchAllianceResult,("2027test_qm1","red"))
    red.raw_breakdown = {"bonusA":1,"bonusB":False}
    db.flush()
    assert len(rp.labelled_rows(db,version,through)) == 3
    red.dq_team_keys = ["frc1"]
    db.flush()
    assert len(rp.labelled_rows(db,version,through)) == 2
    red.team_keys = ["frc7","frc2","frc3"]
    db.flush()
    assert not rp.labelled_rows(db,version,through)
    red.team_keys = TEAMS["red"]
    red.dq_team_keys = []
    red.updated_at = through
    db.flush()
    assert len(rp.labelled_rows(db,version,through)) == 2
    db.get(models.Match,"2027test_qm1").actual_time = int(NOW.timestamp())
    db.flush()
    assert not rp.labelled_rows(db,version,through)


def test_weekly_fit_is_auditable_idempotent_and_never_active(migrated,enabled):
    db = migrated
    seed(db,80)
    capture(db)
    complete(db,n=80)
    through, now = NOW+timedelta(days=7),NOW+timedelta(days=7,hours=9)
    result = rp.fit_week(db,2027,through,"test",now=now)
    assert result["status"] == "candidate"
    version = db.get(models.RatingCalibration,result["id"])
    assert all(v["fitted"] for v in result["counts"].values())
    before = rp.audited_training_rows(db,version.id)
    assert len(before) == 320 and version.matches == 80
    assert rp.fit_week(db,2027,through,"other",now=now) == {"status":"already_fitted","id":version.id}
    db.get(models.MatchAllianceResult,("2027test_qm1","red")).raw_breakdown = {"bonusA":False,"bonusB":True}
    db.flush()
    assert rp.audited_training_rows(db,version.id) == before
    assert rp.score(db,version,through=now)["labels"] == 0
    assert calibrations.active_bundle(db,2027).calibration_id is None
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings,"rating_autotune",True)
        with pytest.raises(ValueError,match="RP candidates cannot activate"):
            calibrations.activate(db,version.id,now=now)
    seed(db,start=80,now=now)
    assert rp.capture(db,2027,["2027test"],event_types={"2027test":0},now=now)["forecasts"] == 2
    saved = db.get(models.RatingRpPrediction,(version.id,"2027test_qm81"))
    assert saved.prediction["corrected"]["red"]["a"] != saved.prediction["raw"]["red"]["a"]["probability"]
    assert db.query(models.RatingRpPrediction).filter_by(calibration_id=version.id).count() == 1
    with pytest.raises(ValueError,match="Monday"):
        rp.fit_week(db,2027,through+timedelta(days=1),"test",now=now)
    with pytest.raises(ValueError,match="advance"):
        rp.fit_week(db,2027,NOW,"test",now=now)
    db.commit()
    for sql in ("UPDATE rating_rp_fit_evidence SET evidence='{}'","DELETE FROM rating_rp_fit_evidence"):
        with pytest.raises(DatabaseError,match="immutable"):
            db.execute(text(sql))
        db.rollback()


def test_no_fit_before_enough_captured_labels_or_at_cutoff(db_session,enabled):
    db = db_session
    seed(db,80)
    capture(db)
    complete(db,n=80)
    assert rp.fit_week(db,2027,NOW,"test",now=NOW)["status"] == "insufficient_captured_labels"
    for r in db.query(models.MatchAllianceResult):
        r.updated_at = NOW+timedelta(days=7)
    db.flush()
    assert rp.fit_week(db,2027,NOW+timedelta(days=7),"test",now=NOW+timedelta(days=7,hours=9))["status"] == "insufficient_captured_labels"


def test_each_rp_fit_uses_its_own_labels_and_obeys_movement():
    rows = [{"rp":name,"p":.9 if i%2 else .1,"base":.5,"y":i%5 != 0} for name in ("a","b") for i in range(200)]
    first,counts = weekly.fit_roles(rows,["a","b"])
    altered = [{**r,"y":not r["y"]} if r["rp"] == "b" else r for r in rows]
    second,_ = weekly.fit_roles(altered,["a","b"])
    assert first["a"] == second["a"] and first["b"] != second["b"]
    assert all(c["fitted"] for c in counts.values())
    assert first["a"]["slope"] == pytest.approx(1/1.5)
    next_,_ = weekly.fit_roles(rows,["a","b"],first)
    for name in first:
        weekly.validate_correction(next_[name],first[name])
    assert weekly.fit_roles(rows[:149],["a"])[0]["a"] == weekly.identity()
    with pytest.raises(ValueError,match="label"):
        weekly.fit_roles([{**rows[0],"y":1}],["a"])
