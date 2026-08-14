# baseline schema
#
# Revision ID: 20260218_0001
# Revises:
# Create Date: 2026-02-18 09:00:00.000000
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260218_0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # ── Core entities ────────────────────────────────────────────────────

    op.create_table(
        "events",
        sa.Column("event_key", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
    )
    op.create_index("ix_events_year", "events", ["year"])

    op.create_table(
        "event_profiles",
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), primary_key=True),
        sa.Column("city", sa.String, nullable=True),
        sa.Column("state_prov", sa.String, nullable=True),
        sa.Column("country", sa.String, nullable=True),
    )
    op.create_index("ix_event_profiles_state_prov", "event_profiles", ["state_prov"])
    op.create_index("ix_event_profiles_country", "event_profiles", ["country"])

    op.create_table(
        "teams",
        sa.Column("team_key", sa.String, primary_key=True),
        sa.Column("team_number", sa.Integer, nullable=False),
        sa.Column("nickname", sa.String, nullable=True),
    )
    op.create_index("ix_teams_team_number", "teams", ["team_number"])

    op.create_table(
        "team_profiles",
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("city", sa.String, nullable=True),
        sa.Column("state_prov", sa.String, nullable=True),
        sa.Column("country", sa.String, nullable=True),
    )
    op.create_index("ix_team_profiles_state_prov", "team_profiles", ["state_prov"])
    op.create_index("ix_team_profiles_country", "team_profiles", ["country"])

    op.create_table(
        "event_teams",
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), primary_key=True),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
    )
    op.create_index("ix_event_teams_team_key", "event_teams", ["team_key"])

    op.create_table(
        "event_team_stats",
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), primary_key=True),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("opr", sa.Float, nullable=True),
        sa.Column("dpr", sa.Float, nullable=True),
        sa.Column("ccwm", sa.Float, nullable=True),
        sa.Column("source", sa.String, nullable=False, server_default="tba_opr"),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "team_static_capabilities",
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("ball_capacity", sa.Integer, nullable=True),
        sa.Column("notes", sa.String, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "event_team_ratings",
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), primary_key=True),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("rating_0_100", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("confidence_0_1", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("robot_level_0_100", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("driver_skill_0_100", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("results_anchor", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("throughput", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("shift_productivity", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("capacity_utilization", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("endgame", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("consistency", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("pros_json", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("cons_json", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("evidence_json", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("details_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("model_version", sa.String, nullable=False, server_default="rating_v1"),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_event_team_rating_event_team", "event_team_ratings", ["event_key", "team_key"])
    op.create_index("ix_event_team_rating_team_key", "event_team_ratings", ["team_key"])
    op.create_index("ix_event_team_rating_team_updated", "event_team_ratings", ["team_key", "updated_at"])

    # ── Match data ───────────────────────────────────────────────────────

    op.create_table(
        "matches",
        sa.Column("match_key", sa.String, primary_key=True),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("comp_level", sa.String, nullable=False),
        sa.Column("set_number", sa.Integer, nullable=False),
        sa.Column("match_number", sa.Integer, nullable=False),
        sa.Column("time", sa.Integer, nullable=True),
    )
    op.create_index("ix_matches_event_key", "matches", ["event_key"])
    op.create_index("ix_matches_event_time", "matches", ["event_key", "time"])
    op.create_index("ix_matches_event_comp_order", "matches", ["event_key", "comp_level", "set_number", "match_number"])

    op.create_table(
        "match_teams",
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), primary_key=True),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("alliance", sa.String, nullable=False),
        sa.Column("station", sa.String, nullable=True),
    )
    op.create_index("ix_match_teams_event_key", "match_teams", ["event_key"])
    op.create_index("ix_match_teams_alliance", "match_teams", ["alliance"])
    op.create_index("ix_match_teams_event_team", "match_teams", ["event_key", "team_key"])
    op.create_index("ix_match_teams_team_event", "match_teams", ["team_key", "event_key"])

    op.create_table(
        "match_videos",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("video_type", sa.String, nullable=False),
        sa.Column("video_key", sa.String, nullable=False),
        sa.Column("url", sa.String, nullable=False),
    )
    op.create_index("ix_match_videos_match_key", "match_videos", ["match_key"])

    # ── Field calibration ────────────────────────────────────────────────

    op.create_table(
        "field_calibrations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=False, unique=True),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("frame_time_sec", sa.Float, nullable=True),
        sa.Column("image_width", sa.Integer, nullable=False),
        sa.Column("image_height", sa.Integer, nullable=False),
        sa.Column("image_points", sa.JSON, nullable=False),
        sa.Column("field_points", sa.JSON, nullable=False),
        sa.Column("homography", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_field_calibrations_match_key", "field_calibrations", ["match_key"])
    op.create_index("ix_field_calibrations_event_key", "field_calibrations", ["event_key"])

    # ── Analysis pipeline ────────────────────────────────────────────────

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("version", sa.String, nullable=False, server_default="v0"),
        sa.Column("status", sa.String, nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_analysis_runs_match_key", "analysis_runs", ["match_key"])

    op.create_table(
        "analysis_run_contexts",
        sa.Column("run_id", sa.Integer, sa.ForeignKey("analysis_runs.id"), primary_key=True),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("analysis_version", sa.String, nullable=False, server_default="video_v3_tracks"),
        sa.Column("params_hash", sa.String, nullable=False, server_default=""),
        sa.Column("calibration_id", sa.Integer, sa.ForeignKey("field_calibrations.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_analysis_run_contexts_match_key", "analysis_run_contexts", ["match_key"])
    op.create_index("ix_analysis_run_contexts_event_key", "analysis_run_contexts", ["event_key"])
    op.create_index("ix_analysis_run_contexts_analysis_version", "analysis_run_contexts", ["analysis_version"])
    op.create_index("ix_analysis_run_contexts_params_hash", "analysis_run_contexts", ["params_hash"])
    op.create_index("ix_analysis_run_contexts_calibration_id", "analysis_run_contexts", ["calibration_id"])
    op.create_index("ix_analysis_run_contexts_created_at", "analysis_run_contexts", ["created_at"])

    op.create_table(
        "team_match_findings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("analysis_run_id", sa.Integer, sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), nullable=False),
        sa.Column("alliance", sa.String, nullable=False),
        sa.Column("station", sa.String, nullable=True),
        sa.Column("source", sa.String, nullable=False, server_default="video_v1"),
        sa.Column("fuel_scoring_rate", sa.Float, nullable=True),
        sa.Column("cycle_time_sec", sa.Float, nullable=True),
        sa.Column("auto_contribution", sa.Float, nullable=True),
        sa.Column("climb_success_prob", sa.Float, nullable=True),
        sa.Column("defensive_engagement_sec", sa.Float, nullable=True),
        sa.Column("reliability_score", sa.Float, nullable=True),
        sa.Column("summary", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_team_match_findings_analysis_run_id", "team_match_findings", ["analysis_run_id"])
    op.create_index("ix_team_match_findings_match_key", "team_match_findings", ["match_key"])
    op.create_index("ix_team_match_findings_event_key", "team_match_findings", ["event_key"])
    op.create_index("ix_team_match_findings_team_key", "team_match_findings", ["team_key"])
    op.create_index("ix_team_match_findings_alliance", "team_match_findings", ["alliance"])
    op.create_index("ix_team_match_finding_event_team", "team_match_findings", ["event_key", "team_key"])
    op.create_index("ix_team_match_finding_match", "team_match_findings", ["match_key", "team_key"])
    op.create_index("ix_team_match_finding_team_event_id", "team_match_findings", ["team_key", "event_key", "id"])
    op.create_index("ix_team_match_finding_team_run", "team_match_findings", ["team_key", "analysis_run_id"])

    op.create_table(
        "team_match_throughputs",
        sa.Column("finding_id", sa.Integer, sa.ForeignKey("team_match_findings.id"), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer, sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), nullable=False),
        sa.Column("balls_shot_total", sa.Integer, nullable=True),
        sa.Column("shooting_time_total_seconds", sa.Float, nullable=True),
        sa.Column("bps_total", sa.Float, nullable=True),
        sa.Column("balls_shot_active", sa.Integer, nullable=True),
        sa.Column("shooting_time_active_seconds", sa.Float, nullable=True),
        sa.Column("active_bps", sa.Float, nullable=True),
        sa.Column("metric_coverage", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("source", sa.String, nullable=False, server_default="video_v3_tracks"),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_team_match_throughputs_analysis_run_id", "team_match_throughputs", ["analysis_run_id"])
    op.create_index("ix_team_match_throughputs_match_key", "team_match_throughputs", ["match_key"])
    op.create_index("ix_team_match_throughputs_event_key", "team_match_throughputs", ["event_key"])
    op.create_index("ix_team_match_throughputs_team_key", "team_match_throughputs", ["team_key"])
    op.create_index("ix_team_match_throughputs_updated_at", "team_match_throughputs", ["updated_at"])

    # ── Tracking data ────────────────────────────────────────────────────

    op.create_table(
        "robot_tracks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("analysis_run_id", sa.Integer, sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), nullable=True),
        sa.Column("track_id", sa.Integer, nullable=False),
        sa.Column("frame_index", sa.Integer, nullable=False),
        sa.Column("time_sec", sa.Float, nullable=False),
        sa.Column("bbox_x1", sa.Float, nullable=False),
        sa.Column("bbox_y1", sa.Float, nullable=False),
        sa.Column("bbox_x2", sa.Float, nullable=False),
        sa.Column("bbox_y2", sa.Float, nullable=False),
        sa.Column("centroid_x", sa.Float, nullable=False),
        sa.Column("centroid_y", sa.Float, nullable=False),
        sa.Column("field_x", sa.Float, nullable=True),
        sa.Column("field_y", sa.Float, nullable=True),
        sa.Column("zone_key", sa.String, nullable=True),
        sa.Column("speed_mps", sa.Float, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("source", sa.String, nullable=False, server_default="cv_motion_v1"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_robot_tracks_analysis_run_id", "robot_tracks", ["analysis_run_id"])
    op.create_index("ix_robot_tracks_match_key", "robot_tracks", ["match_key"])
    op.create_index("ix_robot_tracks_event_key", "robot_tracks", ["event_key"])
    op.create_index("ix_robot_tracks_team_key", "robot_tracks", ["team_key"])
    op.create_index("ix_robot_tracks_track_id", "robot_tracks", ["track_id"])
    op.create_index("ix_robot_tracks_frame_index", "robot_tracks", ["frame_index"])
    op.create_index("ix_robot_tracks_time_sec", "robot_tracks", ["time_sec"])
    op.create_index("ix_robot_tracks_zone_key", "robot_tracks", ["zone_key"])
    op.create_index("ix_robot_tracks_created_at", "robot_tracks", ["created_at"])
    op.create_index("ix_robot_track_event_team", "robot_tracks", ["event_key", "team_key"])
    op.create_index("ix_robot_track_match_time", "robot_tracks", ["match_key", "time_sec"])
    op.create_index("ix_robot_track_team_event_time", "robot_tracks", ["team_key", "event_key", "time_sec", "id"])
    op.create_index("ix_robot_track_team_run_time", "robot_tracks", ["team_key", "analysis_run_id", "time_sec", "id"])

    op.create_table(
        "match_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("analysis_run_id", sa.Integer, sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), nullable=True),
        sa.Column("track_id", sa.Integer, nullable=True),
        sa.Column("frame_index", sa.Integer, nullable=True),
        sa.Column("time_sec", sa.Float, nullable=False),
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("field_x", sa.Float, nullable=True),
        sa.Column("field_y", sa.Float, nullable=True),
        sa.Column("meta", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_match_events_analysis_run_id", "match_events", ["analysis_run_id"])
    op.create_index("ix_match_events_match_key", "match_events", ["match_key"])
    op.create_index("ix_match_events_event_key", "match_events", ["event_key"])
    op.create_index("ix_match_events_team_key", "match_events", ["team_key"])
    op.create_index("ix_match_events_track_id", "match_events", ["track_id"])
    op.create_index("ix_match_events_time_sec", "match_events", ["time_sec"])
    op.create_index("ix_match_events_event_type", "match_events", ["event_type"])
    op.create_index("ix_match_events_created_at", "match_events", ["created_at"])
    op.create_index("ix_match_events_team_event_time", "match_events", ["team_key", "event_key", "time_sec", "id"])
    op.create_index("ix_match_events_team_run_time", "match_events", ["team_key", "analysis_run_id", "time_sec", "id"])

    # ── Quality & artifacts ──────────────────────────────────────────────

    op.create_table(
        "analysis_qualities",
        sa.Column("run_id", sa.Integer, sa.ForeignKey("analysis_runs.id"), primary_key=True),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=False),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("calibration_quality_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("tracking_quality_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("identity_quality_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("overall_quality_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("details", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_analysis_qualities_match_key", "analysis_qualities", ["match_key"])
    op.create_index("ix_analysis_qualities_event_key", "analysis_qualities", ["event_key"])
    op.create_index("ix_analysis_qualities_overall_quality_score", "analysis_qualities", ["overall_quality_score"])
    op.create_index("ix_analysis_qualities_updated_at", "analysis_qualities", ["updated_at"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("analysis_run_id", sa.Integer, sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False, server_default="{}"),
    )
    op.create_index("ix_artifacts_analysis_run_id", "artifacts", ["analysis_run_id"])

    # ── Phase windows ────────────────────────────────────────────────────

    op.create_table(
        "match_phase_windows",
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), primary_key=True),
        sa.Column("phase_key", sa.String, primary_key=True),
        sa.Column("model_version", sa.String, primary_key=True, server_default="phase_v1"),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=False),
        sa.Column("start_sec", sa.Float, nullable=False),
        sa.Column("end_sec", sa.Float, nullable=False),
        sa.Column("duration_sec", sa.Float, nullable=False),
        sa.Column("start_unix", sa.Integer, nullable=True),
        sa.Column("end_unix", sa.Integer, nullable=True),
        sa.Column("source", sa.String, nullable=False, server_default="game_config"),
        sa.Column("confidence_0_1", sa.Float, nullable=False, server_default="0.8"),
        sa.Column("params_hash", sa.String, nullable=False, server_default=""),
        sa.Column("computed_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_match_phase_windows_event_key", "match_phase_windows", ["event_key"])
    op.create_index("ix_match_phase_windows_params_hash", "match_phase_windows", ["params_hash"])
    op.create_index("ix_match_phase_windows_computed_at", "match_phase_windows", ["computed_at"])

    # ── Synergy models ───────────────────────────────────────────────────

    op.create_table(
        "team_season_strengths",
        sa.Column("season", sa.Integer, primary_key=True),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("model_version", sa.String, primary_key=True, server_default="synergy_v1"),
        sa.Column("strength_active_bps", sa.Float, nullable=True),
        sa.Column("confidence_0_1", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("matches_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_quality", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("coverage_factor", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("params_hash", sa.String, nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_team_season_strengths_params_hash", "team_season_strengths", ["params_hash"])
    op.create_index("ix_team_season_strengths_updated_at", "team_season_strengths", ["updated_at"])

    op.create_table(
        "team_pair_synergy_priors",
        sa.Column("season", sa.Integer, primary_key=True),
        sa.Column("team_key_a", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("team_key_b", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("model_version", sa.String, primary_key=True, server_default="synergy_v1"),
        sa.Column("synergy_points_prior", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("confidence_prior", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("n_matches_together", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_quality", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("residual_mean_raw", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("params_hash", sa.String, nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_team_pair_synergy_priors_params_hash", "team_pair_synergy_priors", ["params_hash"])
    op.create_index("ix_team_pair_synergy_priors_updated_at", "team_pair_synergy_priors", ["updated_at"])

    op.create_table(
        "team_event_throughput_strengths",
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), primary_key=True),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("model_version", sa.String, primary_key=True, server_default="synergy_v1"),
        sa.Column("season", sa.Integer, nullable=False),
        sa.Column("strength_active_bps", sa.Float, nullable=True),
        sa.Column("confidence_0_1", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("matches_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_quality", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("coverage_factor", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("params_hash", sa.String, nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_team_event_throughput_strengths_season", "team_event_throughput_strengths", ["season"])
    op.create_index("ix_team_event_throughput_strengths_params_hash", "team_event_throughput_strengths", ["params_hash"])
    op.create_index("ix_team_event_throughput_strengths_updated_at", "team_event_throughput_strengths", ["updated_at"])

    op.create_table(
        "team_pair_synergy_events",
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), primary_key=True),
        sa.Column("team_key_a", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("team_key_b", sa.String, sa.ForeignKey("teams.team_key"), primary_key=True),
        sa.Column("model_version", sa.String, primary_key=True, server_default="synergy_v1"),
        sa.Column("season", sa.Integer, nullable=False),
        sa.Column("synergy_points_event", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("confidence_event", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("n_matches_together", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_quality", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("residual_mean_raw", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("params_hash", sa.String, nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_team_pair_synergy_events_season", "team_pair_synergy_events", ["season"])
    op.create_index("ix_team_pair_synergy_events_params_hash", "team_pair_synergy_events", ["params_hash"])
    op.create_index("ix_team_pair_synergy_events_updated_at", "team_pair_synergy_events", ["updated_at"])

    op.create_table(
        "match_synergy_projections",
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), primary_key=True),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), primary_key=True),
        sa.Column("alliance_color", sa.String, primary_key=True),
        sa.Column("model_version", sa.String, primary_key=True, server_default="synergy_v1"),
        sa.Column("season", sa.Integer, nullable=False),
        sa.Column("scheduled_time", sa.Integer, nullable=True),
        sa.Column("expected_throughput", sa.Float, nullable=True),
        sa.Column("alliance_synergy_points", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("projected_throughput", sa.Float, nullable=True),
        sa.Column("alliance_synergy_score_0_100", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("confidence_0_1", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("source_label", sa.String, nullable=False, server_default="projection"),
        sa.Column("pair_breakdown", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("params_hash", sa.String, nullable=False, server_default=""),
        sa.Column("computed_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_match_synergy_projections_season", "match_synergy_projections", ["season"])
    op.create_index("ix_match_synergy_projections_params_hash", "match_synergy_projections", ["params_hash"])
    op.create_index("ix_match_synergy_projections_computed_at", "match_synergy_projections", ["computed_at"])

    # ── Intel & ML ───────────────────────────────────────────────────────

    op.create_table(
        "intel_snapshots",
        sa.Column("snapshot_key", sa.String, primary_key=True),
        sa.Column("scope", sa.String, nullable=False),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), nullable=True),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=True),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("source_version", sa.String, nullable=False, server_default="intel_snapshot_v1"),
        sa.Column("generated_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_intel_snapshots_scope", "intel_snapshots", ["scope"])
    op.create_index("ix_intel_snapshots_team_key", "intel_snapshots", ["team_key"])
    op.create_index("ix_intel_snapshots_event_key", "intel_snapshots", ["event_key"])
    op.create_index("ix_intel_snapshots_generated_at", "intel_snapshots", ["generated_at"])
    op.create_index("ix_intel_snapshots_expires_at", "intel_snapshots", ["expires_at"])
    op.create_index("ix_intel_snapshots_scope_event_team", "intel_snapshots", ["scope", "event_key", "team_key"])

    op.create_table(
        "ml_feature_snapshots",
        sa.Column("snapshot_key", sa.String, primary_key=True),
        sa.Column("scope", sa.String, nullable=False),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=True),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=True),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), nullable=True),
        sa.Column("alliance_color", sa.String, nullable=True),
        sa.Column("feature_vector", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("target", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("split_tag", sa.String, nullable=False, server_default="train"),
        sa.Column("source_version", sa.String, nullable=False, server_default="shadow_features_v1"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_ml_feature_snapshots_scope", "ml_feature_snapshots", ["scope"])
    op.create_index("ix_ml_feature_snapshots_event_key", "ml_feature_snapshots", ["event_key"])
    op.create_index("ix_ml_feature_snapshots_match_key", "ml_feature_snapshots", ["match_key"])
    op.create_index("ix_ml_feature_snapshots_team_key", "ml_feature_snapshots", ["team_key"])
    op.create_index("ix_ml_feature_snapshots_alliance_color", "ml_feature_snapshots", ["alliance_color"])
    op.create_index("ix_ml_feature_snapshots_split_tag", "ml_feature_snapshots", ["split_tag"])
    op.create_index("ix_ml_feature_snapshots_source_version", "ml_feature_snapshots", ["source_version"])
    op.create_index("ix_ml_feature_snapshots_created_at", "ml_feature_snapshots", ["created_at"])
    op.create_index("ix_ml_feature_snapshots_updated_at", "ml_feature_snapshots", ["updated_at"])
    op.create_index("ix_ml_feature_snapshots_scope_event_created", "ml_feature_snapshots", ["scope", "event_key", "created_at"])
    op.create_index("ix_ml_feature_snapshots_scope_split_created", "ml_feature_snapshots", ["scope", "split_tag", "created_at"])

    op.create_table(
        "ml_model_registry",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("model_key", sa.String, nullable=False),
        sa.Column("model_version", sa.String, nullable=False),
        sa.Column("framework", sa.String, nullable=False, server_default="torch"),
        sa.Column("artifact_path", sa.String, nullable=False),
        sa.Column("input_schema", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("metrics", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("params", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("trained_at", sa.DateTime, nullable=False),
        sa.Column("activated_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("model_key", "model_version", name="uq_ml_model_registry_key_version"),
    )
    op.create_index("ix_ml_model_registry_model_key", "ml_model_registry", ["model_key"])
    op.create_index("ix_ml_model_registry_is_active", "ml_model_registry", ["is_active"])
    op.create_index("ix_ml_model_registry_trained_at", "ml_model_registry", ["trained_at"])
    op.create_index("ix_ml_model_registry_activated_at", "ml_model_registry", ["activated_at"])
    op.create_index("ix_ml_model_registry_created_at", "ml_model_registry", ["created_at"])
    op.create_index("ix_ml_model_registry_key_active", "ml_model_registry", ["model_key", "is_active"])

    op.create_table(
        "ml_shadow_predictions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("model_key", sa.String, nullable=False),
        sa.Column("model_version", sa.String, nullable=False),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=True),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=True),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), nullable=True),
        sa.Column("target_key", sa.String, nullable=False),
        sa.Column("prediction_value", sa.Float, nullable=True),
        sa.Column("prediction_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("feature_snapshot_key", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_ml_shadow_predictions_model_key", "ml_shadow_predictions", ["model_key"])
    op.create_index("ix_ml_shadow_predictions_model_version", "ml_shadow_predictions", ["model_version"])
    op.create_index("ix_ml_shadow_predictions_event_key", "ml_shadow_predictions", ["event_key"])
    op.create_index("ix_ml_shadow_predictions_match_key", "ml_shadow_predictions", ["match_key"])
    op.create_index("ix_ml_shadow_predictions_team_key", "ml_shadow_predictions", ["team_key"])
    op.create_index("ix_ml_shadow_predictions_target_key", "ml_shadow_predictions", ["target_key"])
    op.create_index("ix_ml_shadow_predictions_feature_snapshot_key", "ml_shadow_predictions", ["feature_snapshot_key"])
    op.create_index("ix_ml_shadow_predictions_created_at", "ml_shadow_predictions", ["created_at"])
    op.create_index(
        "ix_ml_shadow_predictions_event_model_target",
        "ml_shadow_predictions",
        ["event_key", "model_key", "model_version", "target_key"],
    )

    # ── Scouting rooms (base table only; assignments/leaders in later migrations) ──

    op.create_table(
        "scouting_rooms",
        sa.Column("room_key", sa.String, primary_key=True),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=True),
        sa.Column("title", sa.String, nullable=True),
        sa.Column("created_by", sa.String, nullable=True),
        sa.Column("archived", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.Column("last_activity_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_scouting_rooms_event_key", "scouting_rooms", ["event_key"])
    op.create_index("ix_scouting_rooms_archived", "scouting_rooms", ["archived"])
    op.create_index("ix_scouting_rooms_created_at", "scouting_rooms", ["created_at"])
    op.create_index("ix_scouting_rooms_updated_at", "scouting_rooms", ["updated_at"])
    op.create_index("ix_scouting_rooms_last_activity_at", "scouting_rooms", ["last_activity_at"])

    op.create_table(
        "scouting_room_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("room_key", sa.String, sa.ForeignKey("scouting_rooms.room_key"), nullable=False),
        sa.Column("event_key", sa.String, sa.ForeignKey("events.event_key"), nullable=True),
        sa.Column("match_key", sa.String, sa.ForeignKey("matches.match_key"), nullable=True),
        sa.Column("team_key", sa.String, sa.ForeignKey("teams.team_key"), nullable=True),
        sa.Column("scout_profile", sa.String, nullable=False),
        sa.Column("client_entry_id", sa.String, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("total_points", sa.Float, nullable=True),
        sa.Column("driver_score_0_100", sa.Float, nullable=True),
        sa.Column("manual_rating_0_100", sa.Float, nullable=True),
        sa.Column("scouting_api_rating_0_100", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("room_key", "client_entry_id", name="uq_scouting_room_entry_client"),
    )
    op.create_index("ix_scouting_room_entries_room_key", "scouting_room_entries", ["room_key"])
    op.create_index("ix_scouting_room_entries_event_key", "scouting_room_entries", ["event_key"])
    op.create_index("ix_scouting_room_entries_match_key", "scouting_room_entries", ["match_key"])
    op.create_index("ix_scouting_room_entries_team_key", "scouting_room_entries", ["team_key"])
    op.create_index("ix_scouting_room_entries_scout_profile", "scouting_room_entries", ["scout_profile"])
    op.create_index("ix_scouting_room_entries_created_at", "scouting_room_entries", ["created_at"])
    op.create_index("ix_scouting_room_entries_updated_at", "scouting_room_entries", ["updated_at"])
    op.create_index("ix_scouting_room_entries_room_created", "scouting_room_entries", ["room_key", "created_at"])
    op.create_index("ix_scouting_room_entries_room_team", "scouting_room_entries", ["room_key", "team_key"])

def downgrade() -> None:
    tables = [
        "scouting_room_entries",
        "scouting_rooms",
        "ml_shadow_predictions",
        "ml_model_registry",
        "ml_feature_snapshots",
        "intel_snapshots",
        "match_synergy_projections",
        "team_pair_synergy_events",
        "team_event_throughput_strengths",
        "team_pair_synergy_priors",
        "team_season_strengths",
        "match_phase_windows",
        "artifacts",
        "analysis_qualities",
        "match_events",
        "robot_tracks",
        "team_match_throughputs",
        "team_match_findings",
        "analysis_run_contexts",
        "analysis_runs",
        "field_calibrations",
        "match_videos",
        "match_teams",
        "matches",
        "team_static_capabilities",
        "event_team_stats",
        "event_team_ratings",
        "event_teams",
        "team_profiles",
        "teams",
        "event_profiles",
        "events",
    ]
    for table in tables:
        op.drop_table(table)
