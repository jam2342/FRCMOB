# Event data ingestion service.
#
# Extracted from ``app.api.routes_events`` to resolve cross-layer dependency
# violations (services → routes).  All ingest logic that does not depend on
# HTTP request/response semantics lives here.

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import sanitize_external_error
from app.db import models
from app.services.ml.shadow import auto_train_shadow_models_for_event_breakdown
from app.services.ratings.model import recompute_event_ratings
from app.services.scoring.truth import (
    _extract_score_breakdown_truth_rows,
    _rebuilt_active_hub_duration_sec,
    _truth_context,
)
from app.services.ml.synergy import SYNERGY_MODEL_VERSION, precompute_event_synergy
from app.services.utils import _as_float
from app.tba.client import TBAClient

logger = logging.getLogger(__name__)

TBA_SCOREBREAKDOWN_RUN_VERSION = "tba_score_breakdown_v1"
TBA_SCOREBREAKDOWN_SOURCE = "tba_score_breakdown"

# ── Helpers ──────────────────────────────────────────────────────────────

def _effective_fuel_rate_prior(
    *,
    fuel_scoring_rate: object,
    cycle_time_sec: object,
) -> float | None:
    rate = _as_float(fuel_scoring_rate)
    cycle_time = _as_float(cycle_time_sec)
    cycle_implied = (
        (60.0 / max(1e-6, float(cycle_time)))
        if cycle_time is not None and cycle_time > 0.0
        else None
    )
    if rate is None and cycle_implied is None:
        return None
    if rate is None:
        return float(cycle_implied)
    if cycle_implied is None:
        return max(0.0, float(rate))
    return max(0.0, float(rate), float(cycle_implied))

def compute_team_fuel_weight_priors(
    db: Session,
    *,
    event_key: str,
    season_year: int,
    team_keys: set[str],
) -> dict[str, float]:
    # Compute fuel rate prior weights for team allocation in score breakdown.
    if not team_keys:
        return {}

    samples_by_team: dict[str, list[float]] = defaultdict(list)

    def _collect_rate_samples(
        rows: list[tuple[str, float | None, float | None, int | None, int | None]],
    ) -> None:
        for team_key, fuel_rate, cycle_time, _match_time, _finding_id in rows:
            key = str(team_key or "")
            if not key:
                continue
            effective = _effective_fuel_rate_prior(
                fuel_scoring_rate=fuel_rate,
                cycle_time_sec=cycle_time,
            )
            if effective is None or effective <= 0.0:
                continue
            samples_by_team[key].append(float(effective))

    event_rows = (
        db.query(
            models.TeamMatchFinding.team_key,
            models.TeamMatchFinding.fuel_scoring_rate,
            models.TeamMatchFinding.cycle_time_sec,
            models.Match.time,
            models.TeamMatchFinding.id,
        )
        .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
        .filter(
            models.TeamMatchFinding.team_key.in_(team_keys),
            models.TeamMatchFinding.source != TBA_SCOREBREAKDOWN_SOURCE,
            models.TeamMatchFinding.event_key == event_key,
        )
        .order_by(models.Match.time.desc().nullslast(), models.TeamMatchFinding.id.desc())
        .limit(max(120, len(team_keys) * 10))
        .all()
    )
    _collect_rate_samples(event_rows)

    missing_for_season = [team_key for team_key in team_keys if not samples_by_team.get(team_key)]
    if missing_for_season:
        season_rows = (
            db.query(
                models.TeamMatchFinding.team_key,
                models.TeamMatchFinding.fuel_scoring_rate,
                models.TeamMatchFinding.cycle_time_sec,
                models.Match.time,
                models.TeamMatchFinding.id,
            )
            .join(models.Match, models.Match.match_key == models.TeamMatchFinding.match_key)
            .join(models.Event, models.Event.event_key == models.TeamMatchFinding.event_key)
            .filter(
                models.TeamMatchFinding.team_key.in_(missing_for_season),
                models.TeamMatchFinding.source != TBA_SCOREBREAKDOWN_SOURCE,
                models.Event.year == season_year,
            )
            .order_by(models.Match.time.desc().nullslast(), models.TeamMatchFinding.id.desc())
            .limit(max(200, len(missing_for_season) * 10))
            .all()
        )
        _collect_rate_samples(season_rows)

    raw_priors: dict[str, float] = {}
    for team_key, samples in samples_by_team.items():
        if not samples:
            continue
        weighted_sum = 0.0
        total_weight = 0.0
        for index, sample in enumerate(samples[:8]):
            weight = max(0.35, 1.0 - (index * 0.12))
            weighted_sum += float(sample) * weight
            total_weight += weight
        if total_weight > 0.0:
            raw_priors[team_key] = weighted_sum / total_weight

    missing_for_opr = [team_key for team_key in team_keys if team_key not in raw_priors]
    if missing_for_opr:
        stat_rows = (
            db.query(models.EventTeamStat.team_key, models.EventTeamStat.opr)
            .filter(
                models.EventTeamStat.event_key == event_key,
                models.EventTeamStat.team_key.in_(missing_for_opr),
            )
            .all()
        )
        for team_key, opr in stat_rows:
            key = str(team_key or "")
            numeric = _as_float(opr)
            if not key or numeric is None or numeric <= 0.0:
                continue
            raw_priors[key] = max(0.2, min(20.0, float(numeric) * 0.08))

    return {
        team_key: round(max(0.8, min(4.5, 1.0 + (float(prior) ** 0.4))), 4)
        for team_key, prior in raw_priors.items()
        if prior > 0.0
    }

def clear_existing_score_breakdown_rows(
    db: Session,
    event_key: str,
) -> int:
    # Remove existing TBA score breakdown analysis runs for an event.
    stale_run_ids = [
        int(run_id)
        for run_id, in (
            db.query(models.AnalysisRun.id)
            .join(models.Match, models.Match.match_key == models.AnalysisRun.match_key)
            .filter(
                models.Match.event_key == event_key,
                models.AnalysisRun.version == TBA_SCOREBREAKDOWN_RUN_VERSION,
            )
            .all()
        )
    ]
    if not stale_run_ids:
        return 0

    db.query(models.TeamMatchThroughput).filter(
        models.TeamMatchThroughput.analysis_run_id.in_(stale_run_ids)
    ).delete(synchronize_session=False)
    db.query(models.MatchEvent).filter(
        models.MatchEvent.analysis_run_id.in_(stale_run_ids)
    ).delete(synchronize_session=False)
    db.query(models.RobotTrack).filter(
        models.RobotTrack.analysis_run_id.in_(stale_run_ids)
    ).delete(synchronize_session=False)
    db.query(models.AnalysisQuality).filter(
        models.AnalysisQuality.run_id.in_(stale_run_ids)
    ).delete(synchronize_session=False)
    db.query(models.TeamMatchFinding).filter(
        models.TeamMatchFinding.analysis_run_id.in_(stale_run_ids)
    ).delete(synchronize_session=False)
    db.query(models.AnalysisRunContext).filter(
        models.AnalysisRunContext.run_id.in_(stale_run_ids)
    ).delete(synchronize_session=False)
    db.query(models.AnalysisRun).filter(
        models.AnalysisRun.id.in_(stale_run_ids)
    ).delete(synchronize_session=False)
    db.flush()
    return len(stale_run_ids)

def upsert_score_breakdown_truth(
    db: Session,
    *,
    event_key: str,
    season_year: int,
    matches: list[dict],
) -> dict[str, int]:
    # Parse TBA score breakdowns into finding rows.
    context = _truth_context()
    phases = context["phases"]
    auto_end_sec = float(phases["auto_sec"])
    teleop_sec = max(1.0, float(phases["teleop_sec"]))
    if int(season_year) >= 2026:
        teleop_sec = max(
            1.0,
            float(
                phases.get("teleop_active_hub_sec")
                or _rebuilt_active_hub_duration_sec(phases, include_post_deactivate_grace=True)
            ),
        )
    total_sec = max(auto_end_sec, float(phases["total_sec"]))
    endgame_start_sec = max(auto_end_sec, total_sec - float(phases["endgame_sec"]))
    cleared_runs = clear_existing_score_breakdown_rows(db, event_key)

    non_tba_findings_by_team_match = {
        (str(match_key), str(team_key))
        for match_key, team_key in (
            db.query(models.TeamMatchFinding.match_key, models.TeamMatchFinding.team_key)
            .filter(
                models.TeamMatchFinding.event_key == event_key,
                models.TeamMatchFinding.source != TBA_SCOREBREAKDOWN_SOURCE,
            )
            .all()
        )
        if isinstance(match_key, str) and isinstance(team_key, str)
    }

    inserted_runs = 0
    inserted_findings = 0
    inserted_events = 0
    skipped_due_existing_findings = 0
    parsed_matches = 0
    matched_rows = 0
    all_match_team_keys: set[str] = set()
    for match in matches:
        alliances = match.get("alliances")
        if not isinstance(alliances, dict):
            continue
        for alliance in ("red", "blue"):
            payload = alliances.get(alliance)
            if not isinstance(payload, dict):
                continue
            for team_key in payload.get("team_keys") or []:
                if isinstance(team_key, str) and team_key:
                    all_match_team_keys.add(team_key)
    team_weight_by_key = compute_team_fuel_weight_priors(
        db,
        event_key=event_key,
        season_year=season_year,
        team_keys=all_match_team_keys,
    )

    for match in matches:
        match_key = match.get("key")
        if not isinstance(match_key, str) or not match_key:
            continue
        truth_rows = _extract_score_breakdown_truth_rows(
            match=match,
            season_year=season_year,
            context=context,
            team_weight_by_key=team_weight_by_key,
        )
        if not truth_rows:
            continue
        parsed_matches += 1

        run_row: models.AnalysisRun | None = None
        for row in truth_rows:
            team_key = str(row.get("team_key") or "")
            alliance = str(row.get("alliance") or "")
            station = str(row.get("station") or "")
            if not team_key or alliance not in {"red", "blue"}:
                continue
            if (match_key, team_key) in non_tba_findings_by_team_match:
                skipped_due_existing_findings += 1
                continue

            auto_points = max(0.0, float(_as_float(row.get("auto_points")) or 0.0))
            teleop_points = max(0.0, float(_as_float(row.get("teleop_points")) or 0.0))
            teleop_score_count = _as_float(row.get("teleop_score_count"))
            climb_points = max(0.0, float(_as_float(row.get("climb_points")) or 0.0))
            climb_success = bool(row.get("climb_success"))
            if (
                auto_points <= 0.0
                and teleop_points <= 0.0
                and (teleop_score_count is None or teleop_score_count <= 0.0)
                and climb_points <= 0.0
                and not climb_success
            ):
                continue

            if run_row is None:
                run_row = models.AnalysisRun(
                    match_key=match_key,
                    version=TBA_SCOREBREAKDOWN_RUN_VERSION,
                    status="completed",
                )
                db.add(run_row)
                db.flush()
                db.add(
                    models.AnalysisRunContext(
                        run_id=run_row.id,
                        match_key=match_key,
                        event_key=event_key,
                        analysis_version=TBA_SCOREBREAKDOWN_RUN_VERSION,
                        params_hash=TBA_SCOREBREAKDOWN_RUN_VERSION,
                        calibration_id=None,
                    )
                )
                inserted_runs += 1

            scoring_proxy = (
                teleop_score_count
                if teleop_score_count is not None and teleop_score_count > 0.0
                else teleop_points
            )
            fuel_scoring_rate_raw = (
                (float(scoring_proxy) / teleop_sec) * 60.0
                if scoring_proxy is not None and float(scoring_proxy) > 0.0
                else None
            )
            fuel_scoring_rate = (
                max(0.0, float(fuel_scoring_rate_raw))
                if fuel_scoring_rate_raw is not None
                else None
            )
            cycle_time_sec = (
                (teleop_sec / max(1e-6, float(scoring_proxy)))
                if scoring_proxy is not None and float(scoring_proxy) > 0.0
                else None
            )
            climb_success_prob = 1.0 if climb_success else (0.35 if climb_points > 0.0 else 0.0)
            status_payload = row.get("status") if isinstance(row.get("status"), dict) else {}
            db.add(
                models.TeamMatchFinding(
                    analysis_run_id=run_row.id,
                    match_key=match_key,
                    event_key=event_key,
                    team_key=team_key,
                    alliance=alliance,
                    station=station or None,
                    source=TBA_SCOREBREAKDOWN_SOURCE,
                    fuel_scoring_rate=round(float(fuel_scoring_rate), 4) if fuel_scoring_rate is not None else None,
                    cycle_time_sec=round(float(cycle_time_sec), 4) if cycle_time_sec is not None else None,
                    auto_contribution=round(auto_points, 4),
                    climb_success_prob=round(climb_success_prob, 4),
                    defensive_engagement_sec=None,
                    reliability_score=None,
                    summary={
                        "source": TBA_SCOREBREAKDOWN_SOURCE,
                        "official_score_breakdown": True,
                        "season_year": season_year,
                        "status": status_payload,
                    },
                )
            )
            inserted_findings += 1
            matched_rows += 1

            if auto_points > 0.0:
                db.add(
                    models.MatchEvent(
                        analysis_run_id=run_row.id,
                        match_key=match_key,
                        event_key=event_key,
                        team_key=team_key,
                        track_id=None,
                        frame_index=None,
                        time_sec=min(total_sec, auto_end_sec),
                        event_type="auto_points_scored",
                        confidence=0.99,
                        field_x=None,
                        field_y=None,
                        meta={
                            "source": TBA_SCOREBREAKDOWN_SOURCE,
                            "official_points": round(auto_points, 4),
                            "season_year": season_year,
                        },
                    )
                )
                inserted_events += 1

            if scoring_proxy is not None and float(scoring_proxy) > 0.0:
                db.add(
                    models.MatchEvent(
                        analysis_run_id=run_row.id,
                        match_key=match_key,
                        event_key=event_key,
                        team_key=team_key,
                        track_id=None,
                        frame_index=None,
                        time_sec=min(total_sec, auto_end_sec + (0.5 * teleop_sec)),
                        event_type="teleop_fuel_score_success",
                        confidence=0.98,
                        field_x=None,
                        field_y=None,
                        meta={
                            "source": TBA_SCOREBREAKDOWN_SOURCE,
                            "count_estimate": round(float(scoring_proxy), 4),
                            "official_points": round(teleop_points, 4),
                            "season_year": season_year,
                        },
                    )
                )
                inserted_events += 1

            if climb_points > 0.0 or climb_success:
                db.add(
                    models.MatchEvent(
                        analysis_run_id=run_row.id,
                        match_key=match_key,
                        event_key=event_key,
                        team_key=team_key,
                        track_id=None,
                        frame_index=None,
                        time_sec=min(total_sec, endgame_start_sec + 5.0),
                        event_type="climb_success" if climb_success else "climb_attempt",
                        confidence=0.98,
                        field_x=None,
                        field_y=None,
                        meta={
                            "source": TBA_SCOREBREAKDOWN_SOURCE,
                            "official_points": round(climb_points, 4),
                            "status": status_payload,
                            "season_year": season_year,
                        },
                    )
                )
                inserted_events += 1

    if inserted_runs > 0:
        logger.info(
            "event_ingest.score_breakdown_truth event=%s runs=%s findings=%s events=%s skipped_existing=%s",
            event_key,
            inserted_runs,
            inserted_findings,
            inserted_events,
            skipped_due_existing_findings,
        )
    return {
        "cleared_runs": int(cleared_runs),
        "parsed_matches": int(parsed_matches),
        "matched_rows": int(matched_rows),
        "inserted_runs": int(inserted_runs),
        "inserted_findings": int(inserted_findings),
        "inserted_events": int(inserted_events),
        "skipped_due_existing_findings": int(skipped_due_existing_findings),
    }

def upsert_event_team_stats_from_tba(
    db: Session,
    tba: TBAClient,
    event_key: str,
) -> tuple[int, str]:
    # Fetch and upsert OPR/DPR/CCWM stats from TBA for an event.
    try:
        payload = tba.event_oprs(event_key)
    except Exception:
        return 0, "remote_unavailable"

    if not isinstance(payload, dict):
        return 0, "invalid_payload"

    oprs = payload.get("oprs") if isinstance(payload.get("oprs"), dict) else {}
    dprs = payload.get("dprs") if isinstance(payload.get("dprs"), dict) else {}
    ccwms = payload.get("ccwms") if isinstance(payload.get("ccwms"), dict) else {}

    all_team_keys = set(oprs.keys()) | set(dprs.keys()) | set(ccwms.keys())
    if not all_team_keys:
        return 0, "empty"

    upserts = 0
    for team_key in all_team_keys:
        if not isinstance(team_key, str) or not team_key:
            continue
        db.merge(
            models.EventTeamStat(
                event_key=event_key,
                team_key=team_key,
                opr=float(oprs[team_key]) if isinstance(oprs.get(team_key), (int, float)) else None,
                dpr=float(dprs[team_key]) if isinstance(dprs.get(team_key), (int, float)) else None,
                ccwm=float(ccwms[team_key]) if isinstance(ccwms.get(team_key), (int, float)) else None,
                source="tba_opr",
            )
        )
        upserts += 1

    db.commit()
    return upserts, "tba"

def ingest_event(
    event_key: str,
    tba: TBAClient,
    db: Session,
    *,
    run_post_compute: bool | None = None,
) -> dict[str, Any]:
    # Ingest event data from TBA into the database.
    #
    # This is the core business logic, free of HTTP concerns.
    # The API route handler wraps this with auth checks and error handling.
    import time as _time
    _ingest_t0 = _time.perf_counter()
    logger.info("event_ingest.start event=%s", event_key)
    event = tba.event(event_key)
    teams = tba.event_teams(event_key)
    matches = tba.event_matches(event_key)

    db.merge(models.Event(event_key=event["key"], name=event["name"], year=event["year"]))
    db.merge(
        models.EventProfile(
            event_key=event["key"],
            city=event.get("city"),
            state_prov=event.get("state_prov"),
            country=event.get("country"),
        )
    )
    db.flush()

    team_keys_from_event = set()
    for team in teams:
        team_key = team["key"]
        team_keys_from_event.add(team_key)
        db.merge(
            models.Team(
                team_key=team_key,
                team_number=team["team_number"],
                nickname=team.get("nickname"),
            )
        )
        db.merge(
            models.TeamProfile(
                team_key=team_key,
                city=team.get("city"),
                state_prov=team.get("state_prov"),
                country=team.get("country"),
            )
        )

    # Some events occasionally return matches with a team not present in event_teams.
    # Pre-upsert those fallback teams so foreign keys on match_teams/event_teams stay valid.
    team_keys_from_matches = set()
    for match in matches:
        for alliance in ("red", "blue"):
            alliance_payload = (match.get("alliances") or {}).get(alliance) or {}
            team_keys_from_matches.update(alliance_payload.get("team_keys") or [])

    extra_team_keys = team_keys_from_matches - team_keys_from_event
    for team_key in sorted(extra_team_keys):
        team_number: int
        try:
            team_number = int(team_key.replace("frc", "", 1))
        except ValueError:
            team_number = 0
        db.merge(models.Team(team_key=team_key, team_number=team_number, nickname=None))

    db.flush()

    all_event_team_keys = team_keys_from_event | extra_team_keys
    for team_key in all_event_team_keys:
        db.merge(models.EventTeam(event_key=event_key, team_key=team_key))
    db.flush()

    for match in matches:
        db.merge(
            models.Match(
                match_key=match["key"],
                event_key=event_key,
                comp_level=match["comp_level"],
                set_number=match["set_number"],
                match_number=match["match_number"],
                time=match.get("time"),
            )
        )
    db.flush()

    linked_match_teams = 0
    inserted_match_videos = 0
    missing_video_match_keys: list[str] = []

    def _merge_match_video(match_key_value: str, video_payload: dict) -> None:
        nonlocal inserted_match_videos
        video_type = str(video_payload.get("type") or "").strip().lower()
        video_key = str(video_payload.get("key") or "").strip()
        if not video_type or not video_key:
            return
        url = ""
        if video_type == "youtube":
            url = f"https://www.youtube.com/watch?v={video_key}"
        existing_video = (
            db.query(models.MatchVideo)
            .filter(
                models.MatchVideo.match_key == match_key_value,
                models.MatchVideo.video_type == video_type,
                models.MatchVideo.video_key == video_key,
            )
            .first()
        )
        if existing_video is not None:
            return
        db.add(
            models.MatchVideo(
                match_key=match_key_value,
                video_type=video_type,
                video_key=video_key,
                url=url,
            )
        )
        inserted_match_videos += 1

    for match in matches:
        for alliance in ("red", "blue"):
            alliance_payload = (match.get("alliances") or {}).get(alliance) or {}
            team_keys = alliance_payload.get("team_keys") or []
            for idx, team_key in enumerate(team_keys, start=1):
                db.merge(
                    models.MatchTeam(
                        match_key=match["key"],
                        team_key=team_key,
                        event_key=event_key,
                        alliance=alliance,
                        station=f"{alliance[0]}{idx}",
                    )
                )
                linked_match_teams += 1

        videos_payload = match.get("videos")
        normalized_videos = videos_payload if isinstance(videos_payload, list) else []
        if not normalized_videos:
            missing_video_match_keys.append(str(match.get("key") or "").strip().lower())
        for video in normalized_videos:
            if not isinstance(video, dict):
                continue
            _merge_match_video(str(match.get("key") or "").strip().lower(), video)

    video_backfill = {
        "enabled": bool(settings.events_ingest_backfill_match_videos),
        "missing_match_count": len([key for key in missing_video_match_keys if key]),
        "max_calls": max(0, int(settings.events_ingest_backfill_match_videos_max_calls)),
        "calls_made": 0,
        "matches_with_new_videos": 0,
        "errors": 0,
    }
    if (
        bool(settings.events_ingest_backfill_match_videos)
        and int(settings.events_ingest_backfill_match_videos_max_calls) > 0
        and missing_video_match_keys
    ):
        budget = max(0, min(int(settings.events_ingest_backfill_match_videos_max_calls), 200))
        for match_key in [key for key in missing_video_match_keys if key][:budget]:
            video_backfill["calls_made"] += 1
            try:
                match_payload = tba.match(match_key)
            except Exception:
                video_backfill["errors"] += 1
                continue
            if not isinstance(match_payload, dict):
                continue
            backfill_videos = match_payload.get("videos")
            if not isinstance(backfill_videos, list) or not backfill_videos:
                continue
            before_count = inserted_match_videos
            for video in backfill_videos:
                if not isinstance(video, dict):
                    continue
                _merge_match_video(match_key, video)
            if inserted_match_videos > before_count:
                video_backfill["matches_with_new_videos"] += 1

    score_breakdown_truth = upsert_score_breakdown_truth(
        db,
        event_key=event_key,
        season_year=int(event.get("year") or 0),
        matches=[match for match in matches if isinstance(match, dict)],
    )

    stats_upserted, stats_source = upsert_event_team_stats_from_tba(db, tba, event_key)
    if stats_upserted == 0:
        db.commit()
    ratings_recompute: dict[str, Any] = {
        "triggered": False,
        "ok": False,
        "detail": None,
        "model_version": None,
        "count": 0,
        "ml_shadow": None,
        "ml_shadow_auto_train": None,
    }
    synergy_precompute: dict[str, Any] = {
        "triggered": False,
        "ok": False,
        "detail": None,
    }
    should_run_post_compute = (
        bool(run_post_compute)
        if isinstance(run_post_compute, bool)
        else bool(settings.events_ingest_run_post_compute)
    )
    if should_run_post_compute and not settings.public_readonly_mode:
        try:
            ratings_payload = recompute_event_ratings(db, event_key)
            ratings_recompute = {
                "triggered": True,
                "ok": bool(ratings_payload.get("ok", False)),
                "detail": None,
                "model_version": ratings_payload.get("model_version"),
                "count": int(ratings_payload.get("count", 0) or 0),
                "quality_gate": ratings_payload.get("quality_gate"),
                "ml_shadow": ratings_payload.get("ml_shadow"),
                "ml_shadow_auto_train": None,
            }
        except Exception as exc:
            ratings_recompute = {
                "triggered": True,
                "ok": False,
                "detail": sanitize_external_error(exc, default="Ratings recompute failed."),
                "model_version": None,
                "count": 0,
                "ml_shadow": None,
                "ml_shadow_auto_train": None,
            }

        if any(match.get("comp_level") == "qm" for match in matches):
            try:
                precompute_result = precompute_event_synergy(db, event_key, model_version=SYNERGY_MODEL_VERSION)
                synergy_precompute = {
                    "triggered": True,
                    "ok": True,
                    "model_version": SYNERGY_MODEL_VERSION,
                    "projection_count": (
                        int(precompute_result.get("projections", {}).get("count", 0))
                        if isinstance(precompute_result, dict)
                        else 0
                    ),
                    "detail": None,
                }
            except Exception as exc:
                synergy_precompute = {
                    "triggered": True,
                    "ok": False,
                    "detail": sanitize_external_error(exc, default="Synergy precompute failed."),
                }

        ml_shadow_auto_train: dict[str, Any] = {
            "triggered": False,
            "ok": False,
            "detail": "disabled",
        }
        if bool(getattr(settings, "ml_shadow_auto_train_on_event_breakdown", False)):
            try:
                ml_shadow_auto_train = auto_train_shadow_models_for_event_breakdown(
                    db,
                    event_key=event_key,
                    limit_events=int(getattr(settings, "ml_shadow_auto_train_limit_events", 40) or 40),
                    source_version=None,
                    activate=bool(getattr(settings, "ml_shadow_auto_train_activate", True)),
                    replace_predictions=True,
                )
            except Exception as exc:
                ml_shadow_auto_train = {
                    "triggered": True,
                    "ok": False,
                    "detail": sanitize_external_error(exc, default="ML shadow auto-train failed."),
                }

            if bool(ml_shadow_auto_train.get("ok")) and bool(
                getattr(settings, "ml_shadow_auto_train_recompute_ratings", True)
            ):
                try:
                    ratings_payload = recompute_event_ratings(db, event_key)
                    ratings_recompute = {
                        "triggered": True,
                        "ok": bool(ratings_payload.get("ok", False)),
                        "detail": None,
                        "model_version": ratings_payload.get("model_version"),
                        "count": int(ratings_payload.get("count", 0) or 0),
                        "quality_gate": ratings_payload.get("quality_gate"),
                        "ml_shadow": ratings_payload.get("ml_shadow"),
                        "ml_shadow_auto_train": None,
                    }
                    ml_shadow_auto_train["ratings_recomputed_after_train"] = True
                    ml_shadow_auto_train["ratings_recompute_ok"] = bool(
                        ratings_recompute.get("ok")
                    )
                except Exception as exc:
                    ml_shadow_auto_train["ratings_recomputed_after_train"] = False
                    ml_shadow_auto_train["ratings_recompute_ok"] = False
                    ml_shadow_auto_train["ratings_recompute_error"] = sanitize_external_error(
                        exc,
                        default="Ratings recompute after ML auto-train failed.",
                    )
        ratings_recompute["ml_shadow_auto_train"] = ml_shadow_auto_train
    elif should_run_post_compute and settings.public_readonly_mode:
        ratings_recompute = {
            "triggered": False,
            "ok": False,
            "detail": "Skipped in public mode.",
            "model_version": None,
            "count": 0,
            "ml_shadow": None,
            "ml_shadow_auto_train": None,
        }
        synergy_precompute = {
            "triggered": False,
            "ok": False,
            "detail": "Skipped in public mode.",
        }

    _ingest_elapsed_ms = round((_time.perf_counter() - _ingest_t0) * 1000, 1)
    logger.info(
        "event_ingest.completed event=%s elapsed_ms=%.1f "
        "teams=%d matches=%d videos_inserted=%d stats_upserted=%d "
        "ratings_ok=%s synergy_ok=%s",
        event_key,
        _ingest_elapsed_ms,
        len(teams),
        len(matches),
        inserted_match_videos,
        stats_upserted,
        ratings_recompute.get("ok"),
        synergy_precompute.get("ok"),
    )
    return {
        "ok": True,
        "event_key": event_key,
        "teams": len(teams),
        "matches": len(matches),
        "match_team_links": linked_match_teams,
        "match_videos_inserted": int(inserted_match_videos),
        "video_backfill": video_backfill,
        "event_team_stats_upserted": stats_upserted,
        "event_team_stats_source": stats_source,
        "score_breakdown_truth": score_breakdown_truth,
        "run_post_compute": should_run_post_compute,
        "ratings_recompute": ratings_recompute,
        "synergy_precompute": synergy_precompute,
    }
