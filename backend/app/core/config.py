import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_config_logger = logging.getLogger("app.core.config")
_SECRET_PLACEHOLDER_VALUES = {
    "replace_me",
    "changeme",
    "your_key_here",
    "your_api_key_here",
    "none",
    "null",
}
LOCAL_DATABASE_URL_FALLBACK = "postgresql+psycopg://localhost:5432/frc"

ROOT_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"

# Same env_file resolution order pydantic-settings uses (see model_config).
_ENV_FILE_PATHS: tuple[Path, ...] = (Path(".env"), ROOT_ENV_PATH)

# Matches TEXAS as a delimited token: AUTOMATION_TEXAS_*, LIVE_ANALYSIS_TEXAS_*,
# OPS_ALERT_TEXAS_*, or a bare TEXAS. Does NOT match e.g. TEXASTYLE.
_LEGACY_TEXAS_KEY_RE = re.compile(r"(?:^|_)TEXAS(?:_|$)", re.IGNORECASE)


def _declared_env_file_keys() -> dict[str, str]:
    # Return {KEY: source_path} for every uncommented KEY=... line in the
    # configured env files. Commented lines (``# ...``) are intentionally
    # skipped so migration notes documenting old names don't trip the guard.
    found: dict[str, str] = {}
    for path in _ENV_FILE_PATHS:
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            if key:
                found.setdefault(key, str(path))
    return found


def assert_no_legacy_texas_settings() -> None:
    # Hard-fail startup if any legacy ``*_TEXAS_*`` config key is present.
    #
    # The Texas->Regional rename (commit 55c55d9) renamed every such field to
    # ``*_REGIONAL_*``. Because ``model_config`` uses ``extra="ignore"``, a
    # stray TEXAS key is silently dropped and the code falls back to defaults —
    # the exact drift that disabled the analysis/ML automation pipeline. Fail
    # loudly so it cannot silently recur.
    offenders: list[str] = []
    for name in os.environ:
        if _LEGACY_TEXAS_KEY_RE.search(name):
            offenders.append(f"{name} (process environment)")
    for name, source in _declared_env_file_keys().items():
        if _LEGACY_TEXAS_KEY_RE.search(name):
            offenders.append(f"{name} ({source})")
    if not offenders:
        return
    listing = "\n  - ".join(sorted(set(offenders)))
    raise RuntimeError(
        "Legacy *_TEXAS_* configuration detected. These keys were renamed to "
        "*_REGIONAL_* and are now silently ignored (extra=\"ignore\"), which "
        "disables the analysis/ML automation pipeline. Rename each to its "
        "*_REGIONAL_* equivalent:\n  - " + listing
    )


class DatabaseConfig(BaseModel):
    url: str
    pool_size: int
    max_overflow: int
    pool_recycle_sec: int
    pool_timeout_sec: int


class SecurityConfig(BaseModel):
    admin_api_key: str
    admin_api_header: str
    admin_session_token_secret: str
    admin_session_ttl_sec: int
    scouting_room_access_ttl_sec: int
    public_readonly_mode: bool
    enforce_admin_auth_for_writes: bool
    strict_startup_env_validation: bool


class SchedulerConfig(BaseModel):
    distributed_lock_enabled: bool
    distributed_lock_prefix: str
    distributed_lock_ttl_sec: int
    runtime_metrics_prefix: str
    runtime_metrics_ttl_sec: int


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = ""
    db_pool_size: int = 25
    db_max_overflow: int = 35
    db_pool_recycle_sec: int = 3600
    db_pool_timeout_sec: int = 30
    redis_url: str = "redis://localhost:6379/0"
    scouting_rooms_redis_pubsub_enabled: bool = True
    scouting_rooms_channel_prefix: str = "scouting:rooms:"
    scouting_rooms_presence_prefix: str = "scouting:rooms:presence:"
    scouting_rooms_presence_ttl_sec: int = 30
    scouting_rooms_presence_touch_interval_sec: int = 10
    scouting_rooms_ws_send_timeout_sec: float = 1.5
    scouting_rooms_cleanup_enabled: bool = True
    scouting_rooms_cleanup_interval_minutes: int = 180
    scouting_rooms_inactive_delete_days: int = 2
    scouting_rooms_cleanup_max_rooms_per_run: int = 100
    public_readonly_mode: bool = False
    enforce_admin_auth_for_writes: bool = True
    strict_startup_env_validation: bool = False
    admin_api_key: str = ""
    admin_api_header: str = "X-Admin-Key"
    admin_session_token_secret: str = ""
    admin_session_ttl_sec: int = 7200
    scouting_room_access_ttl_sec: int = 43200
    log_level: str = "INFO"
    log_format: str = "text"  # "text" for human-readable, "json" for structured
    cors_allow_origins: str = "http://localhost:5173,http://localhost:3000"
    tba_auth_key: str = ""
    first_frc_api_base_url: str = "https://frc-api.firstinspires.org/v3.0"
    first_frc_api_username: str = ""
    first_frc_api_auth_key: str = ""
    statbotics_base_url: str = "https://api.statbotics.io/v3"
    tba_cache_max_entries: int = 2048
    statbotics_cache_ttl_sec: int = 300
    statbotics_stale_cache_ttl_sec: int = 900
    statbotics_stale_serve_on_error: bool = True
    statbotics_circuit_breaker_enabled: bool = True
    statbotics_circuit_failure_threshold: int = 4
    statbotics_circuit_cooldown_sec: int = 90
    game_config_path: str = "game_config/season_template.json"
    push_notifications_enabled: bool = True
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@frcmob.app"
    push_match_alert_interval_sec: int = 60
    push_match_lead_minutes_default: int = 15
    push_ttl_sec: int = 1800
    push_subscription_max_failures: int = 8
    media_cleanup_enabled: bool = True
    media_retention_days_videos: int = 2
    media_retention_days_analysis_frames: int = 2
    media_cleanup_min_free_gb: float = 12.0
    media_cleanup_max_total_gb: float = 30.0
    media_cleanup_min_interval_sec: int = 300
    media_cleanup_protect_recent_minutes: int = 20
    video_extraction_prefer_streaming: bool = True
    video_extraction_allow_download_fallback: bool = True
    video_extraction_cleanup_fallback_download: bool = True
    video_extraction_youtube_timeout_sec: int = 60
    video_extraction_ffmpeg_timeout_sec: int = 180
    video_extraction_live_clip_timeout_buffer_sec: int = 45
    video_extraction_stream_resolve_max_attempts: int = 3
    video_extraction_stream_resolve_backoff_sec: float = 1.0
    storage_cleanup_enabled: bool = True
    storage_cleanup_post_analysis: bool = True
    storage_cleanup_delete_videos: bool = True
    storage_cleanup_delete_analysis_frames: bool = True
    storage_cleanup_delete_sampled_frames: bool = False
    storage_cleanup_age_days_auto: int = 7
    analysis_job_timeout_sec: int = 3600
    analysis_job_result_ttl_sec: int = 86400
    analysis_job_failure_ttl_sec: int = 604800
    analysis_job_retry_max: int = 2
    analysis_job_retry_intervals_sec: str = "45,180"
    analysis_queue_max_pending_jobs: int = 1200
    analysis_queue_max_schedule_per_call: int = 180
    fuel_scoring_rate_max_per_min: float = 16.0
    events_ingest_run_post_compute: bool = True
    events_ingest_backfill_match_videos: bool = True
    events_ingest_backfill_match_videos_max_calls: int = 24
    automation_regional_enabled: bool = True
    automation_regional_interval_minutes: int = 45
    automation_regional_max_events: int = 300
    automation_regional_max_teams: int = 1000
    automation_regional_max_matches_per_event: int = 42
    automation_regional_max_new_jobs_per_tick: int = 220
    automation_regional_include_all_events: bool = False
    automation_regional_include_out_of_region_events: bool = True
    automation_regional_include_ended_today: bool = False
    automation_regional_season_fallback_enabled: bool = False
    automation_regional_halfday_scheduler_enabled: bool = True
    automation_regional_halfday_interval_hours: int = 12
    automation_regional_halfday_season: int = 0
    automation_regional_halfday_include_out_of_region_events: bool = False
    automation_regional_halfday_include_ended_today: bool = True
    automation_regional_halfday_all_matches_in_region_events: bool = True
    automation_regional_auto_calibrate_missing: bool = True
    automation_regional_auto_calibration_overwrite_existing: bool = False
    automation_regional_auto_calibration_refresh_video: bool = False
    automation_regional_auto_calibration_sample_count: int = 18
    automation_regional_auto_calibration_min_inliers: int = 4
    automation_regional_auto_calibration_ransac_reproj_threshold_px: float = 3.0
    automation_regional_auto_calibration_focus_time_sec: float = 0.0
    automation_regional_auto_calibration_allow_synthetic_fallback: bool = True
    automation_regional_auto_calibration_synthetic_margin_ratio: float = 0.08
    automation_regional_clone_event_calibration: bool = True
    automation_regional_require_video: bool = True
    automation_regional_require_calibration: bool = False
    automation_regional_run_post_compute: bool = True
    automation_regional_countries: str = "USA,Canada"
    video_tracking_mode: str = "auto"
    # FRC-tuned detector is the PRIMARY model; generic COCO yolo11n.pt is only a
    # last-resort fallback (it barely detects FRC robots -> empty findings).
    # v2 (2026-05-26): mAP@0.5=0.7172 on locked Einstein holdout, beats v1 (0.6991).
    video_tracking_yolo_model: str = "media/models/frc_robot_detector_v2.pt"
    # Object-storage URL to fetch the detector on startup if the local file is
    # absent (empty -> no download attempt; relies on baked/mounted file).
    video_tracking_yolo_model_url: str = ""
    # Pin downloaded production weights so a partial or substituted artifact
    # can never be used for scouting.
    video_tracking_yolo_model_sha256: str = ""
    video_tracking_yolo_model_max_bytes: int = 1_073_741_824
    # Production must not silently fall back to a generic COCO detector: it can
    # appear healthy while producing unusable FRC robot tracks.
    video_tracking_require_primary_model_in_production: bool = True
    video_tracking_require_primary_model_sha256_in_production: bool = True
    # Identifies the generic non-FRC fallback so the pipeline can flag a run as
    # degraded when it has to use it.
    video_tracking_generic_model_names: str = "yolo11n.pt,yolo11s.pt,yolov8n.pt,yolov8s.pt"
    video_tracking_yolo_device: str = ""
    video_tracking_yolo_conf: float = 0.2
    video_tracking_yolo_iou: float = 0.45
    video_tracking_yolo_max_det: int = 20
    video_tracking_yolo_imgsz: int = 960
    video_tracking_yolo_model_fallbacks: str = "yolo11n.pt"
    video_tracking_yolo_classes: str = ""
    video_tracking_yolo_generic_allowed_classes: str = "0,1,2,3,5,7"
    video_tracking_bytetrack_config: str = "config/bytetrack_frc.yaml"
    video_tracking_yolo_auto_robot_classes: bool = True
    video_tracking_yolo_min_box_area_ratio: float = 0.00008
    video_tracking_yolo_max_box_area_ratio: float = 0.09
    video_tracking_yolo_min_aspect_ratio: float = 0.32
    video_tracking_yolo_max_aspect_ratio: float = 3.2
    video_tracking_yolo_max_detections_per_frame: int = 14
    video_tracking_yolo_min_track_observations: int = 3
    video_tracking_yolo_min_track_avg_confidence: float = 0.24
    video_tracking_sampling_mode: str = "adaptive"
    video_tracking_sample_interval_sec: float = 2.0
    video_tracking_dense_interval_sec: float = 0.8
    video_tracking_adaptive_min_interval_sec: float = 0.75
    video_tracking_adaptive_max_interval_sec: float = 2.0
    video_tracking_max_sample_frames: int = 120
    video_tracking_interlude_trim_enabled: bool = True
    video_tracking_interlude_trim_long_video_min_sec: float = 260.0
    video_tracking_interlude_trim_pre_roll_sec: float = 8.0
    video_tracking_interlude_trim_post_roll_sec: float = 12.0
    video_tracking_interlude_trim_allow_calibration_anchor: bool = True
    video_tracking_interlude_trim_calibration_anchor_fraction: float = 0.5
    video_tracking_interlude_trim_calibration_anchor_max_reuse: int = 3
    video_tracking_interlude_trim_weak_signal_fallback_enabled: bool = True
    video_tracking_interlude_trim_weak_signal_min_observations_per_team: int = 8
    video_tracking_interlude_trim_weak_signal_min_tracks: int = 4
    video_tracking_interlude_trim_anchor_tolerance_sec: float = 1.0
    video_tracking_max_artifact_sample_frames: int = 30
    video_tracking_dense_retry_enabled: bool = True
    video_tracking_dense_retry_interval_sec: float = 0.7
    video_tracking_dense_retry_max_frames: int = 220
    video_tracking_dense_retry_min_observations_per_team: int = 18
    video_tracking_dense_retry_min_tracks: int = 4
    video_tracking_main_view_min_x_ratio: float = 0.02
    video_tracking_main_view_max_x_ratio: float = 0.98
    video_tracking_main_view_min_y_ratio: float = 0.06
    video_tracking_main_view_max_y_ratio: float = 0.78
    video_tracking_main_view_min_box_overlap_ratio: float = 0.6
    video_tracking_field_bounds_prune_enabled: bool = True
    video_tracking_field_bounds_prune_margin_m: float = 0.8
    video_tracking_field_bounds_drop_unprojectable: bool = False
    video_tracking_cut_detection_enabled: bool = True
    video_tracking_cut_detection_hist_threshold: float = 0.34
    video_tracking_cut_detection_frame_diff_threshold: float = 0.19
    video_tracking_cut_detection_min_gap_sec: float = 1.0
    video_tracking_cut_track_id_segment_stride: int = 1000000
    video_tracking_enable_bumper_ocr: bool = True
    video_tracking_bumper_ocr_language: str = "eng"
    video_tracking_bumper_ocr_max_samples_per_track: int = 12
    video_tracking_bumper_ocr_min_text_conf: float = 0.35
    video_tracking_bumper_ocr_expand_ratio: float = 0.25
    video_tracking_bumper_ocr_bottom_band_ratio: float = 0.55
    video_event_classifier_enabled: bool = True
    video_event_classifier_model_path: str = "media/models/frc_event_transition_v1.json"
    video_event_classifier_conf_threshold: float = 0.58
    video_event_classifier_margin_threshold: float = 0.06
    video_event_classifier_prefer_model: bool = True
    live_analysis_enabled: bool = True
    live_analysis_default_interval_sec: int = 35
    live_analysis_default_clip_duration_sec: int = 26
    live_analysis_live_window_sec: int = 170
    live_analysis_analysis_cooldown_sec: int = 75
    live_analysis_max_matches_per_tick: int = 2
    live_analysis_analysis_version: str = "video_v3_live_window"
    live_analysis_post_compute: bool = True
    live_analysis_synergy_quality_threshold: float = 0.35
    live_analysis_regional_auto_enabled: bool = True
    live_analysis_regional_auto_interval_sec: int = 120
    live_analysis_regional_auto_max_events: int = 16
    live_analysis_regional_auto_require_schedule: bool = False
    analysis_quality_gate_enabled: bool = True
    analysis_quality_min_coverage_score: float = 0.2
    analysis_quality_min_detections: int = 8
    analysis_quality_reject_low_runs: bool = True
    analysis_quality_min_overall_score: float = 0.35
    scouting_data_outdated_days: int = 45
    rating_recent_match_window: int = 20
    rating_recent_priority_window: int = 10
    rating_recent_priority_weight: float = 2.0
    rating_recent_base_weight: float = 1.0
    rating_penalty_impact_net_points: float = 0.30
    rating_penalty_impact_subscores: float = 0.35
    rating_penalty_impact_driver: float = 0.35
    rating_penalty_impact_base_rating: float = 0.30
    rating_statbotics_epa_enabled: bool = True
    rating_results_anchor_epa_weight: float = 0.12
    rating_performance_epa_blend: float = 0.08
    rating_performance_auto_weight: float = 0.22
    rating_base_auto_weight: float = 0.09
    rating_performance_antidefense_weight: float = 0.08
    rating_base_antidefense_weight: float = 0.06
    rating_antidefense_stage_early_quals_multiplier: float = 0.62
    rating_antidefense_stage_late_quals_multiplier: float = 1.00
    rating_antidefense_stage_elims_multiplier: float = 1.24
    rating_antidefense_stage_support_matches: int = 6
    rating_trend_throughput_delta: float = 0.05
    rating_trend_reliability_delta: float = 0.04
    rating_trend_cycle_delta: float = 0.05
    rating_trend_penalty_delta: float = 0.10
    synergy_discipline_risk_multiplier: float = 0.30
    synergy_epa_pair_weight: float = 0.20
    synergy_pair_prior_shrink_k: float = 4.0
    synergy_pair_event_shrink_k: float = 3.0
    synergy_pair_event_blend_k: float = 3.0
    team_intel_cache_ttl_sec: int = 120
    event_intel_cache_ttl_sec: int = 90
    intel_snapshot_enabled: bool = True
    intel_snapshot_refresh_enabled: bool = True
    intel_snapshot_refresh_interval_minutes: int = 2
    intel_snapshot_max_events_per_run: int = 5
    intel_snapshot_min_events_per_run: int = 1
    intel_snapshot_max_teams_per_event: int = 0
    intel_snapshot_backfill_missing_fields_only: bool = True
    intel_snapshot_target_runtime_ratio: float = 0.8
    fuel_rate_calibration_enabled: bool = True
    fuel_rate_calibration_ttl_sec: int = 900
    fuel_rate_calibration_min_samples: int = 20
    fuel_rate_calibration_max_rows: int = 5000
    climb_official_backfill_enabled: bool = True
    climb_official_backfill_interval_minutes: int = 180
    climb_official_backfill_max_events_per_run: int = 6
    climb_integrity_audit_enabled: bool = True
    climb_integrity_audit_interval_minutes: int = 360
    climb_integrity_audit_lookback_days: int = 14
    climb_integrity_audit_sample_limit: int = 30000
    climb_integrity_audit_diff_threshold: float = 0.45
    ops_metrics_sample_days: int = 14
    ops_smoke_check_enabled: bool = True
    ops_smoke_check_interval_minutes: int = 30
    ops_alert_queue_pressure_threshold: float = 0.9
    ops_alert_queue_stuck_minutes: int = 25
    ops_alert_regional_automation_stale_hours: int = 14
    ops_alert_automation_lock_spike_threshold: int = 3
    ops_alert_automation_missing_calibration_threshold: int = 20
    ops_alert_automation_blocked_ratio_threshold: float = 0.45
    ops_youtube_slo_lookback_runs: int = 200
    ops_alert_youtube_stream_fallback_ratio_threshold: float = 0.10
    ops_alert_youtube_hard_failure_ratio_threshold: float = 0.02
    ops_alert_interlude_trim_weak_signal_ratio_threshold: float = 0.15
    ops_alert_interlude_trim_weak_signal_min_samples: int = 20
    analysis_low_quality_reprocess_enabled: bool = True
    analysis_low_quality_reprocess_interval_minutes: int = 180
    analysis_low_quality_reprocess_lookback_hours: int = 72
    analysis_low_quality_reprocess_max_matches_per_run: int = 4
    analysis_low_quality_reprocess_cooldown_hours: int = 24
    analysis_low_quality_reprocess_reason_tokens: str = (
        "detections_below_threshold,average_coverage_below_threshold,overall_quality_below_threshold"
    )
    analysis_low_quality_reprocess_sample_interval_sec: float = 0.75
    analysis_low_quality_reprocess_max_frames: int = 240
    analysis_low_quality_reprocess_max_artifact_frames: int = 40
    analysis_low_quality_reprocess_queue_pressure_ceiling: float = 0.75
    request_slow_log_threshold_ms: float = 900.0
    request_timing_header_enabled: bool = False
    db_slow_query_logging_enabled: bool = True
    db_slow_query_threshold_ms: float = 80.0
    db_slow_query_max_sql_chars: int = 220
    response_compression_enabled: bool = True
    response_compression_min_size_bytes: int = 1200
    response_compression_level: int = 5
    scheduler_distributed_lock_enabled: bool = True
    scheduler_distributed_lock_prefix: str = "scheduler:lock:"
    scheduler_distributed_lock_ttl_sec: int = 900
    scheduler_runtime_metrics_prefix: str = "scheduler:runtime:"
    scheduler_runtime_metrics_ttl_sec: int = 604800
    events_search_remote_fallback_enabled: bool = True
    events_search_remote_year_span: int = 3
    events_search_cache_ttl_sec: int = 90
    ml_shadow_enabled: bool = False
    ml_shadow_rollout_ratio: float = 0.0
    ml_shadow_model_dir: str = "media/models/shadow"
    ml_shadow_team_strength_model_version: str = ""
    ml_shadow_match_outcome_model_version: str = ""
    ml_shadow_feature_source_version: str = "shadow_features_v1"
    # Training and promotion are deliberately opt-in. Event ingestion must not
    # silently retrain and activate a model from unreviewed live data.
    ml_shadow_auto_train_on_event_breakdown: bool = False
    ml_shadow_auto_train_limit_events: int = 40
    ml_shadow_auto_train_activate: bool = False
    ml_shadow_auto_train_recompute_ratings: bool = True
    ml_shadow_auto_train_current_season_only: bool = True
    ml_auto_scout_feature_source_version: str = "auto_scout_field_features_v1"
    ml_auto_scout_training_export_enabled: bool = True
    ml_auto_scout_training_export_interval_hours: int = 24
    ml_auto_scout_training_export_replace_existing: bool = False
    ml_auto_scout_training_export_max_drafts: int = 4000
    # Auto-generate drafts for all assigned teams when a match analysis completes.
    auto_scout_generate_after_analysis_enabled: bool = True
    # Scheduler-backed catch-up for matches analyzed before the hook shipped or when the hook fails.
    auto_scout_backfill_enabled: bool = True
    auto_scout_backfill_interval_minutes: int = 30
    auto_scout_backfill_max_runs: int = 20
    auto_scout_backfill_max_drafts: int = 120
    auto_scout_backfill_lookback_hours: int = 168
    # On-device PWA session sync (offline scout uploads). This ingest endpoint is
    # scout-facing, so it is bounded to keep a hostile/buggy client from exhausting
    # memory/DB, and gated behind a signed token (admin or room access) by default.
    on_device_sync_require_signed_token: bool = True
    on_device_sync_max_teams: int = 12  # 6 robots + slack for mislabeled keys
    on_device_sync_max_points_per_team: int = 6000  # ~160s match well above any real fps
    on_device_sync_max_total_points: int = 24000
    on_device_sync_max_runs_per_match: int = 50  # cap distinct on-device runs per match
    ml_match_outcome_blend: float = 0.0  # 0..1 — blend ML prob with deterministic baseline
    ml_team_strength_blend: float = 0.0  # 0..1 — reserved for Phase 3 rating blend
    ml_synergy_blend: float = 0.0  # 0..1 — blend ML pair synergy with deterministic shrinkage
    ml_role_blend: float = 0.0  # 0..1 — blend ML role signals with deterministic classifier
    matches_live_results_cache_ttl_sec: int = 5
    freshness_sla_stale_hours: int = 48
    freshness_recovery_enabled: bool = True
    freshness_recovery_interval_minutes: int = 30
    freshness_recovery_max_events_per_run: int = 5
    freshness_recovery_max_target_teams_per_event: int = 30
    freshness_recovery_force_analysis: bool = False
    freshness_recovery_require_video: bool = True
    freshness_recovery_require_calibration: bool = False
    freshness_recovery_run_post_compute: bool = True

    @field_validator(
        "tba_auth_key",
        "first_frc_api_auth_key",
        "admin_api_key",
        "admin_session_token_secret",
        mode="before",
    )
    @classmethod
    def _normalize_secret_values(cls, value: object, info: ValidationInfo) -> str:
        if value is None:
            return ""
        normalized = str(value).strip()
        if not normalized:
            return ""
        if normalized.lower() in _SECRET_PLACEHOLDER_VALUES:
            _config_logger.warning(
                "Secret %s is set to a placeholder value (%r); treating as unset.",
                info.field_name,
                normalized,
            )
            return ""
        return normalized

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            # No credentials in the fallback default; local dev typically uses
            # trust auth or a .env file. Startup validation will warn if unset.
            return LOCAL_DATABASE_URL_FALLBACK
        # Ensure the psycopg (v3) dialect is used; plain 'postgresql://' maps
        # to the legacy psycopg2 driver which is not installed.
        if normalized.startswith("postgresql://"):
            normalized = "postgresql+psycopg://" + normalized[len("postgresql://"):]
        return normalized

    @field_validator("first_frc_api_username", mode="before")
    @classmethod
    def _normalize_username_values(cls, value: object) -> str:
        if value is None:
            return ""
        normalized = str(value).strip()
        if normalized.lower() in {"", "replace_me", "changeme", "none", "null"}:
            return ""
        return normalized

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> str:
        normalized = str(value or "INFO").strip().upper()
        return normalized or "INFO"

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalize_app_env(cls, value: object) -> str:
        normalized = str(value or "development").strip().lower()
        if normalized in {"dev", "local"}:
            return "development"
        if normalized in {"prod"}:
            return "production"
        if normalized in {"stage"}:
            return "staging"
        if normalized in {"test"}:
            return "testing"
        return normalized or "development"

    @field_validator("admin_api_header", mode="before")
    @classmethod
    def _normalize_admin_api_header(cls, value: object) -> str:
        normalized = str(value or "X-Admin-Key").strip()
        return normalized or "X-Admin-Key"

    @property
    def is_production_like(self) -> bool:
        env = str(getattr(self, "app_env", "") or "").strip().lower()
        return env not in {"", "development", "testing"}

    @property
    def database_url_is_local_fallback(self) -> bool:
        return str(self.database_url or "").strip() == LOCAL_DATABASE_URL_FALLBACK

    @property
    def db(self) -> DatabaseConfig:
        return DatabaseConfig(
            url=self.database_url,
            pool_size=int(self.db_pool_size),
            max_overflow=int(self.db_max_overflow),
            pool_recycle_sec=int(self.db_pool_recycle_sec),
            pool_timeout_sec=int(self.db_pool_timeout_sec),
        )

    @property
    def security(self) -> SecurityConfig:
        return SecurityConfig(
            admin_api_key=self.admin_api_key,
            admin_api_header=self.admin_api_header,
            admin_session_token_secret=self.admin_session_token_secret,
            admin_session_ttl_sec=int(self.admin_session_ttl_sec),
            scouting_room_access_ttl_sec=int(self.scouting_room_access_ttl_sec),
            public_readonly_mode=bool(self.public_readonly_mode),
            enforce_admin_auth_for_writes=bool(self.enforce_admin_auth_for_writes),
            strict_startup_env_validation=bool(self.strict_startup_env_validation),
        )

    @property
    def scheduler(self) -> SchedulerConfig:
        return SchedulerConfig(
            distributed_lock_enabled=bool(self.scheduler_distributed_lock_enabled),
            distributed_lock_prefix=self.scheduler_distributed_lock_prefix,
            distributed_lock_ttl_sec=int(self.scheduler_distributed_lock_ttl_sec),
            runtime_metrics_prefix=self.scheduler_runtime_metrics_prefix,
            runtime_metrics_ttl_sec=int(self.scheduler_runtime_metrics_ttl_sec),
        )

    model_config = SettingsConfigDict(env_file=(".env", str(ROOT_ENV_PATH)), extra="ignore")


settings = Settings()
