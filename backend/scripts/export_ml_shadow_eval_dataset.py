#!/usr/bin/env python3
from __future__ import annotations

# ruff: noqa: E402

import argparse
import csv
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings
from app.db import models
from app.db.session import SessionLocal
from app.services.ml.shadow import (
    FEATURE_SCOPE_MATCH_OUTCOME,
    FEATURE_SCOPE_TEAM_STRENGTH,
    rebuild_feature_snapshots,
)


def _normalize_event_key(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Export ML shadow evaluation dataset as CSV.")
    parser.add_argument("--output", default="media/models/shadow/eval_dataset.csv")
    parser.add_argument("--scope", choices=["all", "team_strength", "match_outcome"], default="all")
    parser.add_argument("--event-key", default=None)
    parser.add_argument("--source-version", default=None)
    parser.add_argument("--rebuild", action="store_true", help="Rebuild feature snapshots before export.")
    parser.add_argument("--limit-events", type=int, default=30)
    parser.add_argument("--include-predictions", action="store_true")
    args = parser.parse_args()

    source_version = (
        str(args.source_version).strip()
        if isinstance(args.source_version, str) and args.source_version.strip()
        else str(getattr(settings, "ml_shadow_feature_source_version", "shadow_features_v1") or "shadow_features_v1")
    )
    normalized_event_key = _normalize_event_key(args.event_key)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = (BACKEND_ROOT / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        if args.rebuild:
            result = rebuild_feature_snapshots(
                db,
                event_key=normalized_event_key,
                limit_events=int(args.limit_events),
                replace_existing=True,
                source_version=source_version,
            )
            print(
                f"rebuilt snapshots source_version={source_version} event_count={result.get('event_count')}"
            )

        scopes: list[str]
        if args.scope == "all":
            scopes = [FEATURE_SCOPE_TEAM_STRENGTH, FEATURE_SCOPE_MATCH_OUTCOME]
        elif args.scope == "team_strength":
            scopes = [FEATURE_SCOPE_TEAM_STRENGTH]
        else:
            scopes = [FEATURE_SCOPE_MATCH_OUTCOME]

        query = db.query(models.MLFeatureSnapshot).filter(
            models.MLFeatureSnapshot.scope.in_(scopes),
            models.MLFeatureSnapshot.source_version == source_version,
        )
        if normalized_event_key:
            query = query.filter(models.MLFeatureSnapshot.event_key == normalized_event_key)
        snapshot_rows = (
            query.order_by(
                models.MLFeatureSnapshot.scope.asc(),
                models.MLFeatureSnapshot.event_key.asc().nullslast(),
                models.MLFeatureSnapshot.match_key.asc().nullslast(),
                models.MLFeatureSnapshot.team_key.asc().nullslast(),
            ).all()
        )

        prediction_by_snapshot_target: dict[tuple[str, str], float] = {}
        if args.include_predictions:
            pred_query = db.query(
                models.MLShadowPrediction.feature_snapshot_key,
                models.MLShadowPrediction.target_key,
                models.MLShadowPrediction.prediction_value,
                models.MLShadowPrediction.created_at,
            ).filter(models.MLShadowPrediction.feature_snapshot_key.isnot(None))
            if normalized_event_key:
                pred_query = pred_query.filter(models.MLShadowPrediction.event_key == normalized_event_key)
            pred_rows = pred_query.order_by(models.MLShadowPrediction.created_at.desc()).all()
            for feature_snapshot_key, target_key, prediction_value, _created_at in pred_rows:
                snapshot_key = str(feature_snapshot_key or "").strip()
                target_key_value = str(target_key or "").strip()
                numeric = _safe_float(prediction_value)
                if not snapshot_key or not target_key_value or numeric is None:
                    continue
                key = (snapshot_key, target_key_value)
                if key in prediction_by_snapshot_target:
                    continue
                prediction_by_snapshot_target[key] = float(numeric)

        export_rows: list[dict[str, Any]] = []
        for row in snapshot_rows:
            feature_vector = row.feature_vector if isinstance(row.feature_vector, dict) else {}
            target = row.target if isinstance(row.target, dict) else {}
            payload: dict[str, Any] = {
                "scope": row.scope,
                "snapshot_key": row.snapshot_key,
                "source_version": row.source_version,
                "split_tag": row.split_tag,
                "event_key": row.event_key,
                "match_key": row.match_key,
                "team_key": row.team_key,
                "alliance_color": row.alliance_color,
                "created_at": row.created_at.isoformat() if row.created_at is not None else None,
            }

            for key, value in feature_vector.items():
                payload[f"f_{key}"] = value
            for key, value in target.items():
                payload[f"t_{key}"] = value

            if row.scope == FEATURE_SCOPE_TEAM_STRENGTH:
                prediction = prediction_by_snapshot_target.get((row.snapshot_key, "strength_active_bps_pred"))
                payload["p_strength_active_bps_pred"] = prediction
            elif row.scope == FEATURE_SCOPE_MATCH_OUTCOME:
                prediction = prediction_by_snapshot_target.get((row.snapshot_key, "red_win_prob"))
                payload["p_red_win_prob"] = prediction

            export_rows.append(payload)

        fieldnames: list[str] = []
        for row in export_rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)

        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(export_rows)

        print(
            f"exported rows={len(export_rows)} scopes={','.join(scopes)} source_version={source_version} path={output_path}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
