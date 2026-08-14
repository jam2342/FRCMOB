#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import models
from app.db.session import SessionLocal
from app.services.events.classifier import (
    DEFAULT_EVENT_LABELS,
    DEFAULT_FEATURE_NAMES,
    build_transition_feature_map,
)
from app.services.game_config import classify_point


TARGET_EVENT_LABELS = {
    "depot_intake",
    "teleop_fuel_score_attempt",
    "climb_attempt",
    "protected_zone_interference",
}


def _safe_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except Exception:
        return float(default)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export transition features + labels for event model training.")
    parser.add_argument("--output", default="media/models/event_transition_dataset.csv")
    parser.add_argument("--event-key", default="", help="Optional event key filter, e.g. 2025txhou")
    parser.add_argument("--run-limit", type=int, default=0, help="Optional latest run cap.")
    parser.add_argument("--label-max-delta-sec", type=float, default=1.25)
    args = parser.parse_args()

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        event_key = args.event_key.strip() or None
        run_limit = int(args.run_limit) if args.run_limit and int(args.run_limit) > 0 else None
        label_max_delta = max(0.1, float(args.label_max_delta_sec))

        run_ids: list[int] = []
        if run_limit:
            run_rows = db.query(models.AnalysisRun.id).order_by(models.AnalysisRun.id.desc()).limit(run_limit).all()
            run_ids = [int(item[0]) for item in run_rows]

        tracks_query = db.query(models.RobotTrack).filter(
            models.RobotTrack.team_key.isnot(None),
        )
        if event_key:
            tracks_query = tracks_query.filter(models.RobotTrack.event_key == event_key)
        if run_ids:
            tracks_query = tracks_query.filter(models.RobotTrack.analysis_run_id.in_(run_ids))
        track_rows = tracks_query.all()

        events_query = db.query(models.MatchEvent).filter(
            models.MatchEvent.team_key.isnot(None),
            models.MatchEvent.event_type.in_(sorted(TARGET_EVENT_LABELS)),
        )
        if event_key:
            events_query = events_query.filter(models.MatchEvent.event_key == event_key)
        if run_ids:
            events_query = events_query.filter(models.MatchEvent.analysis_run_id.in_(run_ids))
        match_event_rows = events_query.all()

        tracks_by_key: dict[tuple[int, str], list[dict]] = defaultdict(list)
        for row in track_rows:
            key = (int(row.analysis_run_id), str(row.team_key))
            field_x = _safe_float(row.field_x, 0.0) if row.field_x is not None else None
            field_y = _safe_float(row.field_y, 0.0) if row.field_y is not None else None
            zone_key = row.zone_key
            zone_kind = None
            if field_x is not None and field_y is not None:
                zone = classify_point(field_x, field_y)
                if zone is not None:
                    if not zone_key:
                        zone_key = zone.key
                    zone_kind = zone.kind
            tracks_by_key[key].append(
                {
                    "run_id": int(row.analysis_run_id),
                    "event_key": str(row.event_key),
                    "match_key": str(row.match_key),
                    "team_key": str(row.team_key),
                    "track_id": int(row.track_id),
                    "frame_index": int(row.frame_index),
                    "time_sec": _safe_float(row.time_sec),
                    "zone_key": zone_key,
                    "zone_kind": zone_kind,
                    "field_x": field_x,
                    "field_y": field_y,
                    "speed_mps": _safe_float(row.speed_mps, 0.0) if row.speed_mps is not None else None,
                    "speed_px": None,
                    "confidence": _safe_float(row.confidence, 0.0) if row.confidence is not None else 0.0,
                }
            )

        events_by_key: dict[tuple[int, str], list[tuple[float, str]]] = defaultdict(list)
        for row in match_event_rows:
            key = (int(row.analysis_run_id), str(row.team_key))
            events_by_key[key].append((_safe_float(row.time_sec), str(row.event_type)))
        for values in events_by_key.values():
            values.sort(key=lambda item: item[0])

        rows_out: list[dict[str, object]] = []
        for key, rows in tracks_by_key.items():
            if len(rows) < 2:
                continue
            rows.sort(key=lambda item: (float(item["time_sec"]), int(item["frame_index"])))
            run_id, team_key = key
            duration_sec = max(1.0, max(float(item["time_sec"]) for item in rows) + 2.0)
            auto_window_sec = 20.0
            endgame_start_sec = max(auto_window_sec, duration_sec - 30.0)
            keyed_events = events_by_key.get(key, [])
            for index in range(1, len(rows)):
                prev_row = rows[index - 1]
                curr_row = rows[index]
                curr_time = float(curr_row["time_sec"])
                prev_time = float(prev_row["time_sec"])
                if (curr_time - prev_time) < 0.35:
                    continue
                feature_map = build_transition_feature_map(
                    prev_row,
                    curr_row,
                    duration_sec=duration_sec,
                    auto_window_sec=auto_window_sec,
                    endgame_start_sec=endgame_start_sec,
                )
                label = "none"
                nearest_delta = None
                for event_time, event_type in keyed_events:
                    delta = abs(event_time - curr_time)
                    if delta > label_max_delta:
                        continue
                    if nearest_delta is None or delta < nearest_delta:
                        nearest_delta = delta
                        label = event_type
                if label not in TARGET_EVENT_LABELS:
                    label = "none"
                if label == "none":
                    digest = hashlib.sha1(
                        f"{run_id}:{team_key}:{curr_time:.3f}".encode("utf-8"),
                        usedforsecurity=False,
                    ).hexdigest()
                    if (int(digest[:8], 16) % 10) != 0:
                        continue

                row_payload: dict[str, object] = {
                    "run_id": run_id,
                    "team_key": team_key,
                    "match_key": curr_row["match_key"],
                    "event_key": curr_row["event_key"],
                    "time_sec": round(curr_time, 4),
                    "label": label,
                }
                for name in DEFAULT_FEATURE_NAMES:
                    row_payload[name] = round(float(feature_map.get(name, 0.0)), 6)
                rows_out.append(row_payload)

        headers = ["run_id", "team_key", "match_key", "event_key", "time_sec", "label", *DEFAULT_FEATURE_NAMES]
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows_out:
                writer.writerow(row)

        label_counts: dict[str, int] = defaultdict(int)
        for row in rows_out:
            label_counts[str(row["label"])] += 1

        print(
            json.dumps(
                {
                    "ok": True,
                    "output": output_path.as_posix(),
                    "rows": len(rows_out),
                    "labels": {label: int(label_counts.get(label, 0)) for label in DEFAULT_EVENT_LABELS},
                    "event_key": event_key,
                    "run_limit": run_limit,
                },
                indent=2,
            )
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
