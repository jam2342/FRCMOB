from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db import models
from app.services.utils import _clamp, _mean, _std as _std


class RoleClassifier:
 
    def __init__(self, db: Session, event_key: str, team_key: str):
        self.db = db
        self.event_key = event_key
        self.team_key = team_key
        self.findings = self._load_findings()

    def _load_findings(self) -> list[models.TeamMatchFinding]:
        return (
            self.db.query(models.TeamMatchFinding)
            .filter(
                models.TeamMatchFinding.event_key == self.event_key,
                models.TeamMatchFinding.team_key == self.team_key,
            )
            .all()
        )

    def classify(self) -> dict[str, Any]:
        if not self.findings:
            return {
                "primary_role": "unknown",
                "role_signals": {"scorer_signal": 50, "defender_signal": 50, "feeder_signal": 50, "endgame_signal": 50},
                "versatility_score": 50,
                "specialization": 50,
                "evidence": {"explanation": "Insufficient data", "matches_analyzed": 0, "confidence": 0},
            }

        scoring_rates = [f.fuel_scoring_rate for f in self.findings if f.fuel_scoring_rate is not None]
        cycle_times = [f.cycle_time_sec for f in self.findings if f.cycle_time_sec is not None]
        climb_success = [f.climb_success_prob for f in self.findings if f.climb_success_prob is not None]
        defensive_eng = [f.defensive_engagement_sec for f in self.findings if f.defensive_engagement_sec is not None]


        scorer_signal = 0.0
        if scoring_rates:
            scoring_rate_pct = min(100.0, (_mean(scoring_rates) or 0.0) / 15.0 * 100.0)  
            cycle_quality = 0.0
            if cycle_times:
                avg_cycle = _mean(cycle_times) or 0.0
                cycle_quality = max(0.0, (6.0 - avg_cycle) / 6.0 * 100.0) 
            scorer_signal = (0.60 * scoring_rate_pct) + (0.40 * cycle_quality)

        defender_signal = 0.0
        if defensive_eng:
            defense_engagement_pct = min(100.0, (_mean(defensive_eng) or 0.0) / 60.0 * 100.0)  
            low_scoring = max(0.0, 100.0 - ((_mean(scoring_rates) or 0.0) / 10.0 * 100.0))
            defender_signal = (0.70 * defense_engagement_pct) + (0.30 * low_scoring)

        feeder_signal = 0.0
        if scoring_rates and cycle_times:
            moderate_throughput = _clamp((_mean(scoring_rates) or 0.0) / 8.0 * 100.0, 0.0, 100.0)
            cycle_eff = _clamp(100.0 - ((_mean(cycle_times) or 0.0) / 8.0 * 100.0), 0.0, 100.0)
            endgame_presence = min(100.0, (_mean(climb_success) or 0.0) * 100.0 if climb_success else 0.0)
            low_endgame = max(0.0, 100.0 - endgame_presence)
            feeder_signal = (0.40 * moderate_throughput) + (0.35 * cycle_eff) + (0.25 * low_endgame)

        endgame_signal = 0.0
        if climb_success:
            climb_rate = _clamp((_mean(climb_success) or 0.0) * 100.0, 0.0, 100.0)
            moderate_score = _clamp((_mean(scoring_rates) or 0.0) / 8.0 * 100.0, 0.0, 100.0)
            endgame_signal = (0.65 * climb_rate) + (0.35 * moderate_score)

        role_signals = {
            "scorer_signal": round(_clamp(scorer_signal, 0.0, 100.0), 1),
            "defender_signal": round(_clamp(defender_signal, 0.0, 100.0), 1),
            "feeder_signal": round(_clamp(feeder_signal, 0.0, 100.0), 1),
            "endgame_signal": round(_clamp(endgame_signal, 0.0, 100.0), 1),
        }

        role_scores = sorted(role_signals.items(), key=lambda x: x[1], reverse=True)
        primary_role_key = role_scores[0][0].replace("_signal", "")
        role_map = {
            "scorer": "primary_scorer",
            "defender": "defender",
            "feeder": "feeder",
            "endgame": "endgame_specialist",
        }
        primary_role = role_map.get(primary_role_key, "versatile")

        top_signals = [score for _, score in role_scores[:2]]
        versatility = 100.0 - min(100.0, abs(top_signals[0] - top_signals[1]) / 10.0)

        specialization = min(100.0, (role_scores[0][1] - _mean(list(role_signals.values())) or 0.0) / 20.0 * 100.0)

        explanation = f"Role: {primary_role} ({role_scores[0][1]:.0f}). "
        if top_signals[0] - top_signals[1] > 15:
            explanation += "Specialized focus on this role. "
        else:
            explanation += "Can adapt to multiple roles. "

        return {
            "primary_role": primary_role,
            "role_signals": role_signals,
            "versatility_score": round(_clamp(versatility, 0.0, 100.0), 1),
            "specialization": round(_clamp(specialization, 0.0, 100.0), 1),
            "evidence": {
                "explanation": explanation,
                "matches_analyzed": len(self.findings),
                "confidence": round(min(100.0, len(self.findings) / 5.0 * 100.0), 1),
            },
        }
