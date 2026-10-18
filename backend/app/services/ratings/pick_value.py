# Pick value (docs/RATING_ROADMAP.md, Phase 5): for an alliance being built, how much
# each remaining team at the event raises its chance to win a playoff match against a
# strong alliance there. Uses the team model's beliefs, so dud risk (a robot that dies
# now and then) and how sure we are both count, not just average points.
from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.services.events.win_probability import red_win_prob
from app.services.ratings.constants import season_baseline
from app.services.ratings.ranking_points import bonus_chances, event_thresholds
from app.services.ratings.strength import curve_for_anchors, points_to_rating
from app.services.ratings.team_model_service import season_run

# A strong playoff alliance at this event: its teams at these strength percentiles.
REFERENCE_PERCENTILES = (0.95, 0.80, 0.65)


def pick_values(db: Session, event_key: str, partner_keys: list[str], limit: int = 30) -> dict[str, Any]:
    from app.services.seasons import is_frozen, is_rated

    season = int(str(event_key)[:4])
    # A past season has no live model to ask (ratings/team_model_service.py).
    if not is_rated(season) or is_frozen(db, season):
        reason = "season_not_rated" if not is_rated(season) else "season_frozen"
        return {"ok": True, "event_key": event_key, "partners": [], "candidates": [], "reference": [], "unavailable": reason}
    baseline = season_baseline(season)
    scale, shape = curve_for_anchors(baseline["median_robot_points"], baseline["p99_robot_points"])
    run = season_run(db, season, allow_stale=True)
    model = run.model
    event_start = db.query(
        func.min(func.coalesce(models.Match.actual_time, models.Match.time, models.Match.predicted_time))
    ).filter(models.Match.event_key == event_key).scalar()
    teams = sorted({str(t).lower() for (t,) in db.query(models.EventTeam.team_key).filter(models.EventTeam.event_key == event_key)})
    partners = [t.lower() for t in partner_keys if t.lower() in teams][:2]
    points = {t: sum(b.mean for b in model.belief(t, event_key, event_start).values()) for t in teams}
    ranked = sorted(teams, key=lambda t: -points[t])
    if len(ranked) < 6:
        return {"ok": True, "event_key": event_key, "partners": partners, "candidates": [], "reference": []}

    # The reference: real teams at those percentiles, not in the alliance being built.
    pool = [t for t in ranked if t not in partners]
    reference = []
    for q in REFERENCE_PERCENTILES:
        index = min(len(pool) - 1, int((1.0 - q) * len(pool)))
        while pool[index] in reference and index + 1 < len(pool):
            index += 1
        reference.append(pool[index])
    ref = model.alliance_strength(event_key, reference, event_start)["total"]
    # With only a captain, the third robot is to come: assume the event's median robot.
    filler = ranked[len(ranked) // 2]

    by_alliance = event_thresholds(
        season,
        [row.components or {} for row in db.query(models.MatchAllianceResult).filter(models.MatchAllianceResult.event_key == event_key)],
    )
    rows = []
    for candidate in teams:
        if candidate in partners:
            continue
        alliance = partners + [candidate]
        if len(alliance) < 3:
            alliance.append(filler if filler not in alliance else ranked[len(ranked) // 2 + 1])
        strength = model.alliance_strength(event_key, alliance, event_start)
        mean, var = strength["total"]
        margin = mean - ref[0]
        win = red_win_prob(margin, math.sqrt(max(1e-6, var + ref[1])), 3, playoff=True)
        belief = model.belief(candidate, event_key, event_start)
        own = sum(b.mean for b in belief.values())
        chances = bonus_chances(season, by_alliance, strength["hub"], strength["tower"])
        rows.append({
            "team_key": candidate,
            "win_vs_strong_alliance": round(win, 4),
            "alliance_points": round(mean, 1),
            "points_per_match": round(own, 1),
            "rating_0_100": round(points_to_rating(own, scale, shape), 1),
            "climb_points": round(belief["tower"].mean, 1),
            "dud_rate": round(model.dud_rate(candidate), 4),
            "bonus_rp_chances": {rp: round(p, 3) for rp, p in chances.items()},
        })
    rows.sort(key=lambda row: (-row["win_vs_strong_alliance"], -row["points_per_match"]))
    return {
        "ok": True,
        "event_key": event_key,
        "partners": partners,
        "assumed_third": filler if len(partners) < 2 else None,
        "reference": reference,
        "reference_points": round(ref[0], 1),
        "candidates": rows[: max(1, int(limit))],
    }


# A typical alliance at this event, for the second comparison.
TYPICAL_PERCENTILES = (0.60, 0.50, 0.40)


def _reference_teams(ranked: list[str], exclude: list[str], percentiles: tuple[float, ...]) -> list[str]:
    pool = [t for t in ranked if t not in exclude]
    chosen: list[str] = []
    for q in percentiles:
        index = min(len(pool) - 1, int((1.0 - q) * len(pool)))
        while pool[index] in chosen and index + 1 < len(pool):
            index += 1
        chosen.append(pool[index])
    return chosen


def alliance_outlook(db: Session, event_key: str, team_keys: list[str]) -> dict[str, Any]:
    # How good a given three-team alliance is, from the same team model as pick values:
    # its expected points and its chance to win a playoff match against a strong and a
    # typical alliance at this event (Alliance Advisor's builder).
    from app.services.seasons import is_frozen, is_rated

    season = int(str(event_key)[:4])
    if not is_rated(season) or is_frozen(db, season):
        reason = "season_not_rated" if not is_rated(season) else "season_frozen"
        return {"ok": True, "event_key": event_key, "teams": [], "unavailable": reason}
    teams_here = sorted({str(t).lower() for (t,) in db.query(models.EventTeam.team_key).filter(models.EventTeam.event_key == event_key)})
    alliance = [t.lower() for t in team_keys]
    missing = [t for t in alliance if t not in teams_here]
    if len(alliance) != 3 or len(set(alliance)) != 3 or missing:
        raise ValueError("Pick three different teams from this event.")
    if len(teams_here) < 6:
        return {"ok": True, "event_key": event_key, "teams": [], "unavailable": "too_few_teams"}

    baseline = season_baseline(season)
    scale, shape = curve_for_anchors(baseline["median_robot_points"], baseline["p99_robot_points"])
    model = season_run(db, season, allow_stale=True).model
    event_start = db.query(
        func.min(func.coalesce(models.Match.actual_time, models.Match.time, models.Match.predicted_time))
    ).filter(models.Match.event_key == event_key).scalar()
    points = {t: sum(b.mean for b in model.belief(t, event_key, event_start).values()) for t in teams_here}
    ranked = sorted(teams_here, key=lambda t: -points[t])

    strength = model.alliance_strength(event_key, alliance, event_start)
    mean, var = strength["total"]

    def against(reference: list[str]) -> dict[str, Any]:
        ref_mean, ref_var = model.alliance_strength(event_key, reference, event_start)["total"]
        win = red_win_prob(mean - ref_mean, math.sqrt(max(1e-6, var + ref_var)), 3, playoff=True)
        return {"teams": reference, "points": round(ref_mean, 1), "win_prob": round(win, 4)}

    by_alliance = event_thresholds(
        season,
        [row.components or {} for row in db.query(models.MatchAllianceResult).filter(models.MatchAllianceResult.event_key == event_key)],
    )
    chances = bonus_chances(season, by_alliance, strength["hub"], strength["tower"])
    rating_rows = {
        row.team_key: row
        for row in db.query(models.EventTeamRating).filter(
            models.EventTeamRating.event_key == event_key, models.EventTeamRating.team_key.in_(alliance)
        )
    }

    def labels(signals: object) -> list[str]:
        return [str(item["label"]) for item in (signals if isinstance(signals, list) else []) if isinstance(item, dict) and item.get("label")][:3]

    members = []
    for team in alliance:
        belief = model.belief(team, event_key, event_start)
        own = sum(b.mean for b in belief.values())
        members.append({
            "team_key": team,
            "rating_0_100": round(points_to_rating(own, scale, shape), 1),
            "points_per_match": round(own, 1),
            "climb_points": round(belief["tower"].mean, 1),
            "dud_rate": round(model.dud_rate(team), 4),
            "event_rank_by_points": ranked.index(team) + 1,
            "strengths": labels(getattr(rating_rows.get(team), "pros_json", None)),
            "risks": labels(getattr(rating_rows.get(team), "cons_json", None)),
        })
    return {
        "ok": True,
        "event_key": event_key,
        "teams": members,
        "alliance_points": round(mean, 1),
        # 80% of this alliance's matches should land in this range.
        # (A score can't go below zero; a weak alliance's normal tail would.)
        "alliance_points_range_80": [round(max(0.0, mean - 1.2816 * math.sqrt(var)), 1), round(mean + 1.2816 * math.sqrt(var), 1)],
        "vs_strong": against(_reference_teams(ranked, alliance, REFERENCE_PERCENTILES)),
        "vs_typical": against(_reference_teams(ranked, alliance, TYPICAL_PERCENTILES)),
        "bonus_rp_chances": {rp: round(p, 3) for rp, p in chances.items()},
        "team_count": len(teams_here),
    }
