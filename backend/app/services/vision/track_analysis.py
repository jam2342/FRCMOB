# Post-processing: field projection, track summary, assignment, defense, metrics.
#
# Extracted from jobs.py to reduce module size.
from __future__ import annotations

import bisect
import math
from collections import defaultdict
from typing import Any

from app.db import models
from app.services.calibration.homography import project_image_to_field
from app.services.events.classifier import (
    TransitionEventModel,
    build_transition_feature_map,
    predict_transition_event,
    resolve_model_event_label,
)
from app.services.game_config import classify_point
from app.services.phase_windows import get_or_compute_match_phase_windows
from app.services.scoring.breakdown import (
    _is_endgame_zone,
    _merge_time_windows,
    _station_sort_key,
    _time_in_windows,
    _window_duration_sec,
)
from app.services.season_config import REBUILT_HUB_SCORE_GRACE_SEC
from app.services.utils import _clamp, _round

MIN_TRACK_OBS_FOR_ASSIGNMENT = 4

def _annotate_observations_with_field(
    observations: list[dict],
    homography: list[list[float]],
    field_min_x: float,
    field_min_y: float,
    scale_x: float,
    scale_y: float,
) -> list[dict]:
    for observation in observations:
        projected = project_image_to_field(
            homography,
            float(observation["centroid_x"]),
            float(observation["centroid_y"]),
        )
        if projected is None:
            observation["field_x"] = None
            observation["field_y"] = None
            observation["zone_key"] = None
            observation["zone_kind"] = None
            continue

        raw_field_x, raw_field_y = projected
        field_x = (raw_field_x - field_min_x) * scale_x
        field_y = (raw_field_y - field_min_y) * scale_y
        observation["field_x"] = _round(field_x, 4)
        observation["field_y"] = _round(field_y, 4)
        zone = classify_point(field_x, field_y)
        observation["zone_key"] = zone.key if zone else None
        observation["zone_kind"] = zone.kind if zone else None
    return observations

def _prune_observations_to_field_bounds(
    observations: list[dict],
    *,
    field_length_m: float,
    field_width_m: float,
    enabled: bool,
    margin_m: float,
    drop_unprojectable: bool,
) -> tuple[list[dict], dict[str, Any]]:
    if not enabled:
        return observations, {
            "enabled": False,
            "reason": "disabled",
            "before_count": int(len(observations)),
            "after_count": int(len(observations)),
            "dropped_out_of_bounds": 0,
            "dropped_unprojectable": 0,
        }
    if not observations:
        return observations, {
            "enabled": True,
            "reason": "no_observations",
            "before_count": 0,
            "after_count": 0,
            "dropped_out_of_bounds": 0,
            "dropped_unprojectable": 0,
        }

    margin = max(0.0, float(margin_m))
    min_x = -margin
    max_x = max(0.0, float(field_length_m)) + margin
    min_y = -margin
    max_y = max(0.0, float(field_width_m)) + margin

    filtered: list[dict] = []
    dropped_out_of_bounds = 0
    dropped_unprojectable = 0
    for row in observations:
        field_x = row.get("field_x")
        field_y = row.get("field_y")
        if field_x is None or field_y is None:
            if drop_unprojectable:
                dropped_unprojectable += 1
                continue
            filtered.append(row)
            continue
        x = float(field_x)
        y = float(field_y)
        if x < min_x or x > max_x or y < min_y or y > max_y:
            dropped_out_of_bounds += 1
            continue
        filtered.append(row)

    return filtered, {
        "enabled": True,
        "margin_m": _round(margin, 4),
        "drop_unprojectable": bool(drop_unprojectable),
        "field_bounds_m": {
            "min_x": _round(min_x, 4),
            "max_x": _round(max_x, 4),
            "min_y": _round(min_y, 4),
            "max_y": _round(max_y, 4),
        },
        "before_count": int(len(observations)),
        "after_count": int(len(filtered)),
        "dropped_out_of_bounds": int(dropped_out_of_bounds),
        "dropped_unprojectable": int(dropped_unprojectable),
    }

def _build_track_summaries(observations: list[dict]) -> dict[int, dict]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for observation in observations:
        grouped[int(observation["track_id"])].append(observation)

    summaries: dict[int, dict] = {}
    for track_id, points in grouped.items():
        points.sort(key=lambda row: row["frame_index"])
        field_points = [
            (float(row["field_x"]), float(row["field_y"]), float(row["time_sec"]))
            for row in points
            if row["field_x"] is not None and row["field_y"] is not None
        ]
        total_distance_m = 0.0
        for index in range(1, len(field_points)):
            x0, y0, _ = field_points[index - 1]
            x1, y1, _ = field_points[index]
            total_distance_m += math.hypot(x1 - x0, y1 - y0)

        if field_points:
            avg_field_x = sum(item[0] for item in field_points) / len(field_points)
            avg_field_y = sum(item[1] for item in field_points) / len(field_points)
        else:
            avg_field_x = None
            avg_field_y = None
        avg_image_x = sum(float(row["centroid_x"]) for row in points) / len(points)
        avg_image_y = sum(float(row["centroid_y"]) for row in points) / len(points)

        duration_sec = max(0.0, float(points[-1]["time_sec"]) - float(points[0]["time_sec"]))
        avg_speed_mps = (total_distance_m / duration_sec) if duration_sec > 0 else None

        summaries[track_id] = {
            "track_id": track_id,
            "observation_count": len(points),
            "start_time_sec": float(points[0]["time_sec"]),
            "end_time_sec": float(points[-1]["time_sec"]),
            "duration_sec": _round(duration_sec, 4),
            "avg_field_x": _round(avg_field_x, 4) if avg_field_x is not None else None,
            "avg_field_y": _round(avg_field_y, 4) if avg_field_y is not None else None,
            "avg_image_x": _round(avg_image_x, 4),
            "avg_image_y": _round(avg_image_y, 4),
            "distance_m": _round(total_distance_m, 4),
            "avg_speed_mps": _round(avg_speed_mps, 4) if avg_speed_mps is not None else None,
        }

    return summaries

def _compute_field_speeds(observations: list[dict]) -> list[dict]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for observation in observations:
        grouped[int(observation["track_id"])].append(observation)

    for points in grouped.values():
        points.sort(key=lambda row: row["frame_index"])
        previous = None
        for row in points:
            row["speed_mps"] = None
            if previous is None:
                previous = row
                continue
            x0 = previous.get("field_x")
            y0 = previous.get("field_y")
            x1 = row.get("field_x")
            y1 = row.get("field_y")
            if x0 is None or y0 is None or x1 is None or y1 is None:
                previous = row
                continue
            dt = max(1e-6, float(row["time_sec"]) - float(previous["time_sec"]))
            speed = math.hypot(float(x1) - float(x0), float(y1) - float(y0)) / dt
            row["speed_mps"] = _round(speed, 4)
            previous = row
    return observations

def _assign_tracks_to_teams(
    track_summaries: dict[int, dict],
    match_teams: list[models.MatchTeam],
    field_mid_x: float,
    *,
    ocr_track_hints: dict[int, dict] | None = None,
) -> tuple[dict[int, str], dict]:
    alliance_teams: dict[str, list[models.MatchTeam]] = {
        "red": sorted(
            [row for row in match_teams if row.alliance == "red"],
            key=lambda row: _station_sort_key(row.station),
        ),
        "blue": sorted(
            [row for row in match_teams if row.alliance == "blue"],
            key=lambda row: _station_sort_key(row.station),
        ),
    }

    candidate_tracks = [
        summary
        for summary in track_summaries.values()
        if summary["observation_count"] >= MIN_TRACK_OBS_FOR_ASSIGNMENT and summary["avg_field_x"] is not None
    ]

    team_alliance_map = {row.team_key: row.alliance for row in match_teams}
    assigned: dict[int, str] = {}
    assigned_teams: set[str] = set()
    assignment_sources: dict[int, str] = {}
    ocr_preassigned = 0

    if ocr_track_hints:
        ranked_hints = sorted(
            (
                (int(track_id), hint)
                for track_id, hint in ocr_track_hints.items()
                if int(track_id) in track_summaries and isinstance(hint, dict)
            ),
            key=lambda row: (
                -float(row[1].get("score") or 0.0),
                -int(row[1].get("support_count") or 0),
                row[0],
            ),
        )
        for track_id, hint in ranked_hints:
            team_key = str(hint.get("team_key") or "")
            if not team_key or team_key not in team_alliance_map:
                continue
            if team_key in assigned_teams:
                continue
            summary = track_summaries.get(track_id)
            if not summary:
                continue
            avg_field_x = summary.get("avg_field_x")
            team_alliance = team_alliance_map.get(team_key)
            if avg_field_x is not None and team_alliance in {"red", "blue"}:
                inferred_alliance = "red" if float(avg_field_x) <= float(field_mid_x) else "blue"
                if inferred_alliance != team_alliance:
                    continue
            assigned[track_id] = team_key
            assigned_teams.add(team_key)
            assignment_sources[track_id] = "ocr"
            ocr_preassigned += 1

    for alliance, teams in alliance_teams.items():
        available_teams = [team for team in teams if team.team_key not in assigned_teams]
        if not available_teams:
            continue
        alliance_tracks = [
            summary
            for summary in candidate_tracks
            if int(summary["track_id"]) not in assigned
            if (summary["avg_field_x"] <= field_mid_x and alliance == "red")
            or (summary["avg_field_x"] > field_mid_x and alliance == "blue")
        ]
        alliance_tracks.sort(
            key=lambda row: (
                row["avg_field_y"] if row["avg_field_y"] is not None else 1e9,
                -row["observation_count"],
            )
        )

        for idx, summary in enumerate(alliance_tracks[: len(available_teams)]):
            assigned[int(summary["track_id"])] = available_teams[idx].team_key
            assigned_teams.add(available_teams[idx].team_key)
            assignment_sources[int(summary["track_id"])] = "spatial"

    mapped_tracks = [track_summaries[track_id] for track_id in assigned]
    for summary in candidate_tracks:
        track_id = int(summary["track_id"])
        if track_id in assigned:
            continue
        if not mapped_tracks or summary["avg_field_x"] is None or summary["avg_field_y"] is None:
            continue

        nearest_track: dict | None = None
        nearest_distance: float | None = None
        for mapped in mapped_tracks:
            if mapped["avg_field_x"] is None or mapped["avg_field_y"] is None:
                continue
            distance = math.hypot(
                float(summary["avg_field_x"]) - float(mapped["avg_field_x"]),
                float(summary["avg_field_y"]) - float(mapped["avg_field_y"]),
            )
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
                nearest_track = mapped
        if nearest_track is not None and nearest_distance is not None and nearest_distance <= 2.3:
            team_key = assigned.get(int(nearest_track["track_id"]))
            if team_key:
                assigned[track_id] = team_key
                assignment_sources[track_id] = "nearest"

    total_observations = sum(summary["observation_count"] for summary in track_summaries.values())
    assigned_observations = sum(
        summary["observation_count"]
        for track_id, summary in track_summaries.items()
        if track_id in assigned
    )
    coverage_ratio = (assigned_observations / total_observations) if total_observations > 0 else 0.0

    if coverage_ratio < 0.35:
        all_tracks = sorted(
            track_summaries.values(),
            key=lambda row: (-row["observation_count"], row["track_id"]),
        )
        max_image_x = max((float(track["avg_image_x"]) for track in all_tracks), default=0.0)
        min_image_x = min((float(track["avg_image_x"]) for track in all_tracks), default=0.0)
        image_mid_x = (min_image_x + max_image_x) / 2.0

        fallback_assigned: dict[int, str] = {}
        for alliance, teams in alliance_teams.items():
            available_teams = [team for team in teams if team.team_key not in fallback_assigned.values()]
            if not available_teams:
                continue
            alliance_tracks = [
                track
                for track in all_tracks
                if (track["avg_image_x"] <= image_mid_x and alliance == "red")
                or (track["avg_image_x"] > image_mid_x and alliance == "blue")
            ]
            alliance_tracks.sort(
                key=lambda row: (
                    row["avg_image_y"],
                    -row["observation_count"],
                )
            )
            for idx, track in enumerate(alliance_tracks[: len(available_teams)]):
                fallback_assigned[int(track["track_id"])] = available_teams[idx].team_key

        if len(fallback_assigned) >= len(assigned):
            assigned = fallback_assigned
            assignment_sources = {track_id: "image_fallback" for track_id in fallback_assigned}
            ocr_preassigned = sum(
                1
                for track_id, team_key in assigned.items()
                if track_id in (ocr_track_hints or {}) and (ocr_track_hints or {}).get(track_id, {}).get("team_key") == team_key
            )

    assignment_meta = {
        "assigned_track_count": len(assigned),
        "ocr_preassigned_track_count": int(ocr_preassigned),
        "assignment_sources": assignment_sources,
        "coverage_ratio": _round(float(coverage_ratio), 4),
    }
    return assigned, assignment_meta

def _detect_defensive_interactions(
    observations_by_team: dict[str, list[dict]],
    match_teams: list[models.MatchTeam],
    duration_sec: float,
    sample_interval_sec: float,
) -> dict[str, dict[str, Any]]:
    team_to_alliance = {mt.team_key: mt.alliance for mt in match_teams}

    # Pre-sort each team's valid observations by time; keep parallel time list for bisect.
    sorted_by_team: dict[str, tuple[list[float], list[dict]]] = {}
    for tk, obs_list in observations_by_team.items():
        valid = sorted(
            [o for o in obs_list if o.get("field_x") is not None and o.get("field_y") is not None],
            key=lambda o: float(o["time_sec"]),
        )
        times = [float(o["time_sec"]) for o in valid]
        sorted_by_team[tk] = (times, valid)

    proximity_threshold_m = 2.0
    time_window_sec = 0.75
    empty: dict[str, Any] = {
        "proximity_engagement_sec": 0.0,
        "velocity_interrupt_events": 0,
        "blocking_potential_score": 0.0,
    }

    defensive_metrics: dict[str, dict[str, Any]] = {}

    for team_key, observations in observations_by_team.items():
        alliance = team_to_alliance.get(team_key)
        if not alliance or alliance not in {"red", "blue"}:
            continue

        _, valid_obs = sorted_by_team.get(team_key, ([], []))
        if not valid_obs:
            defensive_metrics[team_key] = dict(empty)
            continue

        # Build opponent index once per team (only cross-alliance).
        opp_index: list[tuple[list[float], list[dict]]] = [
            sorted_by_team[tk]
            for tk in observations_by_team
            if tk != team_key and team_to_alliance.get(tk) not in {None, alliance}
            and tk in sorted_by_team
        ]
        if not opp_index:
            defensive_metrics[team_key] = dict(empty)
            continue

        proximity_sec = 0.0
        velocity_interrupt_events = 0
        blocking_events = 0
        in_proximity = False

        for obs_idx, obs in enumerate(valid_obs):
            obs_time = float(obs["time_sec"])
            obs_x = float(obs["field_x"])
            obs_y = float(obs["field_y"])
            obs_speed = float(obs.get("speed_mps") or 0.0)

            min_distance = float("inf")
            nearest_opp_speed = 0.0

            for opp_times, opp_obs in opp_index:
                lo = bisect.bisect_left(opp_times, obs_time - time_window_sec)
                hi = bisect.bisect_right(opp_times, obs_time + time_window_sec)
                for opp in opp_obs[lo:hi]:
                    d = math.hypot(obs_x - float(opp["field_x"]), obs_y - float(opp["field_y"]))
                    if d < min_distance:
                        min_distance = d
                        nearest_opp_speed = float(opp.get("speed_mps") or 0.0)

            currently_close = min_distance < proximity_threshold_m
            if currently_close != in_proximity:
                in_proximity = currently_close
            if currently_close:
                proximity_sec += sample_interval_sec

            if currently_close and obs_idx > 0:
                prev_speed = float(valid_obs[obs_idx - 1].get("speed_mps") or 0.0)
                if prev_speed > 0.8 and obs_speed < 0.3:
                    velocity_interrupt_events += 1

            if min_distance < 1.5 and obs_speed > nearest_opp_speed + 0.3:
                blocking_events += 1

        defensive_metrics[team_key] = {
            "proximity_engagement_sec": _round(min(float(duration_sec), proximity_sec), 3),
            "velocity_interrupt_events": int(velocity_interrupt_events),
            "blocking_potential_score": _round(_clamp(float(blocking_events) / max(1, len(valid_obs)), 0.0, 1.0), 4),
        }

    return defensive_metrics

def _ev(
    row: dict,
    event_type: str,
    confidence: float,
    meta: dict,
    *,
    team_key: str,
    time_sec: float | None = None,
) -> dict:
    return {
        "team_key": team_key,
        "track_id": row["track_id"],
        "frame_index": row["frame_index"],
        "time_sec": time_sec if time_sec is not None else row["time_sec"],
        "event_type": event_type,
        "confidence": confidence,
        "field_x": row.get("field_x"),
        "field_y": row.get("field_y"),
        "meta": meta,
    }

def _inference_source(model_label: str, target_event: str, heuristic_hit: bool) -> str:
    if model_label != target_event:
        return "heuristic"
    return "hybrid" if heuristic_hit else "model"

def _synthetic_time_in_windows(
    offset_sec: float,
    windows: list[tuple[float, float]],
    duration_sec: float,
) -> float:
    remaining = max(0.0, float(offset_sec))
    for start, end in windows:
        span = max(0.0, float(end) - float(start))
        if remaining <= span:
            return _round(float(start) + remaining, 3)
        remaining -= span
    last_end = windows[-1][1] if windows else duration_sec
    return _round(min(float(duration_sec), float(last_end)), 3)

def _compute_team_metrics_and_events(
    team_key: str,
    observations: list[dict],
    duration_sec: float,
    auto_window_sec: float,
    endgame_window_sec: float,
    sample_interval_sec: float,
    event_model: TransitionEventModel | None = None,
    event_model_threshold: float = 0.58,
    event_model_margin_threshold: float = 0.06,
    event_model_prefer_model: bool = True,
    active_scoring_windows_sec: list[tuple[float, float]] | None = None,
    active_scoring_duration_sec: float | None = None,
) -> tuple[dict, list[dict], dict]:
    if not observations:
        metrics = {
            "fuel_scoring_rate": 0.0,
            "cycle_time_sec": None,
            "auto_contribution": 0.0,
            "climb_success_prob": 0.0,
            "defensive_engagement_sec": 0.0,
            "reliability_score": 0.0,
        }
        return metrics, [], {
            "enabled": bool(event_model),
            "predictions": 0,
            "applied": 0,
            "fallback_heuristic": 0,
            "model_version": (event_model.version if event_model else None),
            "threshold": _round(event_model_threshold, 4),
            "margin_threshold": _round(event_model_margin_threshold, 4),
        }

    observations.sort(key=lambda row: row["time_sec"])
    endgame_start_sec = max(0.0, duration_sec - endgame_window_sec)
    resolved_active_scoring_windows = (
        _merge_time_windows(
            list(active_scoring_windows_sec),
            floor_sec=max(0.0, auto_window_sec),
            ceil_sec=max(0.0, duration_sec) + REBUILT_HUB_SCORE_GRACE_SEC,
        )
        if active_scoring_windows_sec
        else None
    )
    events: list[dict] = []
    zone_time: dict[str, float] = defaultdict(float)
    loading_entries: list[float] = []
    scoring_entries: list[float] = []
    climb_entries: list[float] = []
    event_inference_meta: dict[str, Any] = {
        "enabled": bool(event_model),
        "predictions": 0,
        "applied": 0,
        "fallback_heuristic": 0,
        "model_version": (event_model.version if event_model else None),
        "threshold": _round(event_model_threshold, 4),
        "margin_threshold": _round(event_model_margin_threshold, 4),
    }

    auto_points = [
        row for row in observations
        if row["time_sec"] <= auto_window_sec and row["field_x"] is not None and row["field_y"] is not None
    ]
    auto_distance_m = 0.0
    for index in range(1, len(auto_points)):
        auto_distance_m += math.hypot(
            float(auto_points[index]["field_x"]) - float(auto_points[index - 1]["field_x"]),
            float(auto_points[index]["field_y"]) - float(auto_points[index - 1]["field_y"]),
        )

    if auto_distance_m >= 1.2:
        first_auto = auto_points[0] if auto_points else observations[0]
        events.append(_ev(first_auto, "auto_mobility", 0.8, {"distance_m": _round(auto_distance_m)}, team_key=team_key))

    last_zone_key = observations[0].get("zone_key")
    segment_start = float(observations[0]["time_sec"])
    if last_zone_key:
        events.append(_ev(
            observations[0], "zone_entry", 0.7,
            {"zone_key": last_zone_key, "zone_kind": observations[0].get("zone_kind")},
            team_key=team_key,
        ))

    for index in range(1, len(observations)):
        row = observations[index]
        current_zone = row.get("zone_key")
        if current_zone == last_zone_key:
            continue

        now = float(row["time_sec"])
        if last_zone_key:
            duration = max(0.0, now - segment_start)
            zone_time[last_zone_key] += duration
            events.append(_ev(
                row, "zone_dwell", 0.65,
                {"zone_key": last_zone_key, "duration_sec": _round(duration)},
                team_key=team_key, time_sec=now,
            ))

        if current_zone or event_model is not None:
            zone_kind = row.get("zone_kind")
            is_endgame_zone = _is_endgame_zone(current_zone, zone_kind)
            prediction_label = "none"
            prediction_conf = 0.0
            prediction_margin = 0.0
            prediction_threshold = event_model_threshold
            prediction_margin_threshold = event_model_margin_threshold
            if event_model is not None:
                prev_row = observations[index - 1]
                feature_map = build_transition_feature_map(
                    prev_row,
                    row,
                    duration_sec=duration_sec,
                    auto_window_sec=auto_window_sec,
                    endgame_start_sec=endgame_start_sec,
                )
                prediction = predict_transition_event(event_model, feature_map)
                prediction_label = str(prediction.get("label") or "none")
                prediction_conf = float(prediction.get("confidence") or 0.0)
                prediction_margin = float(prediction.get("top_2_margin") or 0.0)
                decision = resolve_model_event_label(
                    event_model,
                    prediction,
                    default_conf_threshold=event_model_threshold,
                    default_margin_threshold=event_model_margin_threshold,
                )
                prediction_threshold = float(decision.get("confidence_threshold") or event_model_threshold)
                prediction_margin_threshold = float(decision.get("margin_threshold") or event_model_margin_threshold)
                model_label = str(decision.get("label") or "none")
                event_inference_meta["predictions"] = int(event_inference_meta.get("predictions", 0)) + 1
            else:
                model_label = "none"

            if current_zone:
                events.append(_ev(
                    row, "zone_entry", 0.72,
                    {
                        "zone_key": current_zone,
                        "zone_kind": zone_kind,
                        "event_model_label": model_label if model_label != "none" else None,
                        "event_model_confidence": (_round(prediction_conf, 4) if prediction_conf > 0 else None),
                        "event_model_threshold": (_round(prediction_threshold, 4) if prediction_conf > 0 else None),
                        "event_model_margin": (_round(prediction_margin, 4) if prediction_margin > 0 else None),
                        "event_model_margin_threshold": (
                            _round(prediction_margin_threshold, 4) if prediction_conf > 0 else None
                        ),
                    },
                    team_key=team_key, time_sec=now,
                ))

            if (
                (zone_kind == "loading")
                or (model_label == "depot_intake" and (event_model_prefer_model or zone_kind != "loading"))
            ):
                loading_entries.append(now)
                if model_label == "depot_intake":
                    event_inference_meta["applied"] = int(event_inference_meta.get("applied", 0)) + 1
                elif prediction_label != "none":
                    event_inference_meta["fallback_heuristic"] = int(event_inference_meta.get("fallback_heuristic", 0)) + 1
                events.append(_ev(
                    row, "depot_intake",
                    _round(max(0.74, prediction_conf), 4) if model_label == "depot_intake" else 0.74,
                    {
                        "zone_key": current_zone,
                        "inference_source": _inference_source(model_label, "depot_intake", zone_kind == "loading"),
                        "event_model_margin": (_round(prediction_margin, 4) if prediction_margin > 0 else None),
                    },
                    team_key=team_key, time_sec=now,
                ))
            elif (
                now > auto_window_sec
                and (
                    (zone_kind == "scoring")
                    or (
                        model_label == "teleop_fuel_score_attempt"
                        and (event_model_prefer_model or zone_kind != "scoring")
                    )
                )
            ):
                scoring_entries.append(now)
                if model_label == "teleop_fuel_score_attempt":
                    event_inference_meta["applied"] = int(event_inference_meta.get("applied", 0)) + 1
                elif prediction_label != "none":
                    event_inference_meta["fallback_heuristic"] = int(event_inference_meta.get("fallback_heuristic", 0)) + 1
                events.append(_ev(
                    row, "teleop_fuel_score_attempt",
                    _round(max(0.68, prediction_conf), 4) if model_label == "teleop_fuel_score_attempt" else 0.68,
                    {
                        "zone_key": current_zone,
                        "inference_source": _inference_source(model_label, "teleop_fuel_score_attempt", zone_kind == "scoring"),
                        "hub_active_window": _time_in_windows(now, resolved_active_scoring_windows),
                        "event_model_margin": (_round(prediction_margin, 4) if prediction_margin > 0 else None),
                    },
                    team_key=team_key, time_sec=now,
                ))
            elif (
                now >= endgame_start_sec
                and (
                    is_endgame_zone
                    or (model_label == "climb_attempt" and (event_model_prefer_model or not is_endgame_zone))
                )
            ):
                climb_entries.append(now)
                if model_label == "climb_attempt":
                    event_inference_meta["applied"] = int(event_inference_meta.get("applied", 0)) + 1
                elif prediction_label != "none":
                    event_inference_meta["fallback_heuristic"] = int(event_inference_meta.get("fallback_heuristic", 0)) + 1
                events.append(_ev(
                    row, "climb_attempt",
                    _round(max(0.62, prediction_conf), 4) if model_label == "climb_attempt" else 0.62,
                    {
                        "zone_key": current_zone,
                        "inference_source": _inference_source(model_label, "climb_attempt", is_endgame_zone),
                        "event_model_margin": (_round(prediction_margin, 4) if prediction_margin > 0 else None),
                    },
                    team_key=team_key, time_sec=now,
                ))
            elif (
                (zone_kind == "protected")
                or (
                    model_label == "protected_zone_interference"
                    and (event_model_prefer_model or zone_kind != "protected")
                )
            ):
                if model_label == "protected_zone_interference":
                    event_inference_meta["applied"] = int(event_inference_meta.get("applied", 0)) + 1
                elif prediction_label != "none":
                    event_inference_meta["fallback_heuristic"] = int(event_inference_meta.get("fallback_heuristic", 0)) + 1
                events.append(_ev(
                    row, "protected_zone_interference",
                    _round(max(0.52, prediction_conf), 4) if model_label == "protected_zone_interference" else 0.52,
                    {
                        "zone_key": current_zone,
                        "inference_source": _inference_source(model_label, "protected_zone_interference", zone_kind == "protected"),
                        "inferred": True,
                        "event_model_margin": (_round(prediction_margin, 4) if prediction_margin > 0 else None),
                    },
                    team_key=team_key, time_sec=now,
                ))

        last_zone_key = current_zone
        segment_start = now

    if last_zone_key:
        tail_duration = max(0.0, duration_sec - segment_start)
        zone_time[last_zone_key] += tail_duration
        events.append(_ev(
            observations[-1], "zone_dwell", 0.6,
            {"zone_key": last_zone_key, "duration_sec": _round(tail_duration)},
            team_key=team_key,
        ))

    scoring_entries.sort()
    loading_entries.sort()
    teleop_duration = max(1e-6, duration_sec - auto_window_sec)
    active_scoring_entries = [value for value in scoring_entries if _time_in_windows(value, resolved_active_scoring_windows)]
    active_loading_entries = [value for value in loading_entries if _time_in_windows(value, resolved_active_scoring_windows)]
    active_duration_fallback = (
        _window_duration_sec(resolved_active_scoring_windows)
        if resolved_active_scoring_windows
        else teleop_duration
    )
    active_duration_for_metrics = (
        max(1e-6, float(active_scoring_duration_sec))
        if isinstance(active_scoring_duration_sec, (int, float)) and float(active_scoring_duration_sec) > 0.0
        else max(1e-6, float(active_duration_fallback))
    )

    cycle_times: list[float] = []
    max_cycle_pair_sec = max(8.0, min(70.0, float(active_duration_for_metrics) * 0.75))
    pairing_windows = (
        resolved_active_scoring_windows
        if resolved_active_scoring_windows
        else [(max(0.0, auto_window_sec), max(0.0, duration_sec))]
    )
    for window_start, window_end in pairing_windows:
        window_loadings = [
            value for value in active_loading_entries
            if window_start <= value <= window_end
        ]
        window_scores = [
            value for value in active_scoring_entries
            if window_start <= value <= window_end
        ]
        if not window_loadings or not window_scores:
            continue
        score_idx = 0
        for loading_time in window_loadings:
            while score_idx < len(window_scores) and window_scores[score_idx] <= loading_time:
                score_idx += 1
            if score_idx >= len(window_scores):
                break
            cycle_delta = window_scores[score_idx] - loading_time
            score_idx += 1
            if cycle_delta <= 0.0 or cycle_delta > max_cycle_pair_sec:
                continue
            cycle_times.append(cycle_delta)

    team_distance_m = 0.0
    for index in range(1, len(observations)):
        x0 = observations[index - 1].get("field_x")
        y0 = observations[index - 1].get("field_y")
        x1 = observations[index].get("field_x")
        y1 = observations[index].get("field_y")
        if x0 is None or y0 is None or x1 is None or y1 is None:
            continue
        team_distance_m += math.hypot(float(x1) - float(x0), float(y1) - float(y0))

    cycle_time_sec = _round(sum(cycle_times) / len(cycle_times), 3) if cycle_times else None
    inferred_cycles = max(0, int(team_distance_m / 7.2))
    if not active_scoring_entries and inferred_cycles > 0:
        synthetic_score_count = max(1, inferred_cycles // 2)
        synthetic_windows = (
            _merge_time_windows(
                list(resolved_active_scoring_windows),
                floor_sec=max(0.0, auto_window_sec),
                ceil_sec=max(0.0, duration_sec),
            )
            if resolved_active_scoring_windows
            else [(max(0.0, auto_window_sec), max(0.0, duration_sec))]
        )
        synthetic_window_duration = max(1e-6, _window_duration_sec(synthetic_windows))

        for synthetic_idx in range(synthetic_score_count):
            synthetic_offset = ((synthetic_idx + 1) * synthetic_window_duration) / (synthetic_score_count + 1)
            synthetic_time = _synthetic_time_in_windows(synthetic_offset, synthetic_windows, duration_sec)
            scoring_entries.append(synthetic_time)
            active_scoring_entries.append(synthetic_time)
            events.append(_ev(
                observations[0], "teleop_fuel_score_attempt", 0.45,
                {"synthetic": True, "reason": "distance_based_inference", "hub_active_window": True},
                team_key=team_key, time_sec=synthetic_time,
            ))
        if cycle_time_sec is None and synthetic_score_count > 0:
            cycle_time_sec = _round(active_duration_for_metrics / synthetic_score_count, 3)

    # fuel_scoring_rate is normalized to "scores per minute".
    fuel_scoring_rate = _round(max(0.0, (len(active_scoring_entries) / active_duration_for_metrics) * 60.0), 3)
    defensive_engagement_sec = _round(
        sum(
            duration
            for zone_key, duration in zone_time.items()
            if "neutral" in zone_key
        ),
        3,
    )

    speed_values: list[float] = []
    burst_events = 0
    for row in observations:
        speed = row.get("speed_mps")
        if speed is None and row.get("speed_px") is not None:
            speed = float(row["speed_px"]) / 45.0
        if speed is None:
            continue
        speed = float(speed)
        speed_values.append(speed)
        if speed > 1.6 and burst_events < 4:
            events.append(_ev(
                row, "speed_burst", 0.58,
                {"speed_mps": _round(speed, 3)},
                team_key=team_key,
            ))
            burst_events += 1
    moving_ratio = (sum(1 for s in speed_values if s > 0.35) / len(speed_values)) if speed_values else 0.0

    expected_samples = max(1.0, duration_sec / max(0.1, float(sample_interval_sec)))
    coverage_ratio = min(1.0, len(observations) / expected_samples)
    reliability_score = _round(_clamp((0.55 * coverage_ratio) + (0.45 * moving_ratio), 0.0, 1.0), 3)
    if reliability_score < 0.12:
        last = observations[-1]
        events.append(_ev(
            last, "robot_disabled", 0.61,
            {"moving_ratio": _round(moving_ratio, 3), "coverage_ratio": _round(coverage_ratio, 3)},
            team_key=team_key,
        ))

    auto_contribution = _round(
        _clamp(
            (auto_distance_m * 0.85) + (len([time for time in scoring_entries if time <= auto_window_sec]) * 2.5),
            0.0,
            18.0,
        ),
        3,
    )

    # Fallback: detect endgame tower dwell even when no explicit endgame zone transition event fired.
    endgame_tower_dwell = 0.0
    for index in range(1, len(observations)):
        prev = observations[index - 1]
        curr = observations[index]
        seg_start = max(float(prev["time_sec"]), endgame_start_sec)
        seg_end = max(float(curr["time_sec"]), endgame_start_sec)
        if seg_end <= seg_start:
            continue
        prev_zone = str(prev.get("zone_key") or "").lower()
        curr_zone = str(curr.get("zone_key") or "").lower()
        prev_kind = str(prev.get("zone_kind") or "").lower()
        curr_kind = str(curr.get("zone_kind") or "").lower()
        if _is_endgame_zone(prev_zone, prev_kind) or _is_endgame_zone(curr_zone, curr_kind):
            endgame_tower_dwell += seg_end - seg_start

    last_row = observations[-1]
    last_zone = str(last_row.get("zone_key") or "").lower()
    last_kind = str(last_row.get("zone_kind") or "").lower()
    if duration_sec > endgame_start_sec and _is_endgame_zone(last_zone, last_kind):
        tail_start = max(float(last_row["time_sec"]), endgame_start_sec)
        if duration_sec > tail_start:
            endgame_tower_dwell += duration_sec - tail_start

    endgame_tower_dwell = _round(endgame_tower_dwell, 3)
    if not climb_entries and endgame_tower_dwell >= 2.0:
        climb_entries.append(_round(endgame_start_sec, 3))
        events.append(_ev(
            last_row, "climb_attempt", 0.52,
            {"inferred": True, "tower_dwell_sec": endgame_tower_dwell},
            team_key=team_key, time_sec=_round(endgame_start_sec, 3),
        ))

    climb_success = False
    if climb_entries:
        required_tower_dwell = max(3.0, min(6.0, float(endgame_window_sec) * 0.16))
        if endgame_tower_dwell >= required_tower_dwell:
            climb_success = True
            last = observations[-1]
            events.append(_ev(
                last, "climb_success", 0.78,
                {"tower_dwell_sec": endgame_tower_dwell},
                team_key=team_key, time_sec=_round(duration_sec, 3),
            ))

    climb_success_prob = 1.0 if climb_success else (0.35 if climb_entries else 0.0)

    metrics = {
        "fuel_scoring_rate": fuel_scoring_rate,
        "cycle_time_sec": cycle_time_sec,
        "auto_contribution": auto_contribution,
        "climb_success_prob": _round(climb_success_prob, 3),
        "defensive_engagement_sec": defensive_engagement_sec,
        "reliability_score": reliability_score,
    }
    event_inference_meta["events_total"] = len(events)
    event_inference_meta["model_applied_ratio"] = _round(
        float(event_inference_meta.get("applied", 0)) / max(1.0, float(event_inference_meta.get("predictions", 0))),
        4,
    )
    return metrics, events, event_inference_meta

def _resolve_phase_window_seconds(
    db,
    match: models.Match,
    *,
    duration_sec: float,
) -> dict:
    auto_end_default = min(20.0, duration_sec)
    endgame_start_default = max(auto_end_default, duration_sec - 30.0)
    teleop_scoring_start_default = auto_end_default
    teleop_scoring_end_default = endgame_start_default
    default_confidence = 0.8
    phase_source = "duration_fallback"

    try:
        phase_payload = get_or_compute_match_phase_windows(db, match, refresh=False)
        windows = phase_payload.get("windows") if isinstance(phase_payload, dict) else {}
        windows = windows if isinstance(windows, dict) else {}
        phase_source = str(phase_payload.get("source") or "game_config")
        auto_window = windows.get("auto") if isinstance(windows.get("auto"), dict) else {}
        teleop_scoring_window = (
            windows.get("teleop_scoring")
            if isinstance(windows.get("teleop_scoring"), dict)
            else {}
        )
        endgame_window = windows.get("endgame") if isinstance(windows.get("endgame"), dict) else {}
        auto_end = float(auto_window.get("end_sec") or auto_end_default)
        teleop_scoring_start = float(teleop_scoring_window.get("start_sec") or teleop_scoring_start_default)
        teleop_scoring_end = float(teleop_scoring_window.get("end_sec") or teleop_scoring_end_default)
        endgame_start = float(endgame_window.get("start_sec") or endgame_start_default)
        phase_confidence = float(
            teleop_scoring_window.get("confidence_0_1")
            or endgame_window.get("confidence_0_1")
            or default_confidence
        )
    except Exception:
        auto_end = auto_end_default
        teleop_scoring_start = teleop_scoring_start_default
        teleop_scoring_end = teleop_scoring_end_default
        endgame_start = endgame_start_default
        phase_confidence = default_confidence

    auto_end = _clamp(auto_end, 0.0, duration_sec)
    endgame_start = _clamp(endgame_start, auto_end, duration_sec)
    teleop_scoring_start = _clamp(teleop_scoring_start, auto_end, endgame_start)
    teleop_scoring_end = _clamp(teleop_scoring_end, teleop_scoring_start, endgame_start)

    return {
        "auto_end_sec": _round(auto_end, 3),
        "endgame_start_sec": _round(endgame_start, 3),
        "endgame_window_sec": _round(max(0.0, duration_sec - endgame_start), 3),
        "teleop_scoring_start_sec": _round(teleop_scoring_start, 3),
        "teleop_scoring_end_sec": _round(teleop_scoring_end, 3),
        "teleop_scoring_duration_sec": _round(max(0.0, teleop_scoring_end - teleop_scoring_start), 3),
        "source": phase_source,
        "confidence_0_1": _round(_clamp(phase_confidence, 0.0, 1.0), 4),
    }

