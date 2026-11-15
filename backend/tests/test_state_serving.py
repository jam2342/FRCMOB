from dataclasses import asdict
from functools import partial

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services import seasons
from app.services.ratings import calibrations as layers, constants, gradual_rehearsal as g, state_fitting as state, state_serving as serving
from app.services.ratings import team_model_service as service, weekly_rehearsal as w
from app.services.ratings.state_bootstrap import build_bootstrap
from tests.test_weekly_rehearsal import PARAMS, data


@pytest.fixture
def rehearsed(tmp_path, monkeypatch):
    # A finished fixture rehearsal: scratch database with results, the bootstrap and weekly
    # early-fast versions carrying v2 layers.
    monkeypatch.setitem(w.POLICY, "compiled", False)
    monkeypatch.setattr(g, "fit_likelihood", lambda inputs, start, names, cutoff, **k: {
        "params": start.__class__(**{**asdict(start), "noise": start.noise*.98}), "converged": True, "evals": 1, "runs": 1})
    artifact = build_bootstrap(2024, {"2023": {"mle": asdict(PARAMS)}}, [{"season": 2023, "plan": [{"params": asdict(PARAMS)}]}],
                               {"fixture": "a"*64}, layer_params=layers.identity(v2=True))
    run = w.rehearse(tmp_path/"r", data(2024, 4), data(2023, 1), artifact, "p"*64, "test",
                     fit_callback=partial(g.fit_job, calibration_prior="bootstrap", schedule=g.EARLY_FAST))
    engine = w.prepare_database(tmp_path/"r"/"scratch.sqlite")
    service.clear_cache()
    yield engine, run
    service.clear_cache()
    engine.dispose()


def serve(monkeypatch, on=True):
    monkeypatch.setattr(settings, "rating_state_serving_enabled", on)
    monkeypatch.setattr(serving, "FIRST_STATE_SEASON", 2024)


def point(db, identifier):
    bundle = layers.ensure_bundle(db, 2024)
    bundle.calibration_id = identifier
    bundle.bundle_version += 1
    db.commit()


def test_declared_needs_the_switch_a_new_season_no_baseline_and_a_tier_a_spec(monkeypatch):
    monkeypatch.setattr(settings, "rating_state_serving_enabled", True)
    assert not serving.declared(2024) and not seasons.is_rated(2024)  # history never becomes state-rated
    monkeypatch.setattr(serving, "FIRST_STATE_SEASON", 2024)
    monkeypatch.setattr(settings, "rating_state_serving_enabled", False)
    assert not serving.declared(2024) and not serving.declared(2026)
    monkeypatch.setattr(settings, "rating_state_serving_enabled", True)
    assert not serving.declared(2026)  # a hand-entered baseline always wins
    assert 2026 in constants.SEASON_BASELINES and 2024 not in constants.SEASON_BASELINES
    assert serving.declared(2024) and seasons.is_rated(2024)
    assert not serving.declared(2099) and not seasons.is_rated(2099)  # no declared spec
    monkeypatch.setattr(settings, "rating_state_serving_enabled", False)
    assert not seasons.is_rated(2024)


def test_state_run_matches_the_rehearsal_replay_and_reads_like_a_season_run(rehearsed, monkeypatch):
    engine, run = rehearsed
    serve(monkeypatch)
    applied = [j["fit"]["id"] for j in run["jobs"] if j.get("applied")]
    with Session(engine) as db:
        point(db, applied[-1])
        row = serving.active_version(db, 2024)
        assert row.id == applied[-1]
        built = service.season_run(db, 2024)
        assert isinstance(built, serving.StateSeasonRun) and built.version_id == row.id
        params, fitted, bootstrap = serving.resolve(db, row)
        inputs, _, _ = state.inputs_for(db, 2024, 10**10, bootstrap.params["context"])
        _, table, here, total = state.replay(inputs, params)
    assert list(built.before_match) == [str(k) for k in table["key"]]
    for i, (prediction, seen, row_features) in enumerate(built.before_match.values()):
        assert row_features["rm"] == table["rm"][i] and row_features["new_r"] == table["new_r"][i]
        assert prediction.red_mean == table["rm"][i]
    assert built.played_here == here and built.played_total == total
    team = next(iter(total))
    belief = built.model.belief(team)["total"]
    assert belief.mean > 0 and built.model.dud_rate(team) is None and built.robots_here == {}
    assert built.model.start_basis(team) in ("last_season", "rookie", "season_typical")
    r, b, p = layers.apply({k: np.array([v]) for k, v in {**row_features, "y": .5}.items()}, fitted)
    assert r[0] > 0 and b[0] > 0 and 0 < p[0] < 1


def test_dispatch_falls_back_only_for_legacy_seasons_and_refuses_unrated_state_seasons(rehearsed, monkeypatch):
    engine, run = rehearsed
    serve(monkeypatch)
    with Session(engine) as db:
        with pytest.raises(ValueError, match="no active state rating version"):
            service.season_run(db, 2024)  # declared, nothing installed: never 2026 constants
        bootstrap = db.query(models.RatingCalibration).filter_by(season=2024, recipe=serving.BOOTSTRAP_RECIPE).one()
        point(db, bootstrap.id)
        assert service.season_run(db, 2024).version_id == bootstrap.id
        first = service.season_run(db, 2024)
        applied = [j["fit"]["id"] for j in run["jobs"] if j.get("applied")]
        point(db, applied[0])
        assert service.season_run(db, 2024) is not first  # a new active version rebuilds
        rejected = db.query(models.RatingCalibration).filter_by(season=2024).filter(models.RatingCalibration.status == "rejected").first()
        other = db.query(models.RatingCalibration).filter(models.RatingCalibration.recipe.notin_(serving.SERVED_RECIPES)).first()
        for row in (rejected, other):
            if row is not None:
                point(db, row.id)
                assert serving.active_version(db, 2024) is None
    monkeypatch.setattr(settings, "rating_state_serving_enabled", False)
    with Session(engine) as db:
        assert serving.active_version(db, 2024) is None
