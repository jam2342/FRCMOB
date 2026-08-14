# Pydantic response schemas for the busiest API endpoints.
#
# These provide automatic input validation, OpenAPI documentation,
# and a single source of truth for response shapes.

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── Shared building blocks ──────────────────────────────────────

class TeamRef(BaseModel):
    team_key: str
    team_number: int
    nickname: str | None = None

class StationTeamRef(TeamRef):
    station: str

class OkEnvelope(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    ok: bool = True

# ── GET /matches/event/{event_key}/schedule ─────────────────────

class ScheduleMatch(BaseModel):
    match_key: str
    display_name: str
    comp_level: str
    set_number: int
    match_number: int
    scheduled_time: int | None = None
    has_time: bool
    red_score: int | None = None
    blue_score: int | None = None
    winner_alliance: str | None = None
    is_completed: bool
    winning_score: int | None = None
    losing_score: int | None = None
    red: list[StationTeamRef]
    blue: list[StationTeamRef]

class ScheduleResponse(OkEnvelope):
    event_key: str
    event_name: str
    source: str
    first_last_modified: str | None = None
    published: bool
    times_published: bool
    count: int
    total_count: int | None = None
    limit: int | None = None
    offset: int = 0
    matches: list[ScheduleMatch]

# ── GET /scouting/event/{event_key}/ratings ─────────────────────

class RatingSubscores(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="allow")

    results_anchor: float | None = None
    throughput: float | None = None
    shift_productivity: float | None = None
    capacity_utilization: float | None = None
    endgame: float | None = None
    auto_contribution: float | None = None
    anti_defense: float | None = None
    manual_points_impact: float | None = None
    rp_contribution: float | None = None
    defense_presence: float | None = None
    consistency: float | None = None
    penalty_discipline: float | None = None

class RatingTrend(BaseModel):
    model_config = ConfigDict(extra="allow")

    latest_0_100: float | None = None
    previous_0_100: float | None = None
    delta: float | None = None
    direction: str | None = None  # "up" | "down" | "flat"
    snapshot_count: int = 0
    sparkline: list[float] = Field(default_factory=list)
    min_0_100: float | None = None
    max_0_100: float | None = None

class RatingRow(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), extra="allow")

    event_key: str
    team_key: str
    team_number: int | None = None
    nickname: str | None = None
    rating_0_100: float | None = None
    confidence_0_1: float | None = None
    robot_level_0_100: float | None = None
    driver_skill_0_100: float | None = None
    subscores: RatingSubscores = Field(default_factory=RatingSubscores)
    pros: list[Any] = Field(default_factory=list)
    cons: list[Any] = Field(default_factory=list)
    evidence: list[Any] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    model_version: str = ""
    updated_at: str | None = None
    rating_trend: RatingTrend | None = None

class RatingsResponse(OkEnvelope):
    event_key: str
    event_name: str
    model_version: str
    source: str
    count: int
    ratings: list[RatingRow]
    last_updated_at: str | None = None

# ── GET /teams/search ───────────────────────────────────────────

class TeamSearchResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    team_key: str
    team_number: int
    nickname: str | None = None
    analyzed_matches: int = 0
    source: str = "local"
    country: str | None = None
    state: str | None = None
    registration_year: int | None = None
    registered_events: list[dict[str, Any]] = Field(default_factory=list)
    registered_events_count: int = 0
    registered_events_source: str = ""

class TeamSearchResponse(OkEnvelope):
    query: str
    count: int
    teams: list[TeamSearchResult]

# ── GET /matches/event/{event_key}/team-live-form ───────────────

class RecentFormMatch(BaseModel):
    match_key: str
    match_display: str
    time: int | None = None
    result: str

class TeamLiveFormStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    team_key: str
    is_live: bool = False
    live_match_key: str | None = None
    live_match_display: str | None = None
    live_match_start_unix: int | None = None
    recent_form: list[str] = Field(default_factory=list)
    recent_form_matches: list[RecentFormMatch] = Field(default_factory=list)
    recent_form_winrate_0_1: float | None = None
    in_form_label: str = ""
    completed_matches_considered: int = 0

class TeamLiveFormResponse(OkEnvelope):
    event_key: str
    source: str = "tba"
    available: bool = False
    detail: str | None = None
    as_of_unix: int = 0
    form_window: int = 0
    live_window_sec: int = 0
    team_statuses: dict[str, TeamLiveFormStatus] = Field(default_factory=dict)

# ── GET /events/{event_key}/schedule-with-synergy ───────────────

class SynergyPairBreakdown(BaseModel):
    model_config = ConfigDict(extra="allow")

class AllianceSynergy(BaseModel):
    model_config = ConfigDict(extra="allow")

    available: bool = False
    alliance_synergy_score_0_100: float | None = None
    alliance_synergy_points: float = 0.0
    expected_throughput: float | None = None
    projected_throughput: float | None = None
    confidence_0_1: float = 0.0
    source_label: str = ""
    pair_breakdown: list[Any] = Field(default_factory=list)
    computed_at: str | None = None

class SynergyAlliance(BaseModel):
    teams: list[StationTeamRef]
    synergy: AllianceSynergy = Field(default_factory=AllianceSynergy)

class MatchOutcomePrediction(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())

    available: bool = False
    source_label: str = ""
    model_key: str = ""
    model_version: str | None = None
    red_win_prob: float | None = None
    blue_win_prob: float | None = None
    red_win_prob_deterministic: float | None = None
    red_win_prob_ml: float | None = None
    prediction_blend: float | None = None
    favored_alliance: str | None = None
    edge_confidence_0_1: float | None = None

class SynergyMatch(BaseModel):
    match_key: str
    comp_level: str
    set_number: int
    match_number: int
    scheduled_time: int | None = None
    red: SynergyAlliance
    blue: SynergyAlliance
    prediction: MatchOutcomePrediction = Field(default_factory=MatchOutcomePrediction)

class ScheduleWithSynergyResponse(OkEnvelope):
    event_key: str
    event_name: str
    model_version: str
    projection_count: int
    ml_prediction_model_version: str | None = None
    ml_prediction_count: int = 0
    ml_blended_prediction_count: int = 0
    deterministic_prediction_count: int = 0
    prediction_blend: float = 0.0
    count: int
    precompute: dict[str, Any] | None = None
    matches: list[SynergyMatch]

# ── GET /scouting/event/{event_key}/teams/history ───────────────

class SeasonScope(BaseModel):
    model_config = ConfigDict(extra="allow")

    season_year: int
    active_season_year: int
    source: str = ""
    uses_active_season: bool = False

class TeamHistory(BaseModel):
    model_config = ConfigDict(extra="allow")

    team_key: str
    team_number: int
    nickname: str | None = None
    region: str | None = None
    state_prov: str | None = None
    country: str | None = None
    analysis_coverage: dict[str, Any] = Field(default_factory=dict)
    history_count: int = 0
    history_count_quality_gate_accepted: int = 0
    history_count_before_quality_gate: int = 0
    history_excluded_by_quality_gate: int = 0
    data_freshness: dict[str, Any] = Field(default_factory=dict)
    averages: dict[str, Any] = Field(default_factory=dict)
    previous_games: list[dict[str, Any]] = Field(default_factory=list)

class TeamsHistoryResponse(OkEnvelope):
    event_key: str
    event_name: str | None = None
    source: str
    refresh_error: str | None = None
    season_scope: SeasonScope
    quality_gate: dict[str, Any] = Field(default_factory=dict)
    teams_count: int
    teams: list[TeamHistory]
