import copy
from dataclasses import asdict
from datetime import timedelta
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import numpy as np
import pytest
from sqlalchemy import event as sql_event

from app.api import routes_rating_calibrations as api
from app.api.schemas import RatingStatePreview
from app.core.config import settings
from app.db import models
from app.db.session import get_db
from app.services.ratings import calibrations, component_spec as specs, state_fitting, state_preview
from app.services.ratings.replay import MatchCard
from app.services.ratings.research_filter import ScaleFreeFilter
from app.services.ratings.state_inputs import digest, inputs_for
from app.services.ratings.strength import curve_for_anchors, points_to_rating
from app.services.ratings.total_adapter import TotalStateAdapter, replay_adapter
from tests.test_rating_state_fitting import CUTOFF, PARAMS, artifact, seed


def declared(year=2027):
    return specs.ComponentSpec(year,"A",1,(specs.Component("total",("score_without_fouls",)),))


def test_registry_fails_closed_and_sources_preserve_unknowns():
    for year in range(2022,2027):
        assert specs.get_spec(year).components == declared(year).components
    with pytest.raises(ValueError,match="no declared"):
        specs.get_spec(2027)
    assert specs.project(declared(),150,{"foulPoints":10}) == {"total":140}
    for score, breakdown in ((150,{}),(-1,{"foulPoints":0}),(10,{"foulPoints":11}),(True,{"foulPoints":0}),(150,{"foulPoints":float("nan")})):
        assert specs.project(declared(),score,breakdown) == {"total":None}
    split = specs.ComponentSpec(2027,"B",1,(specs.Component("auto",("autoPoints",)),specs.Component("teleop",("teleopPoints",))))
    assert specs.project(split,100,{"foulPoints":5,"autoPoints":20,"teleopPoints":60}) == {"total":95,"auto":20,"teleop":60,"other":15}
    assert specs.project(split,100,{"foulPoints":5,"autoPoints":20})["other"] is None
    assert specs.project(split,10,{"foulPoints":0,"autoPoints":20,"teleopPoints":0})["other"] == -10
    robot = specs.ComponentSpec(2027,"C",1,(specs.Component("climb",("r1","r2","r3"),"robot"),))
    assert specs.project(robot,50,{"foulPoints":0,"r1":5,"r2":10,"r3":0}) == {"total":50,"climb":[5,10,0],"other":35}
    assert specs.project(robot,50,{"foulPoints":0,"r1":True,"r2":10,"r3":0})["climb"] == [None,10,0]
    with pytest.raises(ValueError,match="exactly"):
        specs.ComponentSpec(2027,"A",1,split.components)
    with pytest.raises(ValueError,match="three"):
        specs.ComponentSpec(2027,"C",1,(specs.Component("climb",("r1",),"robot"),))


def setup_preview(db, monkeypatch, *, n=20, year=2027):
    seed(db,year-1,n=20)
    seed(db,year,n=n)
    data = artifact()
    if year != 2027:
        data["season"] = year
        data["source_seasons"] = [year-2,year-1]
    bootstrap = state_fitting.install_bootstrap(db,data,"test")
    now = CUTOFF.replace(year=year)
    bootstrap.created_at = now-timedelta(days=30)
    db.commit()
    monkeypatch.setattr(state_preview,"get_spec",lambda season: declared(season))
    return bootstrap, now


def test_adapter_matches_raw_filter_and_keeps_forecasts_read_only(db_session,monkeypatch):
    bootstrap, now = setup_preview(db_session,monkeypatch)
    inputs, _, _ = inputs_for(db_session,2027,now.timestamp(),bootstrap.params["context"])
    raw, _, here, total = state_fitting.replay(inputs,PARAMS,record=False)
    run = replay_adapter(inputs,PARAMS,declared())
    assert run.played_here == here and run.played_total == total
    for team, old_event in raw.latest.items():
        assert run.model.team_points(team,old_event) == pytest.approx(raw.team_points(team,old_event))
    red, blue = ("frc1","frc2","frc3"),("frc4","frc5","frc6")
    old_latest = dict(run.model.inner.latest)
    old_blocks = set(run.model.inner.blocks)
    card = MatchCard("2027new_qm1","2027new",0,"qm",0,red,blue)
    expected = copy.deepcopy(raw).predict(card)
    assert run.model.forecast("2027new",red,blue) == expected
    assert run.model.alliance_strength("2027new",red)["total"] == pytest.approx((expected.red_mean,expected.red_var))
    run.model.belief("frc1","2027other")
    assert run.model.inner.latest == old_latest and set(run.model.inner.blocks) == old_blocks
    # Alliance variance retains off-diagonal covariance after shared observations.
    block = run.model.inner.blocks["2027test"]
    assert np.any(block.cov[:block.n,:block.n]-np.diag(np.diag(block.cov[:block.n,:block.n])))
    assert run.model.bonus_rp_supported is False
    with pytest.raises(ValueError,match="distinct"):
        run.model.forecast("2027new",red,red)


def test_unknown_team_prior_and_case():
    adapter = TotalStateAdapter(ScaleFreeFilter(PARAMS,rookies={"frc100b"},previous_unit=40),declared())
    belief = adapter.belief("frc100B")["total"]
    assert belief.mean == pytest.approx(PARAMS.rookie*40)
    assert belief.var == pytest.approx((PARAMS.prior*40)**2)
    assert not adapter.inner.latest


@pytest.mark.parametrize("n,ready",[(11,False),(12,True)])
def test_preview_readiness_unsupported_fields_and_no_database_writes(db_session,monkeypatch,n,ready):
    bootstrap, now = setup_preview(db_session,monkeypatch,n=n)
    writes = []
    def record(conn,cursor,statement,params,context,many):
        if statement.lstrip().split()[0].upper() in ("INSERT","UPDATE","DELETE"):
            writes.append(statement)
    engine = db_session.get_bind()
    sql_event.listen(engine,"before_cursor_execute",record)
    try:
        out = RatingStatePreview.model_validate(state_preview.build_preview(db_session,bootstrap.id,"2027test",now=now)).model_dump(mode="json")
    finally:
        sql_event.remove(engine,"before_cursor_execute",record)
    assert not writes
    assert out["points_ready"] is ready and out["provisional"] and not out["serving_eligible"]
    assert out["rating_scale"] is None and len(out["ratings"]) == 12
    for row in out["ratings"]:
        assert (row["expected_points"] is not None) is ready
        assert row["rating_0_100"] is None
        assert all(v is None for v in row["subscores"].values())
        assert row["driver_skill_0_100"] is None and row["robot_level_0_100"] is None
        if ready:
            assert set(row["expected_points"]) == {"total"}
    assert out["provenance"]["observed_alliances"] == 2*n
    assert json.loads(json.dumps(out,allow_nan=False))["as_of"] == now.isoformat()


def test_same_live_rating_anchors_and_stored_team_spelling(db_session,monkeypatch):
    bootstrap, now = setup_preview(db_session,monkeypatch,year=2026)
    db_session.add(models.Team(team_key="frc100B",team_number=100))
    db_session.add(models.EventTeam(event_key="2026test",team_key="frc100B"))
    db_session.commit()
    out = state_preview.build_preview(db_session,bootstrap.id,"2026test",now=now)
    assert out["rating_scale"]["source"] == "live_season_baseline"
    curve = curve_for_anchors(40.47,265.1)
    for row in out["ratings"]:
        assert row["rating_0_100"] == pytest.approx(points_to_rating(row["expected_points"]["total"],*curve))
    assert "frc100B" in [r["team_key"] for r in out["ratings"]]
    # Ingested lowercase keys still resolve the stored spelling without a roster row.
    db_session.query(models.EventTeam).filter_by(team_key="frc100B").delete()
    side = db_session.query(models.MatchAllianceResult).filter_by(event_key="2026test",alliance="red").first()
    side.team_keys = ["frc100b",*side.team_keys[1:]]
    db_session.commit()
    out = state_preview.build_preview(db_session,bootstrap.id,"2026test",now=now)
    assert "frc100B" in [r["team_key"] for r in out["ratings"]]


def test_candidate_provenance_and_activation_stay_blocked(db_session,monkeypatch):
    bootstrap, now = setup_preview(db_session,monkeypatch)
    payload = {"schema":state_fitting.RECIPE,"bootstrap_id":bootstrap.id,"state":asdict(PARAMS),
               "layers":calibrations.identity(),"bootstrap_sha256":digest(bootstrap.params)}
    candidate = models.RatingCalibration(season=2027,recipe=state_fitting.RECIPE,version=1,params=payload,
        fitted_through=now-timedelta(days=1),created_at=now-timedelta(hours=12),matches=20,code_sha="test",status="candidate")
    db_session.add(candidate)
    db_session.commit()
    assert not state_preview.build_preview(db_session,candidate.id,"2027test",now=now)["provisional"]
    monkeypatch.setattr(settings,"rating_autotune",True)
    monkeypatch.setattr(settings,"rating_prediction_log_enabled",True)
    with pytest.raises(ValueError,match="state candidates cannot activate"):
        calibrations.activate(db_session,candidate.id,now=now)
    candidate.params = {**payload,"bootstrap_sha256":"wrong"}
    db_session.flush()
    with pytest.raises(ValueError,match="provenance"):
        state_preview.build_preview(db_session,candidate.id,"2027test",now=now)


def test_preview_rejects_wrong_season_future_and_undeclared(db_session,monkeypatch):
    bootstrap, now = setup_preview(db_session,monkeypatch)
    monkeypatch.setattr(state_preview,"get_spec",specs.get_spec)
    with pytest.raises(ValueError,match="no declared"):
        state_preview.build_preview(db_session,bootstrap.id,"2027test",now=now)
    monkeypatch.setattr(state_preview,"get_spec",lambda season:declared(season))
    with pytest.raises(ValueError,match="belong"):
        state_preview.build_preview(db_session,bootstrap.id,"2026test",now=now)
    bootstrap.created_at = now+timedelta(days=1)
    db_session.flush()
    with pytest.raises(ValueError,match="future"):
        state_preview.build_preview(db_session,bootstrap.id,"2027test",now=now)


def test_private_endpoint_requires_admin_and_opt_in(db_session,monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(settings,"admin_api_key","test-rating-admin")
    monkeypatch.setattr(settings,"rating_state_adapter_enabled",False)
    url = "/admin/rating/calibrations/1/preview?event_key=2026test"
    with TestClient(app) as client:
        assert client.get(url).status_code == 403
        headers = {"X-Admin-Key":"test-rating-admin"}
        assert client.get(url,headers=headers).status_code == 409
        monkeypatch.setattr(settings,"rating_state_adapter_enabled",True)
        assert client.get(url,headers=headers).json()["detail"] == "unknown or rejected total-state calibration"


def test_private_endpoint_serializes_real_preview(db_session,monkeypatch):
    bootstrap, _ = setup_preview(db_session,monkeypatch,year=2026)
    calibration_id = bootstrap.id
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr(settings,"admin_api_key","test-rating-admin")
    monkeypatch.setattr(settings,"rating_state_adapter_enabled",True)
    with TestClient(app) as client:
        response = client.get(f"/admin/rating/calibrations/{calibration_id}/preview?event_key=2026test",
                              headers={"X-Admin-Key":"test-rating-admin"})
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["count"] == 12 and preview["component_spec"]["tier"] == "A"
        assert preview["ratings"][0]["subscores"]["endgame"] is None
        assert preview["ratings"][0]["expected_points"]["total"] > 0
