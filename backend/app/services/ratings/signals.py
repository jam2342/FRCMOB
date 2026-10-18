# Strengths and risks for a team, in plain words, from the same official points the
# rating uses. Each signal names the matches it rests on.
from __future__ import annotations

from typing import Any

from app.services.ratings.strength import MatchPoints, Strength

# How far above or below the season's typical robot a component must be to be called out.
STRONG_RATIO = 1.6
WEAK_RATIO = 0.5
MIN_MATCHES = 3


def _signal(
    rule_id: str,
    label: str,
    rationale: str,
    *,
    metric: str,
    value: float,
    strength: Strength,
    impact: str,
    category: str,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence = evidence or []
    return {
        "rule_id": rule_id,
        "label": label,
        "rationale": rationale,
        "metric": metric,
        "metric_value": round(float(value), 2),
        "sample_size": strength.matches_here,
        "confidence_0_1": strength.confidence,
        "signal_confidence_0_1": strength.confidence,
        "evidence_count": len(evidence),
        "evidence": evidence,
        "impact": impact,
        "category": category,
    }


def _best(here: list[MatchPoints], field: str, n: int = 3, reverse: bool = True) -> list[dict[str, Any]]:
    rows = [m for m in here if getattr(m, field) is not None]
    rows.sort(key=lambda m: (getattr(m, field), m.time), reverse=reverse)
    return [{"match_key": m.match_key, "metric": field, "value": round(float(getattr(m, field)), 2)} for m in rows[:n]]


def team_signals(
    *,
    strength: Strength,
    here: list[MatchPoints],
    typical: dict[str, float],
    subscores: dict[str, float | None],
    trend: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pros: list[dict[str, Any]] = []
    cons: list[dict[str, Any]] = []
    if strength.matches_here + strength.matches_before < MIN_MATCHES:
        played = strength.matches_here + strength.matches_before
        if played == 0 and strength.basis == "last_season":
            cons.append(_signal(
                "last_season_only", "No matches this season yet",
                "Starts from how strong the team was last season, in a different game.",
                metric="matches", value=0, strength=strength, impact="neutral", category="confidence",
            ))
        elif played == 0 and strength.basis == "rookie":
            cons.append(_signal(
                "rookie_start", "Rookie team",
                "No history yet: starts a little below the season's typical robot.",
                metric="matches", value=0, strength=strength, impact="neutral", category="confidence",
            ))
        else:
            cons.append(_signal(
                "limited_evidence", "Few matches to judge from",
                "Fewer than three official matches this season, so the rating leans on where it started.",
                metric="matches", value=played, strength=strength, impact="neutral", category="confidence",
            ))
        return pros, cons

    teleop_typical = max(1e-6, typical.get("teleop", 0.0))
    if strength.teleop >= STRONG_RATIO * teleop_typical:
        pros.append(_signal(
            "high_teleop", "Scores a lot of fuel",
            f"About {strength.teleop:.0f} teleop points a match; the typical robot scores {teleop_typical:.0f}.",
            metric="teleop_points", value=strength.teleop, strength=strength, impact="positive", category="strength",
            evidence=_best(here, "teleop"),
        ))
    elif strength.teleop <= WEAK_RATIO * teleop_typical:
        cons.append(_signal(
            "low_teleop", "Scores little fuel",
            f"About {strength.teleop:.0f} teleop points a match; the typical robot scores {teleop_typical:.0f}.",
            metric="teleop_points", value=strength.teleop, strength=strength, impact="negative", category="risk",
            evidence=_best(here, "teleop", reverse=False),
        ))

    auto_typical = max(1e-6, typical.get("auto", 0.0))
    if strength.auto >= STRONG_RATIO * auto_typical:
        pros.append(_signal(
            "strong_auto", "Strong autonomous",
            f"About {strength.auto:.0f} points in auto; the typical robot scores {auto_typical:.0f}.",
            metric="auto_points", value=strength.auto, strength=strength, impact="positive", category="strength",
            evidence=_best(here, "auto"),
        ))
    elif strength.auto <= WEAK_RATIO * auto_typical:
        cons.append(_signal(
            "weak_auto", "Autonomous needs work",
            f"About {strength.auto:.0f} points in auto; the typical robot scores {auto_typical:.0f}.",
            metric="auto_points", value=strength.auto, strength=strength, impact="negative", category="risk",
        ))

    climbs = [m for m in here if m.climb]
    if strength.climb >= 5.0:
        where = f"Climbed in {len(climbs)} of {len(here)} matches here" if here else "Climbs at earlier events"
        pros.append(_signal(
            "climbs", "Can climb",
            f"{where}, about {strength.climb:.0f} points a match.",
            metric="climb_points", value=strength.climb, strength=strength, impact="positive", category="strength",
            evidence=_best(here, "climb"),
        ))

    consistency = subscores.get("consistency")
    if consistency is not None and strength.matches_here >= 5:
        if consistency >= 70:
            pros.append(_signal(
                "consistent", "Steady from match to match",
                "Its points rarely stray far from its average.",
                metric="consistency", value=consistency, strength=strength, impact="positive", category="strength",
            ))
        elif consistency < 35:
            cons.append(_signal(
                "inconsistent", "Up and down from match to match",
                "Some matches score far below its average.",
                metric="consistency", value=consistency, strength=strength, impact="negative", category="risk",
                evidence=_best(here, "teleop", reverse=False),
            ))

    if trend is not None:
        base = max(10.0, abs(trend["earlier_points"]))
        change = trend["delta_points"] / base
        if change >= 0.2:
            pros.append(_signal(
                "trending_up", "Scoring is trending up",
                f"Last four matches: {trend['recent_points']:.0f} points a match, up from {trend['earlier_points']:.0f}.",
                metric="trend_points", value=trend["delta_points"], strength=strength, impact="positive", category="trend",
            ))
        elif change <= -0.2:
            cons.append(_signal(
                "trending_down", "Scoring is trending down",
                f"Last four matches: {trend['recent_points']:.0f} points a match, down from {trend['earlier_points']:.0f}.",
                metric="trend_points", value=trend["delta_points"], strength=strength, impact="negative", category="trend",
            ))

    if strength.matches_here == 0 and strength.matches_before:
        cons.append(_signal(
            "earlier_events_only", "Not played here yet",
            f"Based on {strength.matches_before} matches at earlier events this season.",
            metric="matches_before", value=strength.matches_before, strength=strength, impact="neutral", category="confidence",
        ))
    return pros, cons
