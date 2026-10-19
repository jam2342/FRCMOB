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
    # Scouts join a workspace once per season; removal revokes access immediately.
    team_workspace_access_ttl_sec: int = 15552000
    # Workspaces are anonymous (no accounts yet), so their cost is bounded instead:
    # site-wide creations per hour, pit photo bytes per workspace and event, and
    # empty workspaces nobody has opened in a while are removed.
    workspace_create_max_per_hour: int = 30
    workspace_signup_challenge_required: bool = False
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""
    turnstile_allowed_hostnames: str = ""
    pit_photo_quota_bytes_per_workspace_event: int = 500 * 1024 * 1024
    pit_photo_quota_bytes_per_workspace: int = 1024 * 1024 * 1024
    pit_photo_quota_bytes_total: int = 5 * 1024 * 1024 * 1024
    pit_photo_minimum_free_bytes: int = 512 * 1024 * 1024
    pit_photo_max_files_per_workspace: int = 2000
    pit_photo_max_files_total: int = 10000
    push_subscriptions_max_total: int = 10000
    push_subscriptions_max_new_per_hour: int = 300
    push_subscription_retention_days: int = 180
    workspace_empty_cleanup_enabled: bool = True
    workspace_empty_cleanup_after_days: int = 30
    log_level: str = "INFO"
    log_format: str = "text"  # "text" for human-readable, "json" for structured
    cors_allow_origins: str = "http://localhost:5173,http://localhost:3000"
    tba_auth_key: str = ""
    first_frc_api_base_url: str = "https://frc-api.firstinspires.org/v3.0"
    first_frc_api_username: str = ""
    first_frc_api_auth_key: str = ""
    tba_cache_max_entries: int = 2048
    # Caching headers on GET responses (app/api/edge_cache.py). The CDN rule goes in
    # this header; a host that only reads another one is a config change here.
    edge_cache_enabled: bool = True
    edge_cache_header: str = "CDN-Cache-Control"
    game_config_path: str = "game_config/season_template.json"
    push_notifications_enabled: bool = True
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "https://scouting-app-iryg.vercel.app"
    push_match_alert_interval_sec: int = 60
    push_match_lead_minutes_default: int = 15
    push_ttl_sec: int = 1800
    push_subscription_max_failures: int = 8
    fuel_scoring_rate_max_per_min: float = 16.0
    events_ingest_run_post_compute: bool = True
    automation_regional_enabled: bool = True
    automation_regional_interval_minutes: int = 45
    automation_regional_max_events: int = 300
    automation_regional_max_teams: int = 1000
    automation_regional_include_all_events: bool = False
    automation_regional_include_out_of_region_events: bool = True
    automation_regional_include_ended_today: bool = False
    automation_regional_season_fallback_enabled: bool = False
    automation_regional_halfday_scheduler_enabled: bool = True
    automation_regional_halfday_interval_hours: int = 12
    automation_regional_halfday_season: int = 0
    automation_regional_halfday_include_out_of_region_events: bool = False
    automation_regional_halfday_include_ended_today: bool = True
    automation_regional_run_post_compute: bool = True
    # A finished event's results still settle (score fixes, late breakdowns) for a few
    # days; after that it only needs an occasional re-check, capped per tick.
    automation_regional_settle_days: int = 3
    automation_regional_max_rechecks_per_tick: int = 20
    automation_regional_countries: str = "USA,Canada"
    scouting_data_outdated_days: int = 45
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
    ops_metrics_sample_days: int = 14
    ops_smoke_check_enabled: bool = True
    ops_smoke_check_interval_minutes: int = 30
    ops_alert_regional_automation_stale_hours: int = 14
    ops_alert_automation_lock_spike_threshold: int = 3
    request_slow_log_threshold_ms: float = 900.0
    request_timing_header_enabled: bool = False
    # Largest legitimate body is a 6 MB pit photo as base64 (~8.4 MB). Caddy has the same cap.
    max_request_body_bytes: int = 10 * 1024 * 1024
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
    on_device_sync_max_runs_per_match: int = 20  # per team (or per caller) and match
    # A verified four-corner calibration + stable pose + strong identity must all
    # be present before ordinary acceptance. Legacy payloads always score zero.
    on_device_sync_min_quality_score: float = 0.8
    matches_live_results_cache_ttl_sec: int = 5
    # FRC Events has a result ~10 s after the field posts it; TBA takes 1-3 min. While a
    # match waits on TBA, its result comes from FRC Events (services/events/first_live_results.py).
    first_live_results_enabled: bool = True
    # Events in play are re-ingested this often, so ratings and the forecast follow each
    # match (services/events/live_ingest.py). Off: only the 12 h job ingests, after events end.
    live_event_ingest_enabled: bool = True
    live_event_ingest_interval_minutes: int = 3
    live_event_ingest_max_events: int = 80

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
