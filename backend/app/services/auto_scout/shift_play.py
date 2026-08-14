from __future__ import annotations

# Shift-based offense/defense analysis engine.
#
# The 2026 REBUILT match alternates which alliance's hub is active (see the
# shift_schedule in game_config). During a robot's active shift it is expected to
# attack; during the opponent's active shift it should gather fuel or play defense.
# This engine reads that intent out of positional tracking: per robot, per shift,
# it scores offense and defense and produces attack/defense heat maps.
#
# It is deliberately decoupled from the ORM (operates on plain TrackPoint lists) so
# the identical logic can run server-side over RobotTrack rows AND, later, on-device
# in the PWA over locally-produced tracks.

import bisect
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.game_config.schema import ShiftSchedule
from app.services.game_config import load_game_config

# On-device PWA sync identifiers (single source of truth). Tracks/runs produced by
# the offline PWA are unauthenticated and best-effort, so authoritative server
# views (shift-play, heatmaps, replayer) must be able to exclude them by these.
ON_DEVICE_SOURCE = "on_device_pwa_v1"  # nosec B105 - not a secret
ON_DEVICE_ANALYSIS_VERSION = "on_device_pwa_v1"  # nosec B105 - not a secret

# Heat-map grid resolution (columns x rows across the field).
GRID_X = 12
GRID_Y = 6
# Largest gap between two samples we still credit as continuous dwell.
MAX_DWELL_DT_SEC = 2.0
# A defender is "on" an opponent if within this distance at ~the same instant.
SHADOW_DISTANCE_M = 1.5
SHADOW_TIME_TOL_SEC = 0.6


@dataclass(slots=True)
class TrackPoint:
    time_sec: float
    field_x: float | None
    field_y: float | None
    zone_key: str | None
    speed_mps: float | None = None


@dataclass(slots=True)
class RobotShiftPlay:
    team_key: str
    alliance: str
    offense_level_1_5: int
    offense_confidence: float
    defense_level_1_5: int
    defense_confidence: float
    defense_assessable: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    shift_breakdown: list[dict[str, Any]] = field(default_factory=list)
    attack_heatmap: list[list[int]] = field(default_factory=list)
    defense_heatmap: list[list[int]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_key": self.team_key,
            "alliance": self.alliance,
            "offense": {
                "level_1_5": self.offense_level_1_5,
                "confidence_0_1": self.offense_confidence,
            },
            "defense": {
                "level_1_5": self.defense_level_1_5,
                "confidence_0_1": self.defense_confidence,
                "assessable": self.defense_assessable,
            },
            "metrics": self.metrics,
            "shift_breakdown": self.shift_breakdown,
            "heatmaps": {"attack": self.attack_heatmap, "defense": self.defense_heatmap},
            "coverage": self.coverage,
        }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _opponent(alliance: str) -> str:
    return "blue" if alliance == "red" else "red"


def _window_for(schedule: ShiftSchedule, time_sec: float):
    for window in schedule.windows:
        if window.start_sec <= time_sec < window.end_sec:
            return window
    return None


def _is_attack_eligible(active: str, alliance: str) -> bool:
    # Everyone can score during "both" windows (auto/transition/endgame).
    return active in (alliance, "both")


def _level_from_index(index: float) -> int:
    return max(1, min(5, int(round(1.0 + (4.0 * _clamp(index, 0.0, 1.0))))))


def _empty_grid() -> list[list[int]]:
    return [[0 for _ in range(GRID_X)] for _ in range(GRID_Y)]


def _bin_point(grid: list[list[int]], x: float | None, y: float | None, length_m: float, width_m: float) -> None:
    if x is None or y is None or length_m <= 0 or width_m <= 0:
        return
    gx = int(_clamp(x / length_m, 0.0, 0.999999) * GRID_X)
    gy = int(_clamp(y / width_m, 0.0, 0.999999) * GRID_Y)
    grid[gy][gx] += 1


def _dwell_segments(points: Sequence[TrackPoint]) -> list[tuple[TrackPoint, float]]:
    # Credit each point with the time until the next sample (capped), so dwell is
    # robust to variable sampling rates.
    ordered = sorted(points, key=lambda p: p.time_sec)
    segments: list[tuple[TrackPoint, float]] = []
    for i in range(len(ordered) - 1):
        dt = ordered[i + 1].time_sec - ordered[i].time_sec
        if dt <= 0:
            continue
        segments.append((ordered[i], min(dt, MAX_DWELL_DT_SEC)))
    return segments


class _OpponentIndex:
    # Time-indexed opponent positions for nearest-in-time distance lookups.
    def __init__(self, points_by_team: dict[str, list[TrackPoint]], opponent_alliance: str, alliance_by_team: dict[str, str]):
        self._times: list[float] = []
        self._coords: list[list[tuple[float, float]]] = []
        merged: dict[float, list[tuple[float, float]]] = defaultdict(list)
        for team_key, points in points_by_team.items():
            if alliance_by_team.get(team_key) != opponent_alliance:
                continue
            for p in points:
                if p.field_x is None or p.field_y is None:
                    continue
                merged[round(p.time_sec, 3)].append((p.field_x, p.field_y))
        for t in sorted(merged):
            self._times.append(t)
            self._coords.append(merged[t])

    def has_data(self) -> bool:
        return bool(self._times)

    def min_distance(self, time_sec: float, x: float, y: float) -> float | None:
        if not self._times:
            return None
        idx = bisect.bisect_left(self._times, time_sec)
        best: float | None = None
        for cand in (idx - 1, idx, idx + 1):
            if 0 <= cand < len(self._times):
                if abs(self._times[cand] - time_sec) > SHADOW_TIME_TOL_SEC:
                    continue
                for (ox, oy) in self._coords[cand]:
                    dist = math.hypot(ox - x, oy - y)
                    if best is None or dist < best:
                        best = dist
        return best

    def scoring_present(self, time_sec: float, scoring_zone_points: "_ZonePresence") -> bool:
        return scoring_zone_points.present(time_sec)


class _ZonePresence:
    # Whether any opponent robot is in a given zone at ~a timestamp (for disruption).
    def __init__(self, points_by_team: dict[str, list[TrackPoint]], opponent_alliance: str, alliance_by_team: dict[str, str], zone_key: str):
        times: set[float] = set()
        for team_key, points in points_by_team.items():
            if alliance_by_team.get(team_key) != opponent_alliance:
                continue
            for p in points:
                if p.zone_key == zone_key:
                    times.add(round(p.time_sec, 3))
        self._times = sorted(times)

    def present(self, time_sec: float) -> bool:
        if not self._times:
            return False
        idx = bisect.bisect_left(self._times, time_sec)
        for cand in (idx - 1, idx):
            if 0 <= cand < len(self._times) and abs(self._times[cand] - time_sec) <= SHADOW_TIME_TOL_SEC:
                return True
        return False


def analyze_robot_shift_play(
    *,
    team_key: str,
    alliance: str,
    points_by_team: dict[str, list[TrackPoint]],
    alliance_by_team: dict[str, str],
    schedule: ShiftSchedule | None = None,
    field_length_m: float | None = None,
    field_width_m: float | None = None,
) -> RobotShiftPlay:
    if schedule is None:
        schedule = load_game_config().shift_schedule
    if schedule is None:
        raise ValueError("shift_schedule is not configured for this season")
    if field_length_m is None or field_width_m is None:
        cfg_field = load_game_config().field
        field_length_m = field_length_m or float(cfg_field.length_m)
        field_width_m = field_width_m or float(cfg_field.width_m)

    opponent = _opponent(alliance)
    role = schedule.role_zones
    attack_zone = getattr(role.attack, alliance)
    gather_zone = getattr(role.gather, alliance)
    defense_zone = getattr(role.defense, alliance)  # == opponent's scoring zone

    points = sorted(points_by_team.get(team_key, []), key=lambda p: p.time_sec)
    segments = _dwell_segments(points)

    # Eligible window-time budgets (denominators for coverage/fractions).
    attack_eligible_sec = sum(
        (w.end_sec - w.start_sec) for w in schedule.windows if _is_attack_eligible(w.active, alliance)
    )
    opponent_active_sec = sum(
        (w.end_sec - w.start_sec) for w in schedule.windows if w.active == opponent
    )

    attack_grid = _empty_grid()
    defense_grid = _empty_grid()

    # Offense accumulators.
    attack_zone_sec = 0.0
    gather_zone_sec = 0.0
    tracked_attack_sec = 0.0
    attack_speed_weighted = 0.0
    attack_speed_time = 0.0
    cycles = 0
    seen_gather_since_attack = False
    in_attack_prev = False

    # Defense accumulators.
    opponent_zone_sec = 0.0
    shadow_sec = 0.0
    tracked_opp_sec = 0.0
    engaged_sec = 0.0
    engaged_opp_scoring_sec = 0.0
    unengaged_sec = 0.0
    unengaged_opp_scoring_sec = 0.0

    opp_index = _OpponentIndex(points_by_team, opponent, alliance_by_team)
    opp_scoring_presence = _ZonePresence(points_by_team, opponent, alliance_by_team, defense_zone)

    per_shift: dict[str, dict[str, Any]] = {}

    for point, dt in segments:
        window = _window_for(schedule, point.time_sec)
        if window is None:
            continue
        bucket = per_shift.setdefault(
            window.key,
            {"key": window.key, "active": window.active, "tracked_sec": 0.0, "zone_sec": defaultdict(float)},
        )
        bucket["tracked_sec"] += dt
        if point.zone_key:
            bucket["zone_sec"][point.zone_key] += dt

        attack_eligible = _is_attack_eligible(window.active, alliance)
        opponent_active = window.active == opponent

        if attack_eligible:
            tracked_attack_sec += dt
            _bin_point(attack_grid, point.field_x, point.field_y, field_length_m, field_width_m)
            if point.speed_mps is not None:
                attack_speed_weighted += float(point.speed_mps) * dt
                attack_speed_time += dt
            if point.zone_key == attack_zone:
                attack_zone_sec += dt
                if not in_attack_prev and seen_gather_since_attack:
                    cycles += 1
                    seen_gather_since_attack = False
                in_attack_prev = True
            else:
                in_attack_prev = False
                if point.zone_key == gather_zone:
                    gather_zone_sec += dt
                    seen_gather_since_attack = True

        if opponent_active:
            tracked_opp_sec += dt
            _bin_point(defense_grid, point.field_x, point.field_y, field_length_m, field_width_m)
            in_opp_zone = point.zone_key == defense_zone
            if in_opp_zone:
                opponent_zone_sec += dt
            shadowing = False
            if point.field_x is not None and point.field_y is not None:
                dist = opp_index.min_distance(point.time_sec, point.field_x, point.field_y)
                if dist is not None and dist <= SHADOW_DISTANCE_M:
                    shadowing = True
                    shadow_sec += dt
            engaged = in_opp_zone or shadowing
            opp_scoring = opp_scoring_presence.present(point.time_sec)
            if engaged:
                engaged_sec += dt
                if opp_scoring:
                    engaged_opp_scoring_sec += dt
            else:
                unengaged_sec += dt
                if opp_scoring:
                    unengaged_opp_scoring_sec += dt

    # ── Offense scoring ──────────────────────────────────────────────
    attack_frac = _clamp(attack_zone_sec / attack_eligible_sec) if attack_eligible_sec > 0 else 0.0
    cycles_norm = _clamp(cycles / 6.0)
    pace = (attack_speed_weighted / attack_speed_time) if attack_speed_time > 0 else 0.0
    pace_norm = _clamp(pace / 1.2)
    offense_index = (0.5 * attack_frac) + (0.3 * cycles_norm) + (0.2 * pace_norm)
    offense_level = _level_from_index(offense_index)
    attack_coverage = _clamp(tracked_attack_sec / attack_eligible_sec) if attack_eligible_sec > 0 else 0.0
    offense_confidence = round(_clamp(0.4 + 0.5 * attack_coverage, 0.0, 0.95), 4) if tracked_attack_sec > 0 else 0.0

    # ── Defense scoring ──────────────────────────────────────────────
    opp_zone_frac = _clamp(opponent_zone_sec / opponent_active_sec) if opponent_active_sec > 0 else 0.0
    shadow_frac = _clamp(shadow_sec / opponent_active_sec) if opponent_active_sec > 0 else 0.0
    engaged_rate = (engaged_opp_scoring_sec / engaged_sec) if engaged_sec > 0 else None
    unengaged_rate = (unengaged_opp_scoring_sec / unengaged_sec) if unengaged_sec > 0 else None
    if engaged_rate is not None and unengaged_rate is not None and unengaged_rate > 0.05:
        disruption = _clamp((unengaged_rate - engaged_rate) / unengaged_rate)
    else:
        disruption = 0.0
    defense_index = (0.4 * opp_zone_frac) + (0.3 * shadow_frac) + (0.3 * disruption)
    defense_level = _level_from_index(defense_index)
    defense_assessable = opponent_active_sec > 0 and tracked_opp_sec > 0
    opp_coverage = _clamp(tracked_opp_sec / opponent_active_sec) if opponent_active_sec > 0 else 0.0
    # Defense is structurally less certain (needs simultaneous opponent tracking).
    defense_confidence = 0.0
    if defense_assessable:
        base = 0.3 + 0.45 * opp_coverage
        if not opp_index.has_data():
            base = min(base, 0.5)  # no opponent tracks -> shadow/disruption blind
        defense_confidence = round(_clamp(base * 0.85, 0.0, 0.9), 4)

    # ── Shift breakdown ──────────────────────────────────────────────
    shift_breakdown: list[dict[str, Any]] = []
    for window in schedule.windows:
        bucket = per_shift.get(window.key)
        attack_eligible = _is_attack_eligible(window.active, alliance)
        opponent_active = window.active == opponent
        expected = "attack" if attack_eligible else ("defend_or_gather" if opponent_active else "idle")
        observed_zone = None
        zone_sec = {}
        tracked = 0.0
        if bucket is not None:
            zone_sec = {k: round(v, 2) for k, v in sorted(bucket["zone_sec"].items(), key=lambda kv: kv[1], reverse=True)}
            tracked = round(bucket["tracked_sec"], 2)
            if zone_sec:
                observed_zone = next(iter(zone_sec))
        shift_breakdown.append(
            {
                "key": window.key,
                "active": window.active,
                "expected_mode": expected,
                "observed_dominant_zone": observed_zone,
                "tracked_sec": tracked,
                "zone_sec": zone_sec,
            }
        )

    return RobotShiftPlay(
        team_key=team_key,
        alliance=alliance,
        offense_level_1_5=offense_level,
        offense_confidence=offense_confidence,
        defense_level_1_5=defense_level,
        defense_confidence=defense_confidence,
        defense_assessable=defense_assessable,
        metrics={
            "attack_zone_sec": round(attack_zone_sec, 2),
            "gather_zone_sec": round(gather_zone_sec, 2),
            "cycles": cycles,
            "pace_mps": round(pace, 3),
            "opponent_zone_sec": round(opponent_zone_sec, 2),
            "shadow_sec": round(shadow_sec, 2),
            "disruption_0_1": round(disruption, 4),
            "offense_index_0_1": round(offense_index, 4),
            "defense_index_0_1": round(defense_index, 4),
        },
        shift_breakdown=shift_breakdown,
        attack_heatmap=attack_grid,
        defense_heatmap=defense_grid,
        coverage={
            "attack_eligible_sec": attack_eligible_sec,
            "opponent_active_sec": opponent_active_sec,
            "tracked_attack_sec": round(tracked_attack_sec, 2),
            "tracked_opponent_sec": round(tracked_opp_sec, 2),
            "attack_coverage_0_1": round(attack_coverage, 4),
            "opponent_coverage_0_1": round(opp_coverage, 4),
            "opponent_tracks_available": opp_index.has_data(),
        },
    )


def analyze_match_shift_play(
    *,
    points_by_team: dict[str, list[TrackPoint]],
    alliance_by_team: dict[str, str],
    schedule: ShiftSchedule | None = None,
    field_length_m: float | None = None,
    field_width_m: float | None = None,
    only_team_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    # only_team_key restricts the per-robot analysis to one team while still passing
    # the full points/alliance context (the opponent index needs every robot's tracks).
    results: dict[str, dict[str, Any]] = {}
    for team_key, alliance in alliance_by_team.items():
        if alliance not in ("red", "blue"):
            continue
        if only_team_key is not None and team_key != only_team_key:
            continue
        results[team_key] = analyze_robot_shift_play(
            team_key=team_key,
            alliance=alliance,
            points_by_team=points_by_team,
            alliance_by_team=alliance_by_team,
            schedule=schedule,
            field_length_m=field_length_m,
            field_width_m=field_width_m,
        ).to_dict()
    return results


# ── ORM adapter (server-side use over a completed analysis run) ──────────────


def points_by_team_from_tracks(tracks: Sequence[Any]) -> dict[str, list[TrackPoint]]:
    grouped: dict[str, list[TrackPoint]] = defaultdict(list)
    for track in tracks:
        team_key = getattr(track, "team_key", None)
        if not team_key:
            continue
        grouped[str(team_key)].append(
            TrackPoint(
                time_sec=float(track.time_sec),
                field_x=float(track.field_x) if track.field_x is not None else None,
                field_y=float(track.field_y) if track.field_y is not None else None,
                zone_key=track.zone_key,
                speed_mps=float(track.speed_mps) if track.speed_mps is not None else None,
            )
        )
    return grouped


def _normalized_heatmap_payload(
    grid: list[list[int]],
    *,
    team_key: str,
    event_key: str,
    match_count: int,
    field_length_m: float,
    field_width_m: float,
) -> dict[str, Any]:
    # Shape a raw count grid like the /tracks/heatmap response so the frontend
    # FieldHeatmap component can render it directly.
    total = sum(sum(row) for row in grid)
    peak = max((max(row) for row in grid), default=0) or 1
    normalized = [[round(value / peak, 4) for value in row] for row in grid]
    return {
        "ok": True,
        "team_key": team_key,
        "event_key": event_key,
        "match_key": None,
        "total_points": total,
        "match_count": match_count,
        "field_length_m": field_length_m,
        "field_width_m": field_width_m,
        "grid_cols": GRID_X,
        "grid_rows": GRID_Y,
        "sigma": 0.0,
        "grid": normalized,
    }


def summarize_team_shift_play(db: Any, *, team_key: str, event_key: str) -> dict[str, Any]:
    # Aggregate shift-play across a team's analyzed matches at an event: summed
    # attack/defense heat maps and coverage-weighted offense/defense levels. Powers
    # the Team Center "Attack vs Defense" view. Lazy/on-demand (loads tracks per run).
    from app.db import models  # local import to keep the engine import-light

    normalized_team_key = str(team_key).strip().lower()
    normalized_event_key = str(event_key).strip().lower()
    cfg_field = load_game_config().field
    field_length_m = float(cfg_field.length_m)
    field_width_m = float(cfg_field.width_m)

    match_keys = [
        str(mk)
        for (mk,) in db.query(models.MatchTeam.match_key)
        .filter(
            models.MatchTeam.team_key == normalized_team_key,
            models.MatchTeam.event_key == normalized_event_key,
        )
        .all()
    ]

    attack_sum = [[0 for _ in range(GRID_X)] for _ in range(GRID_Y)]
    defense_sum = [[0 for _ in range(GRID_X)] for _ in range(GRID_Y)]
    offense_weighted = 0.0
    offense_weight = 0.0
    offense_conf_sum = 0.0
    defense_weighted = 0.0
    defense_weight = 0.0
    defense_conf_sum = 0.0
    defense_assessable_count = 0
    sample_matches = 0

    for match_key in match_keys:
        run = (
            db.query(models.AnalysisRun)
            .filter(
                models.AnalysisRun.match_key == match_key,
                models.AnalysisRun.status == "completed",
                # never let an unauthenticated on-device run shadow the real
                # video-analysis run in this authoritative team view
                models.AnalysisRun.version != ON_DEVICE_ANALYSIS_VERSION,
            )
            .order_by(models.AnalysisRun.created_at.desc(), models.AnalysisRun.id.desc())
            .first()
        )
        if run is None:
            continue
        results = analyze_run_shift_play(db, run_id=run.id, only_team_key=normalized_team_key)
        result = results.get(normalized_team_key)
        if result is None:
            continue
        coverage = result.get("coverage", {})
        if float(coverage.get("tracked_attack_sec") or 0.0) <= 0.0 and float(coverage.get("tracked_opponent_sec") or 0.0) <= 0.0:
            continue  # team not resolved in this match
        sample_matches += 1

        for r in range(GRID_Y):
            for c in range(GRID_X):
                attack_sum[r][c] += result["heatmaps"]["attack"][r][c]
                defense_sum[r][c] += result["heatmaps"]["defense"][r][c]

        offense = result["offense"]
        off_conf = float(offense.get("confidence_0_1") or 0.0)
        offense_weighted += float(offense.get("level_1_5") or 0.0) * max(off_conf, 0.05)
        offense_weight += max(off_conf, 0.05)
        offense_conf_sum += off_conf

        defense = result["defense"]
        if defense.get("assessable"):
            def_conf = float(defense.get("confidence_0_1") or 0.0)
            defense_weighted += float(defense.get("level_1_5") or 0.0) * max(def_conf, 0.05)
            defense_weight += max(def_conf, 0.05)
            defense_conf_sum += def_conf
            defense_assessable_count += 1

    if sample_matches == 0:
        return {
            "ok": True,
            "team_key": normalized_team_key,
            "event_key": normalized_event_key,
            "available": False,
            "sample_matches": 0,
            "offense": None,
            "defense": None,
            "attack_heatmap": None,
            "defense_heatmap": None,
        }

    offense_level = max(1, min(5, int(round(offense_weighted / offense_weight)))) if offense_weight > 0 else 1
    offense_conf = round(offense_conf_sum / sample_matches, 4)
    if defense_assessable_count > 0 and defense_weight > 0:
        defense_level = max(1, min(5, int(round(defense_weighted / defense_weight))))
        defense_conf = round(defense_conf_sum / defense_assessable_count, 4)
    else:
        defense_level = 1
        defense_conf = 0.0

    return {
        "ok": True,
        "team_key": normalized_team_key,
        "event_key": normalized_event_key,
        "available": True,
        "sample_matches": sample_matches,
        "offense": {"level_1_5": offense_level, "confidence_0_1": offense_conf},
        "defense": {
            "level_1_5": defense_level,
            "confidence_0_1": defense_conf,
            "assessable": defense_assessable_count > 0,
        },
        "attack_heatmap": _normalized_heatmap_payload(
            attack_sum, team_key=normalized_team_key, event_key=normalized_event_key,
            match_count=sample_matches, field_length_m=field_length_m, field_width_m=field_width_m,
        ),
        "defense_heatmap": _normalized_heatmap_payload(
            defense_sum, team_key=normalized_team_key, event_key=normalized_event_key,
            match_count=defense_assessable_count, field_length_m=field_length_m, field_width_m=field_width_m,
        ),
    }


def analyze_run_shift_play(db: Any, *, run_id: int, only_team_key: str | None = None) -> dict[str, dict[str, Any]]:
    # Server-side entry point: load a completed run's tracks + alliance assignments
    # and run the engine. Mirrors what the on-device path will do with local tracks.
    # only_team_key restricts the per-robot analysis (opponent context is still full).
    from app.db import models  # local import to keep the engine import-light

    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise ValueError(f"AnalysisRun {run_id} not found")
    tracks = (
        db.query(models.RobotTrack)
        .filter(models.RobotTrack.analysis_run_id == run_id)
        .order_by(models.RobotTrack.time_sec.asc(), models.RobotTrack.id.asc())
        .all()
    )
    alliance_rows = (
        db.query(models.MatchTeam.team_key, models.MatchTeam.alliance)
        .filter(models.MatchTeam.match_key == run.match_key)
        .all()
    )
    alliance_by_team = {
        str(team_key).strip().lower(): str(alliance).strip().lower()
        for team_key, alliance in alliance_rows
        if team_key and alliance
    }
    points_by_team = points_by_team_from_tracks(tracks)
    return analyze_match_shift_play(
        points_by_team=points_by_team,
        alliance_by_team=alliance_by_team,
        only_team_key=only_team_key,
    )
