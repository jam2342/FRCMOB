# Data-loading helpers for ``recompute_event_ratings``.
#
# Queries all DB rows that the rating pipeline needs and returns them in
# a single ``EventRatingData`` dataclass.  No business logic lives here.

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.db import models
from app.services.season_config import CURRENT_SEASON_YEAR
from app.services.quality_gate import evaluate_summary_quality_gate, quality_gate_config_payload
from app.services.ratings.constants import RELEVANT_MATCH_EVENT_TYPES
from app.services.ratings.helpers import _event_from_official_source
from app.services.ratings.statbotics import _load_statbotics_epa_by_team

class EventRatingData:
    # Container for all pre-loaded data needed by the rating pipeline.

    __slots__ = (
        "team_rows",
        "team_keys",
        "epa_context_by_team",
        "match_time_by_key",
        "match_comp_level_by_key",
        "match_number_by_key",
        "max_qm_match_number",
        "finding_rows",
        "raw_finding_rows",
        "raw_findings_count_by_team",
        "excluded_findings_count_by_team",
        "findings_by_team",
        "video_findings_count_by_team",
        "accepted_run_ids",
        "throughputs_by_team",
        "quality_by_run",
        "stats_by_team",
        "event_strength_by_team",
        "season_strength_by_team",
        "capabilities_by_team",
        "speeds_by_team",
        "events_by_team_match",
        "alliance_by_match",
        "opponent_keys_by_team_match",
        "gate_config",
        "season_for_strength",
    )

    def __init__(self) -> None:
        self.team_rows: list[Any] = []
        self.team_keys: list[str] = []
        self.epa_context_by_team: dict[str, Any] = {}
        self.match_time_by_key: dict[str, int | None] = {}
        self.match_comp_level_by_key: dict[str, str] = {}
        self.match_number_by_key: dict[str, int | None] = {}
        self.max_qm_match_number: int | None = None
        self.finding_rows: list[models.TeamMatchFinding] = []
        self.raw_finding_rows: list[models.TeamMatchFinding] = []
        self.raw_findings_count_by_team: dict[str, int] = defaultdict(int)
        self.excluded_findings_count_by_team: dict[str, int] = defaultdict(int)
        self.findings_by_team: dict[str, list[models.TeamMatchFinding]] = defaultdict(list)
        self.video_findings_count_by_team: dict[str, int] = defaultdict(int)
        self.accepted_run_ids: set[int] = set()
        self.throughputs_by_team: dict[str, list[models.TeamMatchThroughput]] = defaultdict(list)
        self.quality_by_run: dict[int, models.AnalysisQuality] = {}
        self.stats_by_team: dict[str, models.EventTeamStat] = {}
        self.event_strength_by_team: dict[str, models.TeamEventThroughputStrength] = {}
        self.season_strength_by_team: dict[str, models.TeamSeasonStrength] = {}
        self.capabilities_by_team: dict[str, models.TeamStaticCapability] = {}
        self.speeds_by_team: dict[str, list[float]] = defaultdict(list)
        self.events_by_team_match: dict[str, dict[str, list[models.MatchEvent]]] = defaultdict(lambda: defaultdict(list))
        self.alliance_by_match: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"red": [], "blue": []})
        self.opponent_keys_by_team_match: dict[str, dict[str, list[str]]] = defaultdict(dict)
        self.gate_config: dict[str, Any] = {}
        self.season_for_strength: int = CURRENT_SEASON_YEAR

def load_event_rating_data(
    db: Session,
    event_key: str,
    manual_context: dict[str, Any],
) -> EventRatingData:
    # Query every table the rating pipeline needs and return an ``EventRatingData``.

    data = EventRatingData()

    # ── Teams ─────────────────────────────────────────────────────────
    data.team_rows = (
        db.query(models.EventTeam, models.Team)
        .outerjoin(models.Team, models.Team.team_key == models.EventTeam.team_key)
        .filter(models.EventTeam.event_key == event_key)
        .all()
    )
    data.team_keys = [event_team.team_key for event_team, _ in data.team_rows]
    if not data.team_keys:
        return data

    data.epa_context_by_team = _load_statbotics_epa_by_team(data.team_rows)

    # ── Matches ───────────────────────────────────────────────────────
    match_rows = (
        db.query(
            models.Match.match_key,
            models.Match.time,
            models.Match.comp_level,
            models.Match.match_number,
        )
        .filter(models.Match.event_key == event_key)
        .all()
    )
    data.match_time_by_key = {
        match_key: (int(match_time) if isinstance(match_time, (int, float)) else None)
        for match_key, match_time, _, _ in match_rows
    }
    data.match_comp_level_by_key = {
        match_key: str(comp_level or "").strip().lower()
        for match_key, _, comp_level, _ in match_rows
    }
    data.match_number_by_key = {
        match_key: int(match_number) if isinstance(match_number, int) else None
        for match_key, _, _, match_number in match_rows
    }
    qual_match_numbers = [
        int(match_number)
        for _, _, comp_level, match_number in match_rows
        if str(comp_level or "").strip().lower() == "qm" and isinstance(match_number, int) and match_number > 0
    ]
    data.max_qm_match_number = max(qual_match_numbers) if qual_match_numbers else None

    # ── Findings ──────────────────────────────────────────────────────
    data.raw_finding_rows = (
        db.query(models.TeamMatchFinding)
        .filter(
            models.TeamMatchFinding.event_key == event_key,
            models.TeamMatchFinding.team_key.in_(data.team_keys),
        )
        .all()
    )
    data.gate_config = quality_gate_config_payload()
    for finding in data.raw_finding_rows:
        data.raw_findings_count_by_team[finding.team_key] += 1
        if not data.gate_config["enabled"]:
            data.finding_rows.append(finding)
            continue
        is_valid, _, _, _ = evaluate_summary_quality_gate(finding.summary)
        if is_valid:
            data.finding_rows.append(finding)
        else:
            data.excluded_findings_count_by_team[finding.team_key] += 1
    for finding in data.finding_rows:
        data.findings_by_team[finding.team_key].append(finding)
        source_token = str(finding.source or "").strip().lower()
        if source_token.startswith("video"):
            data.video_findings_count_by_team[finding.team_key] += 1
    data.accepted_run_ids = {finding.analysis_run_id for finding in data.finding_rows}

    # ── Throughput ────────────────────────────────────────────────────
    throughput_rows_query = (
        db.query(models.TeamMatchThroughput)
        .filter(
            models.TeamMatchThroughput.event_key == event_key,
            models.TeamMatchThroughput.team_key.in_(data.team_keys),
        )
    )
    if data.gate_config["enabled"] and data.accepted_run_ids:
        throughput_rows_query = throughput_rows_query.filter(models.TeamMatchThroughput.analysis_run_id.in_(data.accepted_run_ids))
    throughput_rows = throughput_rows_query.all() if (not data.gate_config["enabled"] or data.accepted_run_ids) else []
    for throughput in throughput_rows:
        data.throughputs_by_team[throughput.team_key].append(throughput)

    # ── Quality ───────────────────────────────────────────────────────
    quality_rows_query = db.query(models.AnalysisQuality).filter(models.AnalysisQuality.event_key == event_key)
    if data.gate_config["enabled"] and data.accepted_run_ids:
        quality_rows_query = quality_rows_query.filter(models.AnalysisQuality.run_id.in_(data.accepted_run_ids))
    quality_rows = quality_rows_query.all() if (not data.gate_config["enabled"] or data.accepted_run_ids) else []
    data.quality_by_run = {row.run_id: row for row in quality_rows}

    # ── Stats ─────────────────────────────────────────────────────────
    stats_rows = (
        db.query(models.EventTeamStat)
        .filter(
            models.EventTeamStat.event_key == event_key,
            models.EventTeamStat.team_key.in_(data.team_keys),
        )
        .all()
    )
    data.stats_by_team = {row.team_key: row for row in stats_rows}

    # ── Event Strength ────────────────────────────────────────────────
    event_strength_rows = (
        db.query(models.TeamEventThroughputStrength)
        .filter(
            models.TeamEventThroughputStrength.event_key == event_key,
            models.TeamEventThroughputStrength.team_key.in_(data.team_keys),
        )
        .all()
    )
    data.event_strength_by_team = {row.team_key: row for row in event_strength_rows}

    # ── Season Strength ───────────────────────────────────────────────
    event_year_match = re.match(r"^(\d{4})", str(event_key or ""))
    data.season_for_strength = (
        int(event_year_match.group(1))
        if event_year_match
        else int(manual_context.get("season_year") or CURRENT_SEASON_YEAR)
    )
    season_strength_rows = (
        db.query(models.TeamSeasonStrength)
        .filter(
            models.TeamSeasonStrength.season == data.season_for_strength,
            models.TeamSeasonStrength.team_key.in_(data.team_keys),
        )
        .all()
    )
    data.season_strength_by_team = {row.team_key: row for row in season_strength_rows}

    # ── Capabilities ──────────────────────────────────────────────────
    capability_rows = (
        db.query(models.TeamStaticCapability)
        .filter(models.TeamStaticCapability.team_key.in_(data.team_keys))
        .all()
    )
    data.capabilities_by_team = {row.team_key: row for row in capability_rows}

    # ── Robot Tracks ──────────────────────────────────────────────────
    track_rows_query = (
        db.query(models.RobotTrack)
        .filter(
            models.RobotTrack.event_key == event_key,
            models.RobotTrack.team_key.in_(data.team_keys),
            models.RobotTrack.speed_mps.isnot(None),
        )
    )
    if data.gate_config["enabled"] and data.accepted_run_ids:
        track_rows_query = track_rows_query.filter(models.RobotTrack.analysis_run_id.in_(data.accepted_run_ids))
    track_rows = track_rows_query.all() if (not data.gate_config["enabled"] or data.accepted_run_ids) else []
    for row in track_rows:
        if row.team_key:
            data.speeds_by_team[row.team_key].append(float(row.speed_mps or 0.0))

    # ── Match Events ──────────────────────────────────────────────────
    match_event_rows_query = (
        db.query(models.MatchEvent)
        .filter(
            models.MatchEvent.event_key == event_key,
            models.MatchEvent.team_key.in_(data.team_keys),
            models.MatchEvent.event_type.in_(RELEVANT_MATCH_EVENT_TYPES),
        )
    )
    match_event_rows = match_event_rows_query.all()
    if data.gate_config["enabled"]:
        if data.accepted_run_ids:
            match_event_rows = [
                event
                for event in match_event_rows
                if event.analysis_run_id in data.accepted_run_ids or _event_from_official_source(event)
            ]
        else:
            match_event_rows = [event for event in match_event_rows if _event_from_official_source(event)]
    for event in match_event_rows:
        if not event.team_key:
            continue
        data.events_by_team_match[event.team_key][event.match_key].append(event)

    # ── Alliance / Opponent mapping ───────────────────────────────────
    match_team_rows = (
        db.query(models.MatchTeam)
        .filter(
            models.MatchTeam.event_key == event_key,
            models.MatchTeam.team_key.in_(data.team_keys),
        )
        .all()
    )
    for row in match_team_rows:
        alliance = str(row.alliance or "").strip().lower()
        if alliance not in {"red", "blue"}:
            continue
        data.alliance_by_match[row.match_key][alliance].append(row.team_key)

    for match_key, alliance_payload in data.alliance_by_match.items():
        red = [team_key for team_key in alliance_payload.get("red", []) if team_key in data.team_keys]
        blue = [team_key for team_key in alliance_payload.get("blue", []) if team_key in data.team_keys]
        for team_key in red:
            data.opponent_keys_by_team_match[team_key][match_key] = list(blue)
        for team_key in blue:
            data.opponent_keys_by_team_match[team_key][match_key] = list(red)

    return data
