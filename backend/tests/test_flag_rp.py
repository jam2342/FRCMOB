import copy
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import event as sql_event

from app.db import models
from app.services.ratings import component_spec, flag_rp_inputs, state_preview
from app.services.ratings.component_spec import BonusRp
from app.services.ratings.flag_rp import FlagRpModel, FlagRpParams
from app.services.ratings.replay import MatchCard
from tests.test_rating_total_adapter import setup_preview


def spec():
    return replace(component_spec.get_spec(2026),bonus_rps=(BonusRp("bonus","bonusFlag"),))


def card(event="2026test",level="qm"):
    return MatchCard(f"{event}_qm1",event,1700000000,level,1700000000,("frc1","frc2","frc3"),("frc4","frc5","frc6"))


def test_laplace_update_matches_direct_matrix_equations_and_keeps_covariance():
    model = FlagRpModel(spec())
    forecast = model.predict(card())
    assert forecast["red"]["bonus"]["probability"] == .5
    assert forecast["red"]["bonus"]["base_rate"] == .5
    model.observe(card(),{"red":{"bonus":True},"blue":{"bonus":False}})
    block = model.states["bonus"].blocks["2026test"]
    h = np.ones(3)
    expected_cov = np.linalg.inv(np.eye(3)+.25*np.outer(h,h))
    assert block.mean[:3] == pytest.approx(expected_cov@h*.5)
    assert block.cov[:3,:3] == pytest.approx(expected_cov+model.params.match_var*np.eye(3))
    assert block.cov[0,1] < 0
    later = model.forecast(card())
    assert later["red"]["bonus"]["probability"] > .5 > later["blue"]["bonus"]["probability"]
    assert np.linalg.eigvalsh(block.cov[:block.n,:block.n]).min() > 0


def test_forecast_is_isolated_case_normalized_and_new_event_has_more_uncertainty():
    model = FlagRpModel(spec())
    model.observe(card(),{"red":{"bonus":True}})
    state = model.states["bonus"]
    before = copy.deepcopy(state.blocks["2026test"].cov)
    latest = dict(state.latest)
    existing = model.forecast(card())
    future = model.forecast(card("2026next"))
    mutated = copy.deepcopy(model).predict(card("2026next"))
    for color in ("red","blue"):
        assert future[color]["bonus"] == pytest.approx(mutated[color]["bonus"])
    assert future["red"]["bonus"]["probability"] == existing["red"]["bonus"]["probability"]
    assert future["red"]["bonus"]["logit_sd"] > existing["red"]["bonus"]["logit_sd"]
    assert set(state.blocks) == {"2026test"} and state.latest == latest
    assert np.array_equal(before,state.blocks["2026test"].cov)
    bteam = replace(card(),red=("frc100B","frc2","frc3"))
    assert model.forecast(bteam) == model.forecast(replace(bteam,red=("frc100b","frc2","frc3")))


def test_missing_false_playoffs_and_invalid_lineups():
    model = FlagRpModel(spec())
    model.observe(card(),{"red":{"bonus":0},"blue":{"bonus":None}})
    assert model.states["bonus"].observed == 0
    model.observe(card(),{"red":{"bonus":False}})
    assert model.states["bonus"].observed == 1 and model.states["bonus"].earned == 0
    assert model.predict(card(level="sf")) == {}
    model.observe(card(level="sf"),{"red":{"bonus":True}})
    assert model.states["bonus"].observed == 1
    with pytest.raises(ValueError,match="distinct"):
        model.forecast(replace(card(),blue=card().red))
    for bad in (0,-1,float("nan"),True):
        with pytest.raises(ValueError,match="positive"):
            FlagRpParams(prior_var=bad)


def test_forecast_precedes_outcome_and_uses_only_earlier_flags():
    a, b = FlagRpModel(spec()), FlagRpModel(spec())
    earlier = {"red":{"bonus":True},"blue":{"bonus":False}}
    a.observe(card(),earlier)
    b.observe(card(),earlier)
    next_card = replace(card(),match_key="2026test_qm2",time=card().time+120)
    assert a.predict(next_card) == b.predict(next_card)
    a.observe(next_card,{"red":{"bonus":True}})
    b.observe(next_card,{"red":{"bonus":False}})
    assert a.forecast(card())["red"]["bonus"]["probability"] > b.forecast(card())["red"]["bonus"]["probability"]


def test_database_flags_use_actual_cutoff_and_keep_dq_and_missing_unknown(db_session,monkeypatch):
    bootstrap, now = setup_preview(db_session,monkeypatch,year=2026,n=12)
    declared = spec()
    rows = db_session.query(models.MatchAllianceResult).filter_by(season_year=2026).all()
    for i,row in enumerate(rows):
        row.raw_breakdown = {"bonusFlag":i%2 == 0}
    rows[0].dq_team_keys = rows[0].team_keys[:1]
    rows[1].raw_breakdown = {}
    late_key = rows[2].match_key
    db_session.get(models.Match,late_key).actual_time = int((now+timedelta(days=1)).timestamp())
    db_session.commit()
    inputs, _ = flag_rp_inputs.database_inputs(db_session,declared,now.timestamp())
    assert len(inputs) == 11 and all(c.match_key != late_key for c,_ in inputs)
    assert flag_rp_inputs.replay_flags(declared,inputs).states["bonus"].observed == 20
    sha = flag_rp_inputs.replay_flags(declared,inputs).settings_sha256
    rows[2].raw_breakdown = {"bonusFlag":True}
    db_session.commit()
    same, _ = flag_rp_inputs.database_inputs(db_session,declared,now.timestamp())
    assert inputs == same and flag_rp_inputs.replay_flags(declared,same).settings_sha256 == sha
    assert bootstrap.season == 2026


def test_preview_exposes_only_future_unscored_quals_and_never_writes(db_session,monkeypatch):
    bootstrap, now = setup_preview(db_session,monkeypatch,year=2026,n=12)
    monkeypatch.setattr(state_preview,"get_spec",lambda season:spec())
    for row in db_session.query(models.MatchAllianceResult).filter_by(season_year=2026):
        row.raw_breakdown = {"bonusFlag":True}
    for i,level in enumerate(("qm","sf","qm")):
        key = f"2026test_future{i}"
        db_session.add(models.Match(match_key=key,event_key="2026test",comp_level=level,match_number=100+i,set_number=1,time=int(now.timestamp())+(i+1)*600))
        for color,teams in (("red",card().red),("blue",card().blue)):
            for team in teams:
                db_session.add(models.MatchTeam(match_key=key,event_key="2026test",alliance=color,team_key=team))
    db_session.add(models.MatchAllianceResult(match_key="2026test_future2",event_key="2026test",season_year=2026,alliance="red",team_keys=list(card().red),total_score=10))
    db_session.commit()
    writes = []
    def record(conn,cursor,statement,params,context,many):
        if statement.lstrip().split()[0].upper() in ("INSERT","UPDATE","DELETE"):
            writes.append(statement)
    engine = db_session.get_bind()
    sql_event.listen(engine,"before_cursor_execute",record)
    try:
        out = state_preview.build_preview(db_session,bootstrap.id,"2026test",now=now)
    finally:
        sql_event.remove(engine,"before_cursor_execute",record)
    preview = out["bonus_rp_preview"]
    assert not writes and not preview["serving_eligible"] and preview["provisional"]
    assert preview["observed_alliances"] == {"bonus":24}
    assert [f["match_key"] for f in preview["forecasts"]] == ["2026test_future0"]
    assert 0 < preview["forecasts"][0]["chances"]["red"]["bonus"]["probability"] < 1
