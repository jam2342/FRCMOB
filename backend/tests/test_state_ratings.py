from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings import model as ratings, state_serving as serving
from app.services.ratings import team_model_service as service
from tests.test_state_serving import point, rehearsed, serve  # noqa: F401


class Belief:
    def __init__(self, mean):
        self.mean = mean


def fake_run(points, unit=30., stored=None):
    played = {(f"frc{i}", "e"): n for i, (_, n) in enumerate(points)}
    model = SimpleNamespace(inner=SimpleNamespace(unit=SimpleNamespace(value=lambda: unit)),
                            belief=lambda team, event: {"total": Belief(points[int(team[3:])][0])})
    return SimpleNamespace(model=model, played_here=played, stored_anchors=stored)


def test_anchors_start_from_the_default_shape_and_blend_toward_the_population():
    median = serving.ANCHOR_DEFAULT["median_over_unit"]*30.
    default = (median, median*serving.ANCHOR_DEFAULT["p99_over_median"])
    assert serving.anchors(fake_run([])) == pytest.approx(default)
    assert serving.anchors(fake_run([(10., 4)]*50)) == pytest.approx(default)  # fewer than 5 matches here
    population = [(float(10+i), 5) for i in range(300)]
    median_measured = 10+149.5
    blended = serving.anchors(fake_run(population))
    assert blended[0] == pytest.approx(.5*median_measured+.5*default[0])
    assert serving.anchors(fake_run(population, stored=[40., 200.])) == (40., 200.)
    flat = serving.anchors(fake_run([(0., 6)]*2000))  # a scoreless population pulls the anchors down
    assert 0 < flat[0] < default[0] and flat[1] > flat[0]


def test_state_season_ratings_are_total_only_and_rank_like_expected_points(rehearsed, monkeypatch):  # noqa: F811
    engine, run = rehearsed
    serve(monkeypatch)
    applied = [j["fit"]["id"] for j in run["jobs"] if j.get("applied")]
    with Session(engine) as db:
        point(db, applied[-1])
        built = service.season_run(db, 2024)
        event = next(iter({key[1] for key in built.played_here}))
        teams = sorted({team for team, e in built.played_here if e == event})
        db.merge(models.Event(event_key=event, year=2024, name="fixture"))
        for team in teams:
            db.merge(models.EventTeam(event_key=event, team_key=team))
        db.commit()
        result = ratings.recompute_event_ratings(db, event)
        rows = db.query(models.EventTeamRating).filter_by(event_key=event).all()
    assert result["ok"] and result["model_version"] == ratings.STATE_MODEL_VERSION and len(rows) == len(teams)
    for row in rows:
        assert row.throughput is None and row.endgame is None and row.consistency is None
        assert row.pros_json == [] and row.model_version == ratings.STATE_MODEL_VERSION
        details = row.details_json
        assert details["expected_points"]["teleop"] is None and details["dud_rate"] is None
        assert details["season_scale"]["anchors"] == "provisional" and details["rating_version"] == applied[-1]
        low, high = details["rating_range_80"]
        assert 0 <= low <= row.rating_0_100 <= high <= 100 and 0 <= row.confidence_0_1 <= 1
    ordered = sorted(rows, key=lambda r: r.details_json["expected_points"]["total"])
    assert [r.rating_0_100 for r in ordered] == sorted(r.rating_0_100 for r in rows)
