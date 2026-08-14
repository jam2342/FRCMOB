from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeAlias

from app.db import models
from sqlalchemy.orm import Session


@dataclass(slots=True)
class PredictorContext:
    db: Session | None
    season_year: int
    event_key: str | None
    match_key: str | None
    team_key: str | None
    analysis_run_id: int | None
    coverage: float
    throughput_coverage: float | None
    events: list[models.MatchEvent]
    tracks: list[models.RobotTrack]
    finding: models.TeamMatchFinding | None
    throughput: models.TeamMatchThroughput | None
    quality: models.AnalysisQuality | None
    # Per-draft caches so round-2 predictors don't rebuild the feature vector or
    # re-run ML inference once per field. Populated lazily on first access.
    feature_vector: dict[str, float] | None = None
    ml_predictions_by_field: dict[str, "PredictorResult | None"] | None = None


@dataclass(slots=True)
class PredictorResult:
    value: Any
    confidence: float
    provenance: str
    evidence_refs: list[dict[str, Any]]


Predictor: TypeAlias = Callable[[PredictorContext], PredictorResult | None]

PREDICTORS: dict[str, Predictor] = {}


def register(field_name: str) -> Callable[[Predictor], Predictor]:
    def _wrap(fn: Predictor) -> Predictor:
        PREDICTORS[field_name] = fn
        return fn

    return _wrap
