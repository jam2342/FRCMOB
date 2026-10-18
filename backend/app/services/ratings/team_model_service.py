# The team model (ratings/team_model.py) run over a season's official results, shared by
# the ratings and the win probability. One run replays every played match in order
# (~22k for 2026) and keeps each match's prediction from before it was played. It is
# cached per process and redone only when the season's alliance results change.
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings.constants import season_baseline
from app.services.ratings.replay import Prediction, load_season
from app.services.ratings.strength import MatchPoints
from app.services.ratings.team_model import SeasonTeamModel, TeamModelParams

logger = logging.getLogger(__name__)


@dataclass
class SeasonRun:
    season: int
    model: SeasonTeamModel
    # Played matches: the prediction made just before the match, the fewest earlier
    # matches at the event among its six teams, and the win-correction features then.
    before_match: dict[str, tuple[Prediction, int, list[float]]] = field(default_factory=dict)
    played_total: dict[str, int] = field(default_factory=dict)
    played_here: dict[tuple[str, str], int] = field(default_factory=dict)
    robots_here: dict[tuple[str, str], list[MatchPoints]] = field(default_factory=dict)
    # Why a rating moved: (team, event) -> [(match_key, points before, points after)].
    changes: dict[tuple[str, str], list[tuple[str, float, float]]] = field(default_factory=dict)
    seconds: float = 0.0


_cache: dict[int, tuple[tuple, SeasonRun]] = {}
_lock = threading.Lock()


def _fingerprint(db: Session, season: int) -> tuple:
    count, latest = (
        db.query(func.count(models.MatchAllianceResult.match_key), func.max(models.MatchAllianceResult.updated_at))
        .filter(models.MatchAllianceResult.season_year == season)
        .one()
    )
    findings = (
        db.query(func.count(models.TeamMatchFinding.id))
        .filter(models.TeamMatchFinding.event_key.like(f"{season}%"))
        .scalar()
    )
    # Last season's strengths and rookie years set the starting beliefs.
    priors = (
        db.query(func.count(models.TeamPriorStrength.team_key), func.max(models.TeamPriorStrength.updated_at))
        .filter(models.TeamPriorStrength.season == season - 1)
        .one()
    )
    rookies = db.query(func.count(models.Team.team_key)).filter(models.Team.rookie_year == season).scalar()
    # The engine too: a process talking to two databases (tests) must not share a run.
    return id(db.get_bind()), int(count or 0), str(latest), int(findings or 0), int(priors[0] or 0), str(priors[1]), int(rookies or 0)


# Serve the last run while a fresh one is built (STALE_READS): a cold run takes ~13 s on
# the 2026 season, and every new result during an event changes the fingerprint.
STALE_READS = True
_refreshing: set[int] = set()


def season_run(db: Session, season: int, *, allow_stale: bool = False) -> SeasonRun:
    # The current run for `season`. With allow_stale (request paths), a run that is out of
    # date is returned at once and rebuilt in the background; ratings jobs pass False.
    fingerprint = _fingerprint(db, season)
    with _lock:
        cached = _cache.get(season)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        if cached is not None and allow_stale and STALE_READS:
            if season not in _refreshing:
                _refreshing.add(season)
                bind = db.get_bind()
                threading.Thread(target=_refresh, args=(bind, season), daemon=True, name=f"team-model-{season}").start()
            return cached[1]
    return _compute(db, season, fingerprint)


def _refresh(bind, season: int) -> None:
    from sqlalchemy.orm import sessionmaker

    try:
        db = sessionmaker(bind=bind, autoflush=False)()
        try:
            _compute(db, season, _fingerprint(db, season))
        finally:
            db.close()
    except Exception:
        logger.exception("team_model.refresh_failed season=%s", season)
    finally:
        with _lock:
            _refreshing.discard(season)


def warm_up(season: int) -> None:
    # Build the run in the background at startup, so the first request doesn't wait.
    def _warm() -> None:
        from app.db.session import SessionLocal

        try:
            db = SessionLocal()
            try:
                season_run(db, season)
            finally:
                db.close()
        except Exception:
            logger.exception("team_model.warm_up_failed season=%s", season)

    threading.Thread(target=_warm, daemon=True, name=f"team-model-warm-{season}").start()


def _compute(db: Session, season: int, fingerprint: tuple) -> SeasonRun:
    started = time.perf_counter()
    baseline = season_baseline(season)
    params: TeamModelParams = baseline["team_model_params"]
    multipliers, var_multipliers = season_prior_multipliers(db, season, baseline)
    model = SeasonTeamModel(
        dict(baseline["team_model_priors"]),
        params,
        team_multipliers=multipliers,
        team_var_multipliers=var_multipliers,
        week_multipliers=list(baseline.get("week_multipliers") or []),
    )
    run = SeasonRun(season=season, model=model)
    played_here: dict[tuple[str, str], int] = defaultdict(int)
    played_total: dict[str, int] = defaultdict(int)
    robots_here: dict[tuple[str, str], list[MatchPoints]] = defaultdict(list)
    changes: dict[tuple[str, str], list[tuple[str, float, float]]] = defaultdict(list)
    for result in load_season(db, season).results:
        card = result.card
        teams = card.red + card.blue
        prediction = model.predict(card)
        seen = min(played_here[(team, card.event_key)] for team in teams)
        before_events = min(played_total[team] - played_here[(team, card.event_key)] for team in teams)
        run.before_match[card.match_key] = (prediction, seen, correction_features(model, card, prediction, seen, before_events))
        before = {team: model.team_points(team, card.event_key) for team in teams}
        model.observe(result)
        for team in teams:
            changes[(team, card.event_key)].append((card.match_key, before[team], model.team_points(team, card.event_key)))
        for team in teams:
            played_here[(team, card.event_key)] += 1
            played_total[team] += 1
        for team, mp in result.robots.items():
            robots_here[(team, card.event_key)].append(mp)
    run.played_here = dict(played_here)
    run.played_total = dict(played_total)
    run.robots_here = dict(robots_here)
    run.changes = dict(changes)
    run.seconds = round(time.perf_counter() - started, 2)
    logger.info("team_model.season_run season=%s matches=%d seconds=%.2f", season, len(run.before_match), run.seconds)
    with _lock:
        _cache[season] = (fingerprint, run)
    return run


def season_prior_multipliers(db: Session, season: int, baseline: dict) -> tuple[dict[str, float], dict[str, float]]:
    # Each team's starting belief this season (docs/RATING_ROADMAP.md, Phase 2): last
    # season's strength z as exp(beta * z) and a narrower prior; rookies a fixed multiplier.
    import math

    beta = float(baseline.get("prior_beta", 0.0))
    known_var = float(baseline.get("prior_known_var_multiplier", 1.0))
    rookie = float(baseline.get("rookie_multiplier", 1.0))
    multipliers: dict[str, float] = {}
    var_multipliers: dict[str, float] = {}
    if beta:
        for team_key, z in db.query(models.TeamPriorStrength.team_key, models.TeamPriorStrength.z).filter(
            models.TeamPriorStrength.season == season - 1
        ):
            multipliers[team_key.lower()] = math.exp(beta * float(z))
            var_multipliers[team_key.lower()] = known_var
    if rookie != 1.0:
        for (team_key,) in db.query(models.Team.team_key).filter(models.Team.rookie_year == season):
            multipliers.setdefault(team_key.lower(), rookie)
    return multipliers, var_multipliers


def correction_features(model: SeasonTeamModel, card, prediction, seen: int, before_events: int) -> list[float]:
    # The learned correction's inputs for one match (ratings/win_correction.py).
    from app.services.events.win_probability import red_win_prob
    from app.services.ratings.win_correction import features

    margin = (prediction.red_mean + prediction.red_fouls) - (prediction.blue_mean + prediction.blue_fouls)
    sd = ((prediction.red_var or 0) + (prediction.blue_var or 0) + prediction.red_fouls_var + prediction.blue_fouls_var) ** 0.5
    playoff = str(card.comp_level or "qm").lower() != "qm"
    base = red_win_prob(margin, max(sd, 1e-3), seen, playoff=playoff)
    return features(model, card.event_key, card.red, card.blue, prediction, base, seen, before_events, playoff, card.event_start)


def clear_cache() -> None:
    with _lock:
        _cache.clear()
