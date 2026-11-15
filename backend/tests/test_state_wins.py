import copy
import pickle

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.db import models
from app.services.events import win_probability as wp
from app.services.ratings import calibrations as layers, state_serving as serving, team_model_service as service, win_correction
from app.services.ratings.replay import MatchCard
from tests.test_state_serving import point, rehearsed, serve  # noqa: F401


def served_run(engine, run, monkeypatch):
    serve(monkeypatch)
    applied = [j["fit"]["id"] for j in run["jobs"] if j.get("applied")]
    with Session(engine) as db:
        point(db, applied[-1])
        return service.season_run(db, 2024)


def test_state_features_are_the_v2_layers_on_the_row(rehearsed, monkeypatch):  # noqa: F811
    engine, run = rehearsed
    built = served_run(engine, run, monkeypatch)
    _prediction, _seen, row = next(iter(built.before_match.values()))
    features = serving.StateFeatures(row, built.layers, built.version_id)
    t = {k: np.array([float(v)]) for k, v in row.items()}
    t["y"] = np.array([.5])
    red, blue, p = layers.apply(t, built.layers)
    assert (features.red_points, features.blue_points, features.win_prob) == (float(red[0]), float(blue[0]), float(p[0]))
    assert features.red_points > 0 and features.blue_points > 0
    assert win_correction.corrected(2024, .123, features) == features.win_prob


def test_upcoming_forecast_leaves_the_model_untouched_and_matches_a_full_copy(rehearsed, monkeypatch):  # noqa: F811
    engine, run = rehearsed
    built = served_run(engine, run, monkeypatch)
    teams = sorted(built.played_total)[:6]
    event = next(iter({e for _t, e in built.played_here}))
    card = MatchCard(f"{event}_qm999", event, 0, "qm", built.first, tuple(teams[:3]), tuple(teams[3:]))
    before = pickle.dumps(built.model.inner)
    prediction, features = serving.forecast_row(built, card)
    assert pickle.dumps(built.model.inner) == before
    full = copy.deepcopy(built.model.inner)
    assert full.predict(card) == prediction
    assert 0 < features.win_prob < 1 and features.version_id == built.version_id


def test_match_predictions_and_matchup_serve_the_recipe(rehearsed, monkeypatch):  # noqa: F811
    engine, run = rehearsed
    built = served_run(engine, run, monkeypatch)
    played_key = next(iter(built.before_match))
    event = played_key.split("_")[0]
    teams = sorted({t for t, e in built.played_here if e == event})[:6]
    with Session(engine) as db:
        db.merge(models.Event(event_key=event, year=2024, name="fixture"))
        for team in teams:
            db.merge(models.EventTeam(event_key=event, team_key=team))
        db.commit()
        assert wp.match_predictions(db, event, [(played_key, [], [])]) == {}  # an empty alliance is skipped, as before
        played = service.season_run(db, 2024).results[0].card
        details = wp.match_predictions(db, event, [(played.match_key, list(played.red), list(played.blue)),
                                                   (f"{event}_qm999", teams[:3], teams[3:])])
        for key, (_margin, _sd, _seen, _prediction, features) in details.items():
            assert isinstance(features, serving.StateFeatures)
            assert win_correction.corrected(2024, .5, features) == features.win_prob
        assert details[played.match_key][4].row == built.before_match[played.match_key][2]
        matchup = wp.custom_matchup(db, event, teams[:3], teams[3:])
    expected = details[f"{event}_qm999"][4]
    assert matchup["red_win_prob"] == pytest.approx(expected.win_prob, abs=1e-4)
    assert matchup["red_points"] == pytest.approx(expected.red_points, abs=.06)
