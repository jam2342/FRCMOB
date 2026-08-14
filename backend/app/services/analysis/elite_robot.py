from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db import models
from app.services.scouting_rooms.elite_detector import RoleClassifier, _clamp, _mean, _std


class EliteRobotAnalyzer:
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
            .order_by(models.TeamMatchFinding.created_at.desc())
            .all()
        )

    def analyze(self) -> dict[str, Any]:
        return {
            "game_performance": self._analyze_game_performance(),
            "autonomous_performance": self._analyze_autonomous_performance(),
            "endgame_capability": self._analyze_endgame_capability(),
            "game_piece_handling": self._analyze_piece_handling(),
            "reliability": self._analyze_reliability(),
            "driver_skill": self._analyze_driver_skill(),
            "strategic_versatility": self._analyze_strategic_versatility(),
            "engineering_quality": self._analyze_engineering_quality(),
            "championship_consistency": self._analyze_consistency(),
            "match_composure": self._analyze_match_composure(),
        }

    def _analyze_game_performance(self) -> dict[str, Any]:
        scoring_rates = [
            f.fuel_scoring_rate
            for f in self.findings
            if f.fuel_scoring_rate is not None and f.fuel_scoring_rate > 0
        ]
        cycle_times = [
            f.cycle_time_sec for f in self.findings if f.cycle_time_sec is not None and f.cycle_time_sec > 0
        ]

        defensive_matches = [
            f for f in self.findings if f.defensive_engagement_sec is not None and f.defensive_engagement_sec > 1.5
        ]
        scoring_under_defense = [
            f.fuel_scoring_rate for f in defensive_matches if f.fuel_scoring_rate is not None and f.fuel_scoring_rate > 0
        ]

        score_0_100 = 50.0
        signal_strength = 0.0

        if scoring_rates:
            avg_rate = _mean(scoring_rates)
            percentile = _clamp((avg_rate / 10.0) * 100.0, 0.0, 100.0)  # Assume 10 shots/min is elite
            score_0_100 = percentile
            signal_strength += len(scoring_rates) / max(1, len(self.findings))

        if cycle_times:
            avg_cycle = _mean(cycle_times)
            # Faster cycles are better; assume 3.0 seconds is elite (inverse scale)
            speed_percentile = _clamp(100.0 - ((avg_cycle / 3.0) * 100.0), 0.0, 100.0)
            score_0_100 = (score_0_100 + speed_percentile) / 2.0
            signal_strength += len(cycle_times) / max(1, len(self.findings))

        consistency_under_defense = 0.0
        if scoring_under_defense and scoring_rates:
            avg_under_defense = _mean(scoring_under_defense)
            avg_overall = _mean(scoring_rates)
            # How well does robot maintain scoring under defense? >80% is elite
            defense_ratio = avg_under_defense / max(0.01, avg_overall)
            consistency_under_defense = _clamp(defense_ratio * 100.0, 0.0, 100.0)

        signal_strength = _clamp(signal_strength, 0.0, 1.0)

        return {
            "score_0_100": round(_clamp(score_0_100, 0.0, 100.0), 1),
            "points_per_second": round(_mean(scoring_rates) / 10.0 if scoring_rates else 0.0, 4),
            "avg_cycle_time_sec": round(_mean(cycle_times), 2) if cycle_times else None,
            "accuracy_under_defense_0_100": round(_clamp(consistency_under_defense, 0.0, 100.0), 1),
            "multi_position_evidence": len(scoring_rates) > 4,  # Multiple scoring ops = multi-pos
            "signal_strength_0_1": round(signal_strength, 3),
        }

    def _analyze_autonomous_performance(self) -> dict[str, Any]:

        auto_contributions = [
            f.auto_contribution
            for f in self.findings
            if f.auto_contribution is not None and f.auto_contribution > 0
        ]
        auto_reliability = len(auto_contributions) / max(1, len(self.findings))

        # Multi-piece proxy: high auto contribution consistently
        multi_piece_evidence = auto_reliability > 0.75 and _mean(auto_contributions) > 8.0 if auto_contributions else False

        score_0_100 = 50.0
        if auto_contributions:
            avg_auto = _mean(auto_contributions)
            score_0_100 = _clamp((avg_auto / 15.0) * 100.0, 0.0, 100.0)  # Assume 15 pts is elite

        return {
            "score_0_100": round(_clamp(score_0_100, 0.0, 100.0), 1),
            "routine_reliability_0_1": round(_clamp(auto_reliability, 0.0, 1.0), 3),
            "multi_piece_auto_evidence": multi_piece_evidence,
            "avg_auto_points": round(_mean(auto_contributions), 2) if auto_contributions else None,
            "signal_matches": len(auto_contributions),
        }

    def _analyze_endgame_capability(self) -> dict[str, Any]:

        climb_attempts = [
            f.climb_success_prob
            for f in self.findings
            if f.climb_success_prob is not None and f.climb_success_prob >= 0
        ]

        success_rate = _mean(climb_attempts) if climb_attempts else 0.0
        score_0_100 = _clamp((success_rate or 0.0) * 100.0, 0.0, 100.0)

        endgame_consistency = 0.0
        if climb_attempts:
            std_dev = _std(climb_attempts)
            consistency = 1.0 - min(1.0, (std_dev or 0.0) / 0.3)  
            endgame_consistency = _clamp(consistency * 100.0, 0.0, 100.0)

        return {
            "score_0_100": round(_clamp(score_0_100, 0.0, 100.0), 1),
            "success_rate_0_1": round(_clamp(success_rate or 0.0, 0.0, 1.0), 3),
            "consistency_under_pressure_0_100": round(endgame_consistency, 1),
            "signal_matches": len(climb_attempts),
        }

    def _analyze_piece_handling(self) -> dict[str, Any]:

        # Signal from cycle_time_sec: faster cycles = better intake + release
        cycle_times = [f.cycle_time_sec for f in self.findings if f.cycle_time_sec is not None and f.cycle_time_sec > 0]

        # Signal from scoring_rate consistency: consistent rate = reliable possession
        scoring_rates = [
            f.fuel_scoring_rate for f in self.findings if f.fuel_scoring_rate is not None and f.fuel_scoring_rate > 0
        ]
        scoring_consistency = 0.0
        if scoring_rates:
            std_dev = _std(scoring_rates)
            consistency = 1.0 - min(1.0, (std_dev or 0.0) / max(0.1, _mean(scoring_rates) or 1.0))
            scoring_consistency = _clamp(consistency * 100.0, 0.0, 100.0)

        intake_speed_proxy = 0.0
        if cycle_times:
            avg_cycle = _mean(cycle_times)
            intake_speed_proxy = _clamp(100.0 - ((avg_cycle / 3.0) * 100.0 * 0.3), 0.0, 100.0)

        return {
            "score_0_100": round((intake_speed_proxy + scoring_consistency) / 2.0, 1),
            "intake_speed_proxy_0_100": round(intake_speed_proxy, 1),
            "possession_security_0_100": round(scoring_consistency, 1),
            "avg_cycle_time_sec": round(_mean(cycle_times), 2) if cycle_times else None,
        }

    def _analyze_reliability(self) -> dict[str, Any]:
        reliability_scores = [
            f.reliability_score
            for f in self.findings
            if f.reliability_score is not None and f.reliability_score >= 0
        ]

        avg_reliability = _mean(reliability_scores) if reliability_scores else 50.0
        reliability_consistency = 0.0
        if reliability_scores:
            std_dev = _std(reliability_scores)
            consistency = 1.0 - min(1.0, (std_dev or 0.0) / 30.0)
            reliability_consistency = _clamp(consistency * 100.0, 0.0, 100.0)

        # Penalty count proxy: check summary for penalty events
        penalty_count = 0
        for finding in self.findings:
            if isinstance(finding.summary, dict):
                event_counts = finding.summary.get("event_counts", {})
                penalty_count += sum(
                    count
                    for key, count in event_counts.items()
                    if isinstance(count, int) and ("foul" in str(key).lower() or "penalty" in str(key).lower())
                )

        penalty_impact = _clamp(100.0 - (penalty_count * 10.0), 0.0, 100.0)

        return {
            "score_0_100": round((avg_reliability + reliability_consistency + penalty_impact) / 3.0, 1),
            "avg_reliability_score_0_100": round(avg_reliability, 1),
            "consistency_match_to_match_0_100": round(reliability_consistency, 1),
            "penalty_discipline_0_100": round(penalty_impact, 1),
            "signal_matches": len(reliability_scores),
        }

    def _analyze_driver_skill(self) -> dict[str, Any]:

        cycle_times = [f.cycle_time_sec for f in self.findings if f.cycle_time_sec is not None]
        cycle_efficiency = 0.0
        if cycle_times:
            avg_cycle = _mean(cycle_times)
            # Elite cycle time ~3-4 sec; standard ~6 sec
            cycle_efficiency = _clamp((6.0 - (avg_cycle or 6.0)) / 6.0 * 100.0 if avg_cycle else 50.0, 0.0, 100.0)

        defensive_interactions = 0
        for finding in self.findings:
            if isinstance(finding.summary, dict):
                defense_data = finding.summary.get("defensive_interactions", {})
                if isinstance(defense_data, dict):
                    defensive_interactions += defense_data.get("velocity_interrupt_events", 0)

        defense_driving_signal = _clamp(int(defensive_interactions) / max(1, len(self.findings)) * 10.0, 0.0, 100.0)

        climb_success = [
            f.climb_success_prob for f in self.findings if f.climb_success_prob is not None and f.climb_success_prob > 0
        ]
        endgame_precision = _clamp(_mean(climb_success) * 100.0 if climb_success else 50.0, 0.0, 100.0) if climb_success else 50.0

        penalty_count = sum(
            int(finding.summary.get("event_counts", {}).get(key, 0))
            for finding in self.findings
            if isinstance(finding.summary, dict)
            for key in finding.summary.get("event_counts", {})
            if "foul" in str(key).lower()
        )
        penalty_discipline = _clamp(100.0 - (penalty_count * 20.0), 0.0, 100.0)

        scoring_rates = [f.fuel_scoring_rate for f in self.findings if f.fuel_scoring_rate is not None]
        consistency_signal = 0.0
        if scoring_rates and len(scoring_rates) >= 2:
            std_dev = _std(scoring_rates) or 0.0
            mean_val = _mean(scoring_rates) or 1.0
            cv = std_dev / max(0.1, mean_val)
            consistency_signal = _clamp(100.0 - (cv * 100.0), 0.0, 100.0)

        driver_skill_score = (
            (0.30 * cycle_efficiency)
            + (0.25 * endgame_precision)
            + (0.20 * defense_driving_signal)
            + (0.15 * penalty_discipline)
            + (0.10 * consistency_signal)
        )

        return {
            "score_0_100": round(_clamp(driver_skill_score, 0.0, 100.0), 1),
            "cycle_efficiency_0_100": round(cycle_efficiency, 1),
            "field_positioning_0_100": round(_clamp(100.0 - (defensive_interactions / max(1, len(self.findings)) * 5.0), 0.0, 100.0), 1),
            "precision_control_0_100": round(endgame_precision, 1),
            "defense_driving_skill_0_100": round(defense_driving_signal, 1),
            "clean_play_discipline_0_100": round(penalty_discipline, 1),
            "decision_consistency_0_100": round(consistency_signal, 1),
        }

    def _analyze_strategic_versatility(self) -> dict[str, Any]:

        role_classifier = RoleClassifier(self.db, self.event_key, self.team_key)
        role_data = role_classifier.classify()

        role_signals = role_data.get("role_signals", {})
        primary_role = role_data.get("primary_role", "unknown")
        versatility_score = role_data.get("versatility_score", 50.0)
        specialization = role_data.get("specialization", 50.0)
        
        is_high_scorer = role_signals.get("scorer_signal", 0) >= 70
        
        is_endgame_specialist = role_signals.get("endgame_signal", 0) >= 70

        is_defender = role_signals.get("defender_signal", 0) >= 70

        multi_role_capability_count = sum([is_high_scorer, is_endgame_specialist, is_defender])
        alliance_compatibility_signal = versatility_score >= 60.0

        championship_versatility = (versatility_score + specialization) / 2.0

        return {
            "score_0_100": round(championship_versatility, 1),
            "primary_role": primary_role,
            "role_signals": role_signals,
            "versatility_score_0_100": round(versatility_score, 1),
            "specialization_0_100": round(specialization, 1),
            "role_scorer_signal": is_high_scorer,
            "role_endgame_specialist_signal": is_endgame_specialist,
            "role_defender_signal": is_defender,
            "multi_role_capability_count": int(multi_role_capability_count),
            "alliance_compatibility_signal": alliance_compatibility_signal,
        }

    def _analyze_engineering_quality(self) -> dict[str, Any]:

        reliability_scores = [
            f.reliability_score for f in self.findings if f.reliability_score is not None and f.reliability_score >= 0
        ]

        cycle_times = [f.cycle_time_sec for f in self.findings if f.cycle_time_sec is not None and f.cycle_time_sec > 0]
        mechanical_health = 0.0
        if cycle_times:
            std_dev = _std(cycle_times)
            mechanical_health = _clamp(100.0 - (std_dev or 0.0) * 50.0, 0.0, 100.0)

        auto_consistency = 0.0
        autos = [f.auto_contribution for f in self.findings if f.auto_contribution is not None]
        if autos:
            auto_std = _std(autos)
            auto_consistency = _clamp(100.0 - ((auto_std or 0.0) / max(1.0, _mean(autos) or 1.0)) * 100.0, 0.0, 100.0)

        drivebase_quality = mechanical_health
        mechanism_quality = auto_consistency

        return {
            "score_0_100": round((_mean(reliability_scores) + drivebase_quality + mechanism_quality) / 3.0 if reliability_scores else 50.0, 1),
            "drivebase_quality_0_100": round(drivebase_quality, 1),
            "mechanism_reliability_0_100": round(_mean(reliability_scores), 1) if reliability_scores else 50.0,
            "sensor_software_quality_0_100": round(auto_consistency, 1),
        }

    def _analyze_consistency(self) -> dict[str, Any]:
        if not self.findings:
            return {
                "score_0_100": 50.0,
                "match_count": 0,
            }

        scoring_rates = [f.fuel_scoring_rate for f in self.findings if f.fuel_scoring_rate is not None and f.fuel_scoring_rate > 0]
        reliability_scores = [f.reliability_score for f in self.findings if f.reliability_score is not None and f.reliability_score >= 0]

        consistency_scores = []

        if scoring_rates:
            std_dev = _std(scoring_rates)
            mean_val = _mean(scoring_rates)
            cv = (std_dev or 0.0) / max(0.01, mean_val or 1.0)
            consistency = _clamp(100.0 - (cv * 100.0), 0.0, 100.0)
            consistency_scores.append(consistency)

        if reliability_scores:
            std_dev = _std(reliability_scores)
            consistency = _clamp(100.0 - (std_dev or 0.0), 0.0, 100.0)
            consistency_scores.append(consistency)

        overall_consistency = _mean(consistency_scores) if consistency_scores else 50.0

        return {
            "score_0_100": round(overall_consistency, 1),
            "match_count": len(self.findings),
            "scoring_consistency_cv": round(_std(scoring_rates) / max(0.01, _mean(scoring_rates) or 1.0), 3) if scoring_rates else None,
        }

    def _analyze_match_composure(self) -> dict[str, Any]:

        climbs = [f.climb_success_prob for f in self.findings if f.climb_success_prob is not None]
        endgame_composure = 0.0
        if climbs:
            endgame_composure = _clamp(_mean(climbs) * 100.0, 0.0, 100.0)

        penalty_count = 0
        for finding in self.findings:
            if isinstance(finding.summary, dict):
                event_counts = finding.summary.get("event_counts", {})
                penalty_count += sum(
                    int(count)
                    for key, count in event_counts.items()
                    if isinstance(count, int) and ("foul" in str(key).lower())
                )
        strategic_discipline = _clamp(100.0 - (penalty_count / max(1, len(self.findings)) * 30.0), 0.0, 100.0)

        return {
            "score_0_100": round((endgame_composure + strategic_discipline) / 2.0, 1),
            "endgame_performance_under_pressure_0_100": round(endgame_composure, 1),
            "strategic_discipline_0_100": round(strategic_discipline, 1),
            "no_fall_off_signal": len(self.findings) > 6 and strategic_discipline > 70.0,
        }
