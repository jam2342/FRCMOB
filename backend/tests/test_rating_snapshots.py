# Tests for the live-rating snapshot time series and trend computation.
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.services.ratings.snapshots import (
    TREND_FLAT_EPSILON,
    compute_event_rating_trends,
    prune_rating_snapshots,
    record_event_rating_snapshots,
)


def _seed_event_team(db, event_key="2026test", team_key="frc1", rating=50.0):
    db.add(models.Event(event_key=event_key, name="Test Event", year=2026))
    db.add(models.Team(team_key=team_key, team_number=1, nickname="T"))
    db.add(
        models.EventTeamRating(
            event_key=event_key,
            team_key=team_key,
            rating_0_100=rating,
            confidence_0_1=0.5,
            model_version="rating_test",
        )
    )
    db.commit()


def test_record_snapshots_one_row_per_team(db_session):
    _seed_event_team(db_session)
    written = record_event_rating_snapshots(db_session, "2026test")
    assert written == 1
    rows = db_session.query(models.RatingSnapshot).all()
    assert len(rows) == 1
    assert rows[0].rating_0_100 == 50.0


def test_trend_direction_and_delta(db_session):
    _seed_event_team(db_session, rating=50.0)
    record_event_rating_snapshots(db_session, "2026test")

    # bump the rating and snapshot again -> should read as "up"
    row = db_session.query(models.EventTeamRating).one()
    row.rating_0_100 = 57.0
    db_session.commit()
    record_event_rating_snapshots(db_session, "2026test")

    trends = compute_event_rating_trends(db_session, "2026test")
    t = trends["frc1"]
    assert t["latest_0_100"] == 57.0
    assert t["previous_0_100"] == 50.0
    assert t["delta"] == 7.0
    assert t["direction"] == "up"
    assert t["snapshot_count"] == 2
    assert t["sparkline"] == [50.0, 57.0]


def test_trend_flat_within_epsilon(db_session):
    _seed_event_team(db_session, rating=50.0)
    record_event_rating_snapshots(db_session, "2026test")
    row = db_session.query(models.EventTeamRating).one()
    row.rating_0_100 = 50.0 + (TREND_FLAT_EPSILON / 2.0)
    db_session.commit()
    record_event_rating_snapshots(db_session, "2026test")

    trends = compute_event_rating_trends(db_session, "2026test")
    assert trends["frc1"]["direction"] == "flat"


def test_single_snapshot_is_flat_zero_delta(db_session):
    _seed_event_team(db_session, rating=42.0)
    record_event_rating_snapshots(db_session, "2026test")
    trends = compute_event_rating_trends(db_session, "2026test")
    t = trends["frc1"]
    assert t["delta"] == 0.0
    assert t["direction"] == "flat"
    assert t["snapshot_count"] == 1


def test_sparkline_capped_to_points(db_session):
    _seed_event_team(db_session, rating=50.0)
    for i in range(20):
        row = db_session.query(models.EventTeamRating).one()
        row.rating_0_100 = 50.0 + i
        db_session.commit()
        record_event_rating_snapshots(db_session, "2026test")
    trends = compute_event_rating_trends(db_session, "2026test", points=12)
    assert len(trends["frc1"]["sparkline"]) == 12
    assert trends["frc1"]["snapshot_count"] == 20


def test_prune_drops_old_snapshots(db_session):
    _seed_event_team(db_session, rating=50.0)
    old = models.RatingSnapshot(
        event_key="2026test",
        team_key="frc1",
        rating_0_100=50.0,
        captured_at=datetime.now(timezone.utc) - timedelta(days=45),
    )
    db_session.add(old)
    db_session.commit()
    record_event_rating_snapshots(db_session, "2026test")  # one fresh row

    deleted = prune_rating_snapshots(db_session, older_than_days=30)
    assert deleted == 1
    assert db_session.query(models.RatingSnapshot).count() == 1
