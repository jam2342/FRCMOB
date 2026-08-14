from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

@dataclass
class _StageContext:
    # Immutable context shared by every stage (set once during setup).

    match_key: str
    match: Any  # models.Match
    run: Any  # models.AnalysisRun
    db: Any  # Session
    calibration: Any  # models.FieldCalibration
    calibration_id: int
    match_teams: list[Any]  # list[models.MatchTeam]
    youtube_video: Any  # models.MatchVideo
    event_profile: Any  # models.EventProfile | None
    analysis_version: str
    params_hash: str
    perimeter_type: str | None
    perimeter_source: str | None
    normalized_sampling_override: dict[str, Any]
    mark_stage: Callable[[str], None]

@dataclass
class _VideoAndPhasesResult:
    # Output of _stage_resolve_video_and_phases.

    prepared_video_source: Any
    video_source: Any
    video_meta: dict[str, Any]
    video_source_mode: str
    stream_fallback_reason: str | None
    streamed_video: bool
    resolved_video_source: str
    raw_video_duration_sec: float
    duration_sec: float
    interlude_trim_window: dict[str, Any]
    phase_windows: dict[str, Any]
    sampling_plan: dict[str, Any]
    season_year: int | None
    match_payload: dict[str, Any] | None
    hub_activity_payload: dict[str, Any] | None
    cycle_fallback_duration_sec: float
    sparse_cycle_fallbacks_by_team: dict[str, Any]
    config: Any  # GameConfig

@dataclass
class _TrackingResult:
    # Output of _stage_run_tracking.

    sample_frames: list[Path]
    observations: list[dict]
    track_summaries: dict[int, dict]
    tracking_backend: str
    tracking_meta: dict[str, Any]
    track_team_map: dict[int, str]
    assignment_meta: dict[str, Any]
    ocr_meta: dict[str, Any]
    sample_interval_sec: float
    sample_max_frames: int
    artifact_max_frames: int
    interlude_trim_fallback_used: bool
    interlude_trim_window: dict[str, Any]
    min_cal_x: float
    max_cal_x: float
    min_cal_y: float
    max_cal_y: float
    config_length: float
    config_width: float

@dataclass
class _PersistTracksResult:
    # Output of _stage_persist_tracks.

    artifact_frames: list[Path]
    source_video_meta: dict[str, Any]

@dataclass
class _TeamMetricsResult:
    # Output of _stage_compute_team_metrics.

    team_coverage_scores: list[float]
    team_detection_counts: list[int]
    team_missing_reason_counts: Counter

@dataclass
class _QualityResult:
    # Output of _stage_evaluate_quality.

    calibration_quality_score: float
    tracking_quality_score: float
    identity_quality_score: float
    overall_quality_score: float
    coverage_avg: float
    coverage_min: float | None
    detections_min: int | None
    reject_low_quality: bool
    rejection_reasons: list[str]
    quality_details: dict[str, Any]
