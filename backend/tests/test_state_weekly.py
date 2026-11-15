from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from functools import partial

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.ratings import calibrations as layers, gradual_rehearsal as g, state_serving as serving, state_weekly as job
from app.services.ratings import weekly_rehearsal as w
from app.services.ratings.state_bootstrap import BOOTSTRAP_RECIPE, build_bootstrap
from tests.test_weekly_rehearsal import PARAMS, data


def artifact():
    return build_bootstrap(2024, {"2023": {"mle": asdict(PARAMS)}}, [{"season": 2023, "plan": [{"params": asdict(PARAMS)}]}],
                           {"fixture": "a"*64}, layer_params=layers.identity(v2=True))


@pytest.fixture
def production(tmp_path, monkeypatch):
    # A season one week in (bootstrap installed, no weekly versions yet) with every result since
    # loaded, as the production database would hold it mid-season.
    monkeypatch.setitem(w.POLICY, "compiled", False)
    monkeypatch.setattr(g, "fit_likelihood", lambda inputs, start, names, cutoff, **k: {
        "params": start.__class__(**{**asdict(start), "noise": start.noise*.98}), "converged": True, "evals": 1, "runs": 1})
    current = data(2024, 4)
    w.rehearse(tmp_path/"r", current, data(2023, 1), artifact(), "p"*64, "test", max_weeks=1,
               fit_callback=partial(g.fit_job, calibration_prior="bootstrap", schedule=g.EARLY_FAST))
    engine = w.prepare_database(tmp_path/"r"/"scratch.sqlite")
    with Session(engine) as db:
        for row in db.query(models.RatingCalibration).filter(models.RatingCalibration.recipe != BOOTSTRAP_RECIPE):
            assert row.fitted_through is not None  # the one rehearsed week stays as history
        w.ingest(db, current.records, 2024)
        db.commit()
    monkeypatch.setattr(settings, "rating_state_serving_enabled", True)
    monkeypatch.setattr(settings, "rating_autotune", True)
    monkeypatch.setattr(serving, "FIRST_STATE_SEASON", 2024)
    yield engine, current
    engine.dispose()


def monday(current, weeks):
    first = datetime.fromtimestamp(current.records[0]["result"].card.time, timezone.utc)
    return layers.monday(first)+timedelta(days=7*weeks)


def test_install_points_the_season_at_its_v2_bootstrap_and_refuses_v1(production):
    engine, _ = production
    with Session(engine) as db:
        result = job.install(db, 2024, artifact(), "test")
        assert serving.active_version(db, 2024).id == result["id"]
        v1 = artifact()
        v1["layers"] = layers.identity()
        with pytest.raises(ValueError, match="v2"):
            job.install(db, 2024, v1, "test")


def test_switches_gate_every_step(production, monkeypatch):
    engine, current = production
    now = monday(current, 3)
    with Session(engine) as db:
        job.install(db, 2024, artifact(), "test")
        monkeypatch.setattr(settings, "rating_autotune", False)
        assert job.fit(db, 2024, now=now, code_sha="t") == {"status": "disabled"}
        monkeypatch.setattr(settings, "rating_autotune", True)
        monkeypatch.setattr(settings, "rating_state_serving_enabled", False)
        assert job.publish(db, 2024, now=now+timedelta(hours=10)) == {"status": "disabled"}


def test_fit_then_publish_moves_the_pointer_under_the_candidates_own_limits(production):
    engine, current = production
    week = monday(current, 3)
    with Session(engine) as db:
        boot = job.install(db, 2024, artifact(), "test")["id"]
        assert job.safety(db, 2024, now=week+timedelta(hours=9))["status"] == "baseline"
        fitted = job.fit(db, 2024, now=week+timedelta(hours=9), code_sha="t")
        assert fitted["status"] == "candidate"
        candidate = db.get(models.RatingCalibration, fitted["id"])
        assert candidate.recipe == g.EARLY_FAST["recipe"] and candidate.params["parent_active"] == boot
        assert candidate.params["anchors"] and candidate.params["anchors"][1] > candidate.params["anchors"][0] > 0
        assert job.publish(db, 2024, now=week+timedelta(hours=9, minutes=59))["status"] == "before_publication"
        published = job.publish(db, 2024, now=week+timedelta(hours=10))
        assert published["status"] == "published"
        assert published["impact"]["limits"] == candidate.params["impact_limits"] == [3., 15.]  # week 3: early limits
        assert serving.active_version(db, 2024).id == candidate.id and candidate.status == "active"
        assert job.publish(db, 2024, now=week+timedelta(hours=11))["status"] == "no_candidate"


def test_safety_rolls_back_to_the_bootstrap_only_on_clear_evidence(production):
    engine, current = production
    week = monday(current, 3)
    with Session(engine) as db:
        boot = job.install(db, 2024, artifact(), "test")["id"]
        candidate = db.get(models.RatingCalibration, job.fit(db, 2024, now=week+timedelta(hours=9), code_sha="t")["id"])
        bundle = layers.ensure_bundle(db, 2024)
        bundle.calibration_id, bundle.previous_id = candidate.id, boot
        db.flush()
        matches = db.query(models.Match).join(models.Event).filter(models.Event.year == 2024).filter(models.Match.actual_time.is_not(None)).all()
        results = {}
        for r in db.query(models.MatchAllianceResult).filter_by(season_year=2024):
            results.setdefault(r.match_key, {})[r.alliance] = r.total_score
        now = datetime.fromtimestamp(max(m.actual_time for m in matches)+3600, timezone.utc)
        logged = 0
        for match in matches:
            sides = results.get(match.match_key, {})
            if match.actual_time < now.timestamp()-13*86400 or len(sides) < 2 or sides["red"] == sides["blue"]:
                continue
            y = float(sides["red"] > sides["blue"])
            shown = datetime.fromtimestamp(match.actual_time-600, timezone.utc)
            db.add(models.MatchPredictionLog(match_key=match.match_key, event_key=match.event_key, season=2024, first_shown_at=shown,
                first={"red_win_prob": 1-y}, final_at=shown, final={"red_win_prob": .9 if y == 0 else .1}, bundle_version=1,
                locked_at=shown+timedelta(minutes=20), backfilled=False,
                features={"schema": serving.STATE_FEATURE_SCHEMA, "version": candidate.id, "bootstrap": {"p": .7 if y else .3}}))
            logged += 1
        db.flush()
        assert logged >= job.SAFETY_MATCHES
        result = job.safety(db, 2024, now=now)
        assert result["status"] == "rolled_back" and result["comparison"]["lo90"] > 0
        assert serving.active_version(db, 2024).id == boot and candidate.status == "rolled_back"
