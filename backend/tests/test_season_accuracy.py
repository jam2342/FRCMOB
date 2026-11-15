from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.db import models
from app.services import seasons
from app.services.ratings.accuracy import season_accuracy
from app.services.ratings import state_weekly as job
from tests.test_state_weekly import artifact, production  # noqa: F401


def log(db, match, *, p, points=None, shown=None, first=None, locked=True, backfilled=False):
    shown = shown or datetime.fromtimestamp(match.actual_time-600, timezone.utc)
    final = {"red_win_prob": p, **({"red_points": points[0], "blue_points": points[1]} if points else {})}
    db.add(models.MatchPredictionLog(match_key=match.match_key, event_key=match.event_key, season=2024,
        first_shown_at=first or shown, first=final, final_at=shown, final=final, bundle_version=1,
        locked_at=datetime.fromtimestamp(match.actual_time, timezone.utc) if locked else None, backfilled=backfilled, features={}))


def test_accuracy_counts_only_forecasts_shown_before_the_match(production):  # noqa: F811
    engine, _ = production
    with Session(engine) as db:
        matches = sorted(db.query(models.Match).join(models.Event).filter(models.Event.year == 2024,
                         models.Match.actual_time.is_not(None)).all(), key=lambda m: m.match_key)
        results = {}
        for r in db.query(models.MatchAllianceResult).filter_by(season_year=2024):
            results.setdefault(r.match_key, {})[r.alliance] = r
        decided = [m for m in matches if len(results.get(m.match_key, {})) == 2
                   and results[m.match_key]["red"].total_score != results[m.match_key]["blue"].total_score][:7]
        a, b, c, d, e, f, late = decided
        wins = {m.match_key: results[m.match_key]["red"].total_score > results[m.match_key]["blue"].total_score for m in decided}
        for m in (a, b, c):
            log(db, m, p=.8 if wins[m.match_key] else .2, points=(100., 90.))  # right, confident
        log(db, d, p=.3 if wins[d.match_key] else .7)  # wrong
        log(db, e, p=.9, backfilled=True)  # reconstructed: never counts
        log(db, f, p=.9, locked=False)  # not locked: never counts
        start = datetime.fromtimestamp(late.actual_time, timezone.utc)
        log(db, late, p=.6 if wins[late.match_key] else .4, shown=start+timedelta(minutes=1), first=start+timedelta(minutes=2))
        db.flush()
        out = season_accuracy(db, 2024)
    overall = out["overall"]
    assert out["available"] and overall["matches"] == overall["decided"] == 4  # the late capture has no valid pre-match forecast
    assert overall["accuracy"] == .75 and overall["brier"] == pytest.approx((3*.2**2+.7**2)/4)
    red_errors = [abs(results[m.match_key]["red"].score_without_fouls-100.) for m in (a, b, c)]
    blue_errors = [abs(results[m.match_key]["blue"].score_without_fouls-90.) for m in (a, b, c)]
    assert overall["points_mae"] == pytest.approx(round(sum(red_errors+blue_errors)/6, 2))
    bins = {row["favourite_from"]: row for row in out["calibration"]}
    assert bins[.8]["matches"] == 3 and bins[.8]["won"] == 1. and bins[.7]["matches"] == 1 and bins[.7]["won"] == 0.
    assert sum(w["matches"] for w in out["by_week"]) == 4


def test_season_info_names_the_active_state_version(production):  # noqa: F811
    engine, _ = production
    with Session(engine) as db:
        assert seasons.season_info(db, 2024)["model"] is None  # nothing installed yet
        installed = job.install(db, 2024, artifact(), "test")
        note = seasons.season_info(db, 2024)["model"]
        assert seasons.season_info(db, 2026)["model"] is None  # legacy seasons have no note
    assert note["version"] == installed["id"] and note["provisional"] is True and note["matches"] > 0
