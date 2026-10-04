# Shared base blend, before penalties, confidence shrinkage, and sparse-data guards.
from app.services.ratings.constants import BASE_AUTO_WEIGHT


def base_rating_score(
    *,
    results_anchor: float,
    performance: float,
    driver_skill: float,
    robot_level: float,
    manual_points: float,
    rp_contribution: float,
    auto_contribution: float,
    anti_defense: float,
    anti_defense_weight: float,
) -> float:
    weights = (0.21, 0.29, 0.16, 0.11, 0.09, 0.05, BASE_AUTO_WEIGHT, anti_defense_weight)
    values = (results_anchor, performance, driver_skill, robot_level,
              manual_points, rp_contribution, auto_contribution, anti_defense)
    return sum(weight * value for weight, value in zip(weights, values)) / max(1e-6, sum(weights))
