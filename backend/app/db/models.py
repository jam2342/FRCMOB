from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

def _utc_now() -> datetime:
    # Timezone-aware UTC timestamp for column defaults.
    return datetime.now(timezone.utc)

class Event(Base):
    __tablename__ = "events"

    event_key: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    year: Mapped[int] = mapped_column(Integer, index=True)

    profile: Mapped["EventProfile | None"] = relationship(back_populates="event", uselist=False, lazy="select")
    matches: Mapped[list["Match"]] = relationship(back_populates="event", lazy="select")
    scouting_rooms: Mapped[list["ScoutingRoom"]] = relationship(back_populates="event", lazy="select")

class EventProfile(Base):
    __tablename__ = "event_profiles"

    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), primary_key=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    state_prov: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    event: Mapped["Event"] = relationship(back_populates="profile", lazy="select")

class Team(Base):
    __tablename__ = "teams"

    team_key: Mapped[str] = mapped_column(String, primary_key=True)
    team_number: Mapped[int] = mapped_column(Integer, index=True)
    nickname: Mapped[str | None] = mapped_column(String, nullable=True)

    profile: Mapped["TeamProfile | None"] = relationship(back_populates="team", uselist=False, lazy="select")

class TeamProfile(Base):
    __tablename__ = "team_profiles"

    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    state_prov: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    team: Mapped["Team"] = relationship(back_populates="profile", lazy="select")

class EventTeam(Base):
    __tablename__ = "event_teams"

    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), primary_key=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)

    __table_args__ = (
        Index("ix_event_teams_team_key", "team_key"),
    )

class EventTeamStat(Base):
    __tablename__ = "event_team_stats"

    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), primary_key=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    opr: Mapped[float | None] = mapped_column(Float, nullable=True)
    dpr: Mapped[float | None] = mapped_column(Float, nullable=True)
    ccwm: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String, default="tba_opr")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
    )

class TeamStaticCapability(Base):
    __tablename__ = "team_static_capabilities"

    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    ball_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
    )

class EventTeamRating(Base):
    __tablename__ = "event_team_ratings"

    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), primary_key=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    rating_0_100: Mapped[float] = mapped_column(Float, default=50.0)
    confidence_0_1: Mapped[float] = mapped_column(Float, default=0.0)
    robot_level_0_100: Mapped[float] = mapped_column(Float, default=50.0)
    driver_skill_0_100: Mapped[float] = mapped_column(Float, default=50.0)
    results_anchor: Mapped[float] = mapped_column(Float, default=50.0)
    throughput: Mapped[float] = mapped_column(Float, default=50.0)
    shift_productivity: Mapped[float] = mapped_column(Float, default=50.0)
    capacity_utilization: Mapped[float] = mapped_column(Float, default=50.0)
    endgame: Mapped[float] = mapped_column(Float, default=50.0)
    consistency: Mapped[float] = mapped_column(Float, default=50.0)
    pros_json: Mapped[list[dict]] = mapped_column(JSON, default=lambda: [])
    cons_json: Mapped[list[dict]] = mapped_column(JSON, default=lambda: [])
    evidence_json: Mapped[list[dict]] = mapped_column(JSON, default=lambda: [])
    details_json: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    model_version: Mapped[str] = mapped_column(String, default="rating_v1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
    )

    __table_args__ = (
        Index("ix_event_team_rating_event_team", "event_key", "team_key"),
        Index("ix_event_team_rating_team_key", "team_key"),
        Index("ix_event_team_rating_team_updated", "team_key", "updated_at"),
    )

class RatingSnapshot(Base):
    # Append-only time series of EventTeamRating values, one row per team per
    # recompute. Powers the live "momentum" UI (sparkline, trend arrow, deltas).
    # Unlike EventTeamRating (one row per team, overwritten each recompute) this
    # is never updated in place — rows accumulate so we can chart history.
    __tablename__ = "rating_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), index=True)
    rating_0_100: Mapped[float] = mapped_column(Float)
    confidence_0_1: Mapped[float] = mapped_column(Float, default=0.0)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    model_version: Mapped[str] = mapped_column(String, default="rating_v1")
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        index=True,
    )

    __table_args__ = (
        Index("ix_rating_snapshot_event_team_captured", "event_key", "team_key", "captured_at"),
        Index("ix_rating_snapshot_event_captured", "event_key", "captured_at"),
    )

class Match(Base):
    __tablename__ = "matches"

    match_key: Mapped[str] = mapped_column(String, primary_key=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    comp_level: Mapped[str] = mapped_column(String)
    set_number: Mapped[int] = mapped_column(Integer)
    match_number: Mapped[int] = mapped_column(Integer)
    time: Mapped[int | None] = mapped_column(Integer, nullable=True)

    event: Mapped["Event"] = relationship(back_populates="matches", lazy="select")
    match_teams: Mapped[list["MatchTeam"]] = relationship(back_populates="match", lazy="select")
    videos: Mapped[list["MatchVideo"]] = relationship(back_populates="match", lazy="select")
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(back_populates="match", lazy="select")
    calibration: Mapped["FieldCalibration | None"] = relationship(back_populates="match", uselist=False, lazy="select")

    __table_args__ = (
        Index("ix_matches_event_time", "event_key", "time"),
        Index("ix_matches_event_comp_order", "event_key", "comp_level", "set_number", "match_number"),
    )

class MatchTeam(Base):
    __tablename__ = "match_teams"

    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), primary_key=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    alliance: Mapped[str] = mapped_column(String, index=True)
    station: Mapped[str | None] = mapped_column(String, nullable=True)

    match: Mapped["Match"] = relationship(back_populates="match_teams", lazy="select")

    __table_args__ = (
        Index("ix_match_teams_event_team", "event_key", "team_key"),
        Index("ix_match_teams_team_event", "team_key", "event_key"),
    )

class MatchVideo(Base):
    __tablename__ = "match_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    video_type: Mapped[str] = mapped_column(String)
    video_key: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)

    match: Mapped["Match"] = relationship(back_populates="videos", lazy="select")

class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    version: Mapped[str] = mapped_column(String, default="v0")
    status: Mapped[str] = mapped_column(String, default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    match: Mapped["Match"] = relationship(back_populates="analysis_runs", lazy="select")
    context: Mapped["AnalysisRunContext | None"] = relationship(back_populates="run", uselist=False, lazy="select")
    findings: Mapped[list["TeamMatchFinding"]] = relationship(back_populates="analysis_run", lazy="select")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="analysis_run", lazy="select")
    quality: Mapped["AnalysisQuality | None"] = relationship(back_populates="run", uselist=False, lazy="select")
    auto_scout_drafts: Mapped[list["AutoScoutDraft"]] = relationship(back_populates="analysis_run", lazy="select")

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'requeued')",
            name="ck_analysis_runs_status",
        ),
    )

class AnalysisRunContext(Base):
    __tablename__ = "analysis_run_contexts"

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), primary_key=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    analysis_version: Mapped[str] = mapped_column(String, default="video_v3_tracks", index=True)
    params_hash: Mapped[str] = mapped_column(String, default="", index=True)
    calibration_id: Mapped[int | None] = mapped_column(ForeignKey("field_calibrations.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)

    run: Mapped["AnalysisRun"] = relationship(back_populates="context", lazy="select")

class TeamMatchFinding(Base):
    __tablename__ = "team_match_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), index=True)
    alliance: Mapped[str] = mapped_column(String, index=True)
    station: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, default="video_v1")
    fuel_scoring_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    cycle_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    auto_contribution: Mapped[float | None] = mapped_column(Float, nullable=True)
    climb_success_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    defensive_engagement_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    analysis_run: Mapped["AnalysisRun"] = relationship(back_populates="findings", lazy="select")
    throughput: Mapped["TeamMatchThroughput | None"] = relationship(back_populates="finding", uselist=False, lazy="select")

    __table_args__ = (
        Index("ix_team_match_finding_event_team", "event_key", "team_key"),
        Index("ix_team_match_finding_match", "match_key", "team_key"),
        Index("ix_team_match_finding_team_event_id", "team_key", "event_key", "id"),
        Index("ix_team_match_finding_team_run", "team_key", "analysis_run_id"),
    )

class TeamMatchThroughput(Base):
    __tablename__ = "team_match_throughputs"

    finding_id: Mapped[int] = mapped_column(ForeignKey("team_match_findings.id"), primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), index=True)
    balls_shot_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shooting_time_total_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    bps_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    balls_shot_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shooting_time_active_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    active_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    metric_coverage: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    source: Mapped[str] = mapped_column(String, default="video_v3_tracks")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    finding: Mapped["TeamMatchFinding"] = relationship(back_populates="throughput", lazy="select")

class RobotTrack(Base):
    __tablename__ = "robot_tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    team_key: Mapped[str | None] = mapped_column(ForeignKey("teams.team_key"), index=True, nullable=True)
    track_id: Mapped[int] = mapped_column(Integer, index=True)
    frame_index: Mapped[int] = mapped_column(Integer, index=True)
    time_sec: Mapped[float] = mapped_column(Float, index=True)
    bbox_x1: Mapped[float] = mapped_column(Float)
    bbox_y1: Mapped[float] = mapped_column(Float)
    bbox_x2: Mapped[float] = mapped_column(Float)
    bbox_y2: Mapped[float] = mapped_column(Float)
    centroid_x: Mapped[float] = mapped_column(Float)
    centroid_y: Mapped[float] = mapped_column(Float)
    field_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    field_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String, default="cv_motion_v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)

    __table_args__ = (
        Index("ix_robot_track_event_team", "event_key", "team_key"),
        Index("ix_robot_track_match_time", "match_key", "time_sec"),
        Index("ix_robot_track_team_event_time", "team_key", "event_key", "time_sec", "id"),
        Index("ix_robot_track_team_run_time", "team_key", "analysis_run_id", "time_sec", "id"),
    )

class MatchEvent(Base):
    __tablename__ = "match_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    team_key: Mapped[str | None] = mapped_column(ForeignKey("teams.team_key"), index=True, nullable=True)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_sec: Mapped[float] = mapped_column(Float, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    field_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    field_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)

    __table_args__ = (
        Index("ix_match_events_team_event_time", "team_key", "event_key", "time_sec", "id"),
        Index("ix_match_events_team_run_time", "team_key", "analysis_run_id", "time_sec", "id"),
    )

class AnalysisQuality(Base):
    __tablename__ = "analysis_qualities"

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), primary_key=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    calibration_quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    tracking_quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    identity_quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    overall_quality_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    run: Mapped["AnalysisRun"] = relationship(back_populates="quality", lazy="select")

class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    meta: Mapped[dict] = mapped_column(JSON, default=lambda: {})

    analysis_run: Mapped["AnalysisRun"] = relationship(back_populates="artifacts", lazy="select")

class FieldCalibration(Base):
    __tablename__ = "field_calibrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True, unique=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    frame_time_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_width: Mapped[int] = mapped_column(Integer)
    image_height: Mapped[int] = mapped_column(Integer)
    image_points: Mapped[list[dict]] = mapped_column(JSON)
    field_points: Mapped[list[dict]] = mapped_column(JSON)
    homography: Mapped[list[list[float]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
    )

    match: Mapped["Match"] = relationship(back_populates="calibration", lazy="select")

class MatchPhaseWindow(Base):
    __tablename__ = "match_phase_windows"

    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), primary_key=True)
    phase_key: Mapped[str] = mapped_column(String, primary_key=True)
    model_version: Mapped[str] = mapped_column(String, primary_key=True, default="phase_v1")
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    duration_sec: Mapped[float] = mapped_column(Float)
    start_unix: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_unix: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String, default="game_config")
    confidence_0_1: Mapped[float] = mapped_column(Float, default=0.8)
    params_hash: Mapped[str] = mapped_column(String, default="", index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)

class TeamSeasonStrength(Base):
    __tablename__ = "team_season_strengths"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    model_version: Mapped[str] = mapped_column(String, primary_key=True, default="synergy_v1")
    strength_active_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_0_1: Mapped[float] = mapped_column(Float, default=0.0)
    matches_used: Mapped[int] = mapped_column(Integer, default=0)
    avg_quality: Mapped[float] = mapped_column(Float, default=0.0)
    coverage_factor: Mapped[float] = mapped_column(Float, default=0.0)
    params_hash: Mapped[str] = mapped_column(String, default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

class TeamPairSynergyPrior(Base):
    __tablename__ = "team_pair_synergy_priors"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_key_a: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    team_key_b: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    model_version: Mapped[str] = mapped_column(String, primary_key=True, default="synergy_v1")
    synergy_points_prior: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_prior: Mapped[float] = mapped_column(Float, default=0.0)
    n_matches_together: Mapped[int] = mapped_column(Integer, default=0)
    avg_quality: Mapped[float] = mapped_column(Float, default=0.0)
    residual_mean_raw: Mapped[float] = mapped_column(Float, default=0.0)
    params_hash: Mapped[str] = mapped_column(String, default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

class TeamEventThroughputStrength(Base):
    __tablename__ = "team_event_throughput_strengths"

    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), primary_key=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    model_version: Mapped[str] = mapped_column(String, primary_key=True, default="synergy_v1")
    season: Mapped[int] = mapped_column(Integer, index=True)
    strength_active_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_0_1: Mapped[float] = mapped_column(Float, default=0.0)
    matches_used: Mapped[int] = mapped_column(Integer, default=0)
    avg_quality: Mapped[float] = mapped_column(Float, default=0.0)
    coverage_factor: Mapped[float] = mapped_column(Float, default=0.0)
    params_hash: Mapped[str] = mapped_column(String, default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

class TeamPairSynergyEvent(Base):
    __tablename__ = "team_pair_synergy_events"

    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), primary_key=True)
    team_key_a: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    team_key_b: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), primary_key=True)
    model_version: Mapped[str] = mapped_column(String, primary_key=True, default="synergy_v1")
    season: Mapped[int] = mapped_column(Integer, index=True)
    synergy_points_event: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_event: Mapped[float] = mapped_column(Float, default=0.0)
    n_matches_together: Mapped[int] = mapped_column(Integer, default=0)
    avg_quality: Mapped[float] = mapped_column(Float, default=0.0)
    residual_mean_raw: Mapped[float] = mapped_column(Float, default=0.0)
    params_hash: Mapped[str] = mapped_column(String, default="", index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

class MatchSynergyProjection(Base):
    __tablename__ = "match_synergy_projections"

    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), primary_key=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), primary_key=True)
    alliance_color: Mapped[str] = mapped_column(String, primary_key=True)
    model_version: Mapped[str] = mapped_column(String, primary_key=True, default="synergy_v1")
    season: Mapped[int] = mapped_column(Integer, index=True)
    scheduled_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_throughput: Mapped[float | None] = mapped_column(Float, nullable=True)
    alliance_synergy_points: Mapped[float] = mapped_column(Float, default=0.0)
    projected_throughput: Mapped[float | None] = mapped_column(Float, nullable=True)
    alliance_synergy_score_0_100: Mapped[float] = mapped_column(Float, default=50.0)
    confidence_0_1: Mapped[float] = mapped_column(Float, default=0.0)
    source_label: Mapped[str] = mapped_column(String, default="projection")
    pair_breakdown: Mapped[list[dict]] = mapped_column(JSON, default=lambda: [])
    params_hash: Mapped[str] = mapped_column(String, default="", index=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

class IntelSnapshot(Base):
    __tablename__ = "intel_snapshots"

    snapshot_key: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str] = mapped_column(String, index=True)
    team_key: Mapped[str | None] = mapped_column(ForeignKey("teams.team_key"), nullable=True, index=True)
    event_key: Mapped[str | None] = mapped_column(ForeignKey("events.event_key"), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    source_version: Mapped[str] = mapped_column(String, default="intel_snapshot_v1")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    __table_args__ = (
        Index("ix_intel_snapshots_scope_event_team", "scope", "event_key", "team_key"),
    )

class MLFeatureSnapshot(Base):
    __tablename__ = "ml_feature_snapshots"

    snapshot_key: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str] = mapped_column(String, index=True)
    event_key: Mapped[str | None] = mapped_column(ForeignKey("events.event_key"), nullable=True, index=True)
    match_key: Mapped[str | None] = mapped_column(ForeignKey("matches.match_key"), nullable=True, index=True)
    team_key: Mapped[str | None] = mapped_column(ForeignKey("teams.team_key"), nullable=True, index=True)
    alliance_color: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    feature_vector: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    target: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    split_tag: Mapped[str] = mapped_column(String, default="train", index=True)
    source_version: Mapped[str] = mapped_column(String, default="shadow_features_v1", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    __table_args__ = (
        Index("ix_ml_feature_snapshots_scope_event_created", "scope", "event_key", "created_at"),
        Index("ix_ml_feature_snapshots_scope_split_created", "scope", "split_tag", "created_at"),
    )

class MLModelRegistry(Base):
    __tablename__ = "ml_model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_key: Mapped[str] = mapped_column(String, index=True)
    model_version: Mapped[str] = mapped_column(String)
    framework: Mapped[str] = mapped_column(String, default="torch")
    artifact_path: Mapped[str] = mapped_column(String)
    input_schema: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    metrics: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    params: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)

    __table_args__ = (
        UniqueConstraint("model_key", "model_version", name="uq_ml_model_registry_key_version"),
        Index("ix_ml_model_registry_key_active", "model_key", "is_active"),
    )

class MLShadowPrediction(Base):
    __tablename__ = "ml_shadow_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_key: Mapped[str] = mapped_column(String, index=True)
    model_version: Mapped[str] = mapped_column(String, index=True)
    event_key: Mapped[str | None] = mapped_column(ForeignKey("events.event_key"), nullable=True, index=True)
    match_key: Mapped[str | None] = mapped_column(ForeignKey("matches.match_key"), nullable=True, index=True)
    team_key: Mapped[str | None] = mapped_column(ForeignKey("teams.team_key"), nullable=True, index=True)
    target_key: Mapped[str] = mapped_column(String, index=True)
    prediction_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    prediction_json: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    feature_snapshot_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)

    __table_args__ = (
        Index(
            "ix_ml_shadow_predictions_event_model_target",
            "event_key",
            "model_key",
            "model_version",
            "target_key",
        ),
    )

class ScoutingRoom(Base):
    __tablename__ = "scouting_rooms"

    room_key: Mapped[str] = mapped_column(String, primary_key=True)
    event_key: Mapped[str | None] = mapped_column(
        ForeignKey("events.event_key"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    event: Mapped["Event | None"] = relationship(back_populates="scouting_rooms", lazy="select")
    entries: Mapped[list["ScoutingRoomEntry"]] = relationship(back_populates="room", lazy="select")
    assignments: Mapped[list["ScoutingRoomAssignment"]] = relationship(back_populates="room", lazy="select")
    leaders: Mapped[list["ScoutingRoomLeader"]] = relationship(back_populates="room", lazy="select")

class ScoutingRoomLeader(Base):
    __tablename__ = "scouting_room_leaders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_key: Mapped[str] = mapped_column(ForeignKey("scouting_rooms.room_key"), index=True)
    scout_profile: Mapped[str] = mapped_column(String, index=True)
    scout_profile_norm: Mapped[str] = mapped_column(String, index=True)
    added_by_scout_profile: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    added_by_scout_profile_norm: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    room: Mapped["ScoutingRoom"] = relationship(back_populates="leaders", lazy="select")

    __table_args__ = (
        UniqueConstraint("room_key", "scout_profile_norm", name="uq_scouting_room_leader_profile"),
        Index("ix_scouting_room_leaders_room_profile_norm", "room_key", "scout_profile_norm"),
    )

class ScoutingRoomEntry(Base):
    __tablename__ = "scouting_room_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_key: Mapped[str] = mapped_column(ForeignKey("scouting_rooms.room_key"), index=True)
    event_key: Mapped[str | None] = mapped_column(
        ForeignKey("events.event_key"), nullable=True, index=True
    )
    match_key: Mapped[str | None] = mapped_column(
        ForeignKey("matches.match_key"), nullable=True, index=True
    )
    team_key: Mapped[str | None] = mapped_column(
        ForeignKey("teams.team_key"), nullable=True, index=True
    )
    scout_profile: Mapped[str] = mapped_column(String, index=True)
    client_entry_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    total_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    driver_score_0_100: Mapped[float | None] = mapped_column(Float, nullable=True)
    manual_rating_0_100: Mapped[float | None] = mapped_column(Float, nullable=True)
    scouting_api_rating_0_100: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    room: Mapped["ScoutingRoom"] = relationship(back_populates="entries", lazy="select")

    __table_args__ = (
        UniqueConstraint("room_key", "client_entry_id", name="uq_scouting_room_entry_client"),
        Index("ix_scouting_room_entries_room_created", "room_key", "created_at"),
        Index("ix_scouting_room_entries_room_team", "room_key", "team_key"),
    )

class ScoutingRoomAssignment(Base):
    __tablename__ = "scouting_room_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_key: Mapped[str] = mapped_column(ForeignKey("scouting_rooms.room_key"), index=True)
    event_key: Mapped[str | None] = mapped_column(
        ForeignKey("events.event_key"), nullable=True, index=True
    )
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), index=True)
    assigned_scout_profile: Mapped[str] = mapped_column(String, index=True)
    assigned_scout_profile_norm: Mapped[str] = mapped_column(String, index=True)
    assigned_by_scout_profile: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    assigned_by_scout_profile_norm: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    room: Mapped["ScoutingRoom"] = relationship(back_populates="assignments", lazy="select")

    __table_args__ = (
        UniqueConstraint("room_key", "match_key", "team_key", name="uq_scouting_room_assignment_slot"),
        Index("ix_scouting_room_assignments_room_event", "room_key", "event_key"),
        Index(
            "ix_scouting_room_assignments_room_assigned_norm",
            "room_key",
            "assigned_scout_profile_norm",
        ),
    )

class AutoScoutDraft(Base):
    __tablename__ = "auto_scout_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(ForeignKey("events.event_key"), index=True)
    match_key: Mapped[str] = mapped_column(ForeignKey("matches.match_key"), index=True)
    team_key: Mapped[str] = mapped_column(ForeignKey("teams.team_key"), index=True)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    draft_payload: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    approved_payload: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    field_confidence: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    field_provenance: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    field_evidence_refs: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    field_overrides: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    coverage_summary: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    missing_reasons: Mapped[list] = mapped_column(JSON, default=lambda: [])
    analysis_version: Mapped[str] = mapped_column(String, default="video_v3_tracks", index=True)
    analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id"), nullable=True, index=True)
    mapper_version: Mapped[str] = mapped_column(String, default="unknown", index=True)
    draft_version: Mapped[int] = mapped_column(Integer, default=1)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    rejected_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    analysis_run: Mapped["AnalysisRun | None"] = relationship(back_populates="auto_scout_drafts", lazy="select")

    __table_args__ = (
        UniqueConstraint(
            "match_key",
            "team_key",
            "analysis_run_id",
            "mapper_version",
            name="uq_auto_scout_draft_team_run_mapper",
        ),
        Index("ix_auto_scout_drafts_event_status", "event_key", "status"),
        Index("ix_auto_scout_drafts_match_team_generated", "match_key", "team_key", "generated_at"),
        Index("ix_auto_scout_drafts_team_status", "team_key", "status"),
    )

class EventPicklist(Base):
    __tablename__ = "event_picklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Plain string (no FK) so a picklist can be drafted before event ingest completes.
    event_key: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String, default="Picklist")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    # Ordered list of slot documents:
    # {team_key, tier ("first"|"second"|"dnp"), dnp_reason, notes,
    #  status ("available"|"picked"|"declined"|"captain"), picked_by_alliance}
    slots: Mapped[list] = mapped_column(JSON, default=lambda: [])
    # Optimistic concurrency: writes must echo the version they loaded.
    version: Mapped[int] = mapped_column(Integer, default=1)
    live_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    __table_args__ = (
        Index("ix_event_picklists_event_archived", "event_key", "archived"),
    )

class PitScoutingEntry(Base):
    __tablename__ = "pit_scouting_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Plain strings (no FK) so pit data can be captured before event ingest.
    event_key: Mapped[str] = mapped_column(String, index=True)
    team_key: Mapped[str] = mapped_column(String, index=True)
    scout_profile: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Free-form answers from the schema-driven pit form.
    payload: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    # List of media-relative photo paths ("/media/pit_photos/...").
    photos: Mapped[list] = mapped_column(JSON, default=lambda: [])
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("event_key", "team_key", name="uq_pit_scouting_event_team"),
    )

class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Web Push endpoint URL is unique per browser subscription.
    endpoint: Mapped[str] = mapped_column(String, unique=True, index=True)
    # {p256dh, auth} encryption keys from PushSubscription.getKey().
    keys: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    event_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Favorite team keys to alert on (e.g. ["frc254"]).
    team_keys: Mapped[list] = mapped_column(JSON, default=lambda: [])
    # {match_lead_count, shift_alerts, scout_profile, room_key}
    prefs: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Map of alert dedupe keys -> ISO timestamp already notified.
    notified: Mapped[dict] = mapped_column(JSON, default=lambda: {})
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )

    __table_args__ = (
        Index("ix_push_subscriptions_event_enabled", "event_key", "enabled"),
    )
