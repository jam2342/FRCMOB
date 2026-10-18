# Event team ratings (rating_v16): a team's expected points per match from the team
# model (team_model.py), which reads official alliance totals match by match, on one
# 0-100 scale for the whole season (median robot 50, 99th percentile 95).
#
# Replayed on the 56 newest 2026 events (scripts/evaluate_rating_replay.py): ties v15
# at picking winners once v15's auto leak is removed (Brier 0.152), better in playoffs
# (0.147 vs 0.155) and on alliance points (65 vs 70 error), mid-season Brier 0.149 vs
# 0.1525. See docs/RATING_ROADMAP.md.
from __future__ import annotations

import logging
import math
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings.constants import MODEL_VERSION, season_baseline
from app.services.ratings.output_builder import build_response_payload
from app.services.ratings.signals import team_signals
from app.services.ratings.strength import MatchPoints, Strength, curve_for_anchors, points_to_rating, scale_for_median
from app.services.ratings.team_model_service import season_run
from app.services.scouting_rooms.elite_detector import RoleClassifier

logger = logging.getLogger(__name__)


def _match_time():
    return func.coalesce(models.Match.actual_time, models.Match.time, models.Match.predicted_time)


def _event_start(db: Session, event_key: str) -> int | None:
    value = db.query(func.min(_match_time())).filter(models.Match.event_key == event_key).scalar()
    return int(value) if value else None


def _consistency(matches: list[MatchPoints]) -> float | None:
    totals = [sum(v for v in (m.teleop, m.auto, m.climb) if v is not None) for m in matches]
    if len(totals) < 3:
        return None
    mean = statistics.fmean(totals)
    if mean <= 0:
        return 0.0
    # 100 = the same output every match; a spread as large as the mean scores 0.
    return max(0.0, min(100.0, 100.0 * (1.0 - statistics.pstdev(totals) / mean)))


def _trend(matches: list[MatchPoints]) -> dict[str, Any] | None:
    # Last 4 matches against the ones before them, once there are 8 here.
    ordered = sorted(matches, key=lambda m: (m.time, m.match_key))
    if len(ordered) < 8:
        return None

    def mean_points(items: list[MatchPoints]) -> float:
        return statistics.fmean(sum(v for v in (m.teleop, m.auto, m.climb) if v is not None) for m in items)

    recent, earlier = mean_points(ordered[-4:]), mean_points(ordered[:-4])
    return {"recent_points": round(recent, 2), "earlier_points": round(earlier, 2), "delta_points": round(recent - earlier, 2)}


def _team_strength(run, team: str, event_key: str, event_start: int | None, baseline: dict) -> tuple[Strength, float]:
    # A team's expected points per match from the team model: its belief at this event
    # if it has played here, else as it enters this event from its earlier events, else
    # the season's prior. Also returns the belief's standard deviation in points.
    beliefs = run.model.belief(team, event_key, event_start)
    matches_here = run.played_here.get((team, event_key), 0)
    starts = run.model.event_start
    matches_before = sum(
        run.played_here.get((team, other), 0)
        for other in run.model.history.get(team, [])
        if other != event_key and (event_start is None or starts.get(other, 0) < event_start)
    )
    variance = sum(b.var for b in beliefs.values())
    prior_variance = sum(run.model.params.of(name).prior_var for name in ("teleop", "auto", "tower"))
    # No matches this season yet: the start comes from last season's strength, the rookie
    # multiplier, or the season's typical robot (team_model_service.season_prior_multipliers).
    basis = (
        "this_event" if matches_here
        else "earlier_events" if matches_before
        else "last_season" if team in run.model.team_var_multipliers
        else "rookie" if team in run.model.team_multipliers
        else "season_typical"
    )
    strength = Strength(
        points=max(0.0, sum(b.mean for b in beliefs.values())),
        teleop=max(0.0, beliefs["teleop"].mean),
        auto=max(0.0, beliefs["auto"].mean),
        climb=max(0.0, beliefs["tower"].mean),
        matches_here=matches_here,
        matches_before=matches_before,
        # 0 for a team we know nothing about, toward 1 as its belief narrows.
        confidence=round(max(0.0, min(1.0, 1.0 - math.sqrt(variance / prior_variance))), 4) if prior_variance > 0 else 0.0,
        basis=basis,
    )
    return strength, math.sqrt(max(0.0, variance))


def _sub(points: float, typical: float) -> float:
    # A component on the same curve as the rating: the season's typical robot -> 50.
    return round(points_to_rating(points, scale_for_median(typical)), 3) if typical > 0 else 0.0


def recompute_event_ratings(db: Session, event_key: str) -> dict[str, Any]:
    from app.services.seasons import is_frozen, is_rated

    started = time.perf_counter()
    season = int(str(event_key)[:4])
    # A frozen season's ratings are final, and a season without a baseline has none: a
    # refresh never rewrites either with another season's code (services/seasons.py).
    if is_frozen(db, season) or not is_rated(season):
        reason = "season_frozen" if is_rated(season) else "season_not_rated"
        return {"ok": True, "event_key": event_key, "count": 0, "ratings": [], "skipped": reason, "duration_sec": 0.0}
    team_rows = (
        db.query(models.EventTeam, models.Team)
        .join(models.Team, models.Team.team_key == models.EventTeam.team_key)
        .filter(models.EventTeam.event_key == event_key)
        .all()
    )
    if not team_rows:
        return {"ok": True, "event_key": event_key, "count": 0, "ratings": [], "duration_sec": round(time.perf_counter() - started, 4)}

    baseline = season_baseline(season)
    typical = baseline["typical"]
    scale_points, shape = curve_for_anchors(baseline["median_robot_points"], baseline["p99_robot_points"])
    event_start = _event_start(db, event_key)
    run = season_run(db, season)

    role_findings: dict[str, list[models.TeamMatchFinding]] = defaultdict(list)
    for finding in db.query(models.TeamMatchFinding).filter(models.TeamMatchFinding.event_key == event_key):
        role_findings[finding.team_key].append(finding)

    now = datetime.now(timezone.utc)
    # Keep the loaded rows referenced so merge() finds them in the session.
    _existing = db.query(models.EventTeamRating).filter(models.EventTeamRating.event_key == event_key).all()
    for event_team, _team in team_rows:
        team_key = event_team.team_key
        lowered = str(team_key).lower()
        here = run.robots_here.get((lowered, event_key), [])
        strength, sd = _team_strength(run, lowered, event_key, event_start, baseline)

        rating = points_to_rating(strength.points, scale_points, shape)
        # 80% range for the rating: the belief's spread through the same curve.
        rating_range = [
            round(points_to_rating(max(0.0, strength.points - 1.2816 * sd), scale_points, shape), 2),
            round(points_to_rating(strength.points + 1.2816 * sd, scale_points, shape), 2),
        ]
        scoring = points_to_rating(strength.teleop + strength.auto, scale_for_median(typical["teleop"] + typical["auto"]))
        consistency = _consistency(here)
        trend = _trend(here)
        climb_max = float(baseline["climb_max_points"])
        subscores = {
            "throughput": _sub(strength.teleop, typical["teleop"]),
            "auto_contribution": _sub(strength.auto, typical["auto"]),
            "endgame": round(100.0 * min(1.0, strength.climb / climb_max), 3) if climb_max > 0 else 0.0,
            "consistency": round(consistency, 3) if consistency is not None else None,
        }
        try:
            role_classification = RoleClassifier(db, event_key, team_key, findings=role_findings.get(team_key, [])).classify()
        except Exception:
            role_classification = {"primary_role": "unknown"}

        pros, cons = team_signals(strength=strength, here=here, typical=typical, subscores=subscores, trend=trend)
        evidence: list[dict[str, Any]] = []
        seen_evidence: set[tuple[str, str]] = set()
        for signal in pros + cons:
            for entry in signal.get("evidence", []):
                key = (str(entry.get("match_key") or ""), str(entry.get("metric") or ""))
                if key[0] and key not in seen_evidence:
                    seen_evidence.add(key)
                    evidence.append(entry)

        moves = run.changes.get((lowered, event_key), [])[-12:]
        details = {
            "algorithm_mode": "team_model_v16",
            "expected_points": {
                "total": round(strength.points, 2),
                "teleop": round(strength.teleop, 2),
                "auto": round(strength.auto, 2),
                "climb": round(strength.climb, 2),
            },
            "basis": strength.basis,
            "points_sd": round(sd, 2),
            # Chance this robot scores ~nothing in a match. Not shown: a weak robot's
            # breakdown is invisible in alliance totals, so the rate flags strong robots.
            "dud_rate": round(run.model.dud_rate(lowered), 4),
            # The last matches here that moved this rating, newest first.
            "recent_changes": [
                {
                    "match_key": match_key,
                    "points_change": round(after - before, 2),
                    "rating_change": round(
                        points_to_rating(max(0.0, after), scale_points, shape) - points_to_rating(max(0.0, before), scale_points, shape), 2
                    ),
                }
                for match_key, before, after in reversed(moves[-5:])
            ],
            # The rating before the first of the last 12 matches here, then after each
            # (oldest first): the live trend's sparkline (ratings/snapshots.py).
            "rating_history": [
                round(points_to_rating(max(0.0, value), scale_points, shape), 2)
                for value in ([moves[0][1]] + [after for _key, _before, after in moves] if moves else [])
            ],
            "rating_range_80": rating_range,
            "matches_here": strength.matches_here,
            "matches_before": strength.matches_before,
            "match_count": strength.matches_here,
            "season_scale": {
                "median_robot_points": baseline["median_robot_points"],
                "p99_robot_points": baseline["p99_robot_points"],
                "scale_points": round(scale_points, 3),
                "shape": round(shape, 4),
                "typical_robot": typical,
            },
            "subscores": subscores,
            "trend": trend,
            "role_classification": role_classification,
            "computed_at": now.isoformat(),
        }
        db.merge(
            models.EventTeamRating(
                event_key=event_key,
                team_key=team_key,
                rating_0_100=round(rating, 3),
                confidence_0_1=strength.confidence,
                robot_level_0_100=round(scoring, 3),
                # Driver skill, an OPR anchor, shift productivity and capacity use
                # can't be told apart from official results.
                driver_skill_0_100=None,
                results_anchor=None,
                throughput=subscores["throughput"],
                shift_productivity=None,
                capacity_utilization=None,
                endgame=subscores["endgame"],
                consistency=subscores["consistency"],
                pros_json=pros,
                cons_json=cons,
                evidence_json=evidence,
                details_json=details,
                model_version=MODEL_VERSION,
                updated_at=now,
            )
        )
    db.commit()

    persisted = (
        db.query(models.EventTeamRating, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.EventTeamRating.team_key)
        .filter(models.EventTeamRating.event_key == event_key)
        .order_by(models.EventTeamRating.rating_0_100.desc(), models.EventTeamRating.team_key.asc())
        .all()
    )
    payload = build_response_payload(persisted)
    return {
        "ok": True,
        "event_key": event_key,
        "model_version": MODEL_VERSION,
        "duration_sec": round(time.perf_counter() - started, 4),
        "count": len(payload),
        "ratings": payload,
    }
