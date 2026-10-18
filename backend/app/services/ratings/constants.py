# Rating model version and the per-season numbers that set its scale.
from __future__ import annotations

from app.services.ratings.strength import StrengthParams
from app.services.ratings.team_model import TeamModelParams

MODEL_VERSION = "rating_v16_team_model"

# rating_v15's estimate, kept as the replay baseline (ratings/replay.py StrengthV15).
STRENGTH_PARAMS = StrengthParams(
    event_shrink=3.0,
    prior_shrink=4.0,
    prior_window=24,
    recent_window=10,
    recent_weight=2.0,
    match_window=20,
)

# Per season (docs/SEASON_TRANSITION.md); a season without a row fails closed.
# - team_model_priors: mean points per robot per component over the season's events,
#   the belief for a team with no history (a mean, since alliance totals are sums).
# - team_model_params: chosen on the older events (evaluate_rating_replay.py --grid-team).
# - median_robot_points / p99_robot_points: team-events (5+ matches) under the team
#   model at the end of each event; the rating curve puts them at 50 and 95.
#   typical: the medians per component (signals, subscores).
# evaluate_rating_replay.py prints all of these ("team_model_baseline").
SEASON_BASELINES: dict[int, dict] = {
    2026: {
        # 9,868 team-events, all 2026 events.
        "team_model_priors": {"teleop": 50.91, "auto": 13.174, "tower": 0.704},
        # Duds (Phase 3): base rate 2.5% from EM on the training events. The validation
        # splits disagreed (inner kept improving up to 8%, held-out preferred ~1%); 2.5%
        # helped both and keeps 80% intervals near 80%.
        "team_model_params": TeamModelParams(dud_base_rate=0.025, dud_predict=True, dud_robust=True),
        # Early season (Phase 2): a team's first belief = prior × exp(0.9 × its 2025 z)
        # (team_prior_strengths, scripts/build_prior_strengths.py); rookies × 0.6. Chosen
        # on the first quarter of 2026 events (Brier 0.1623 -> 0.1489).
        "prior_beta": 0.9,
        "prior_known_var_multiplier": 1.0,
        "rookie_multiplier": 0.6,
        "median_robot_points": 40.47,
        "p99_robot_points": 265.1,
        "typical": {"teleop": 31.51, "auto": 8.88, "climb": 0.03},
        # Level 3 climb; climbs are rare (96.5% of robot-matches have none).
        "climb_max_points": 30.0,
    },
}


def season_baseline(season: int) -> dict:
    baseline = SEASON_BASELINES.get(int(season))
    if baseline is None:
        raise ValueError(f"No rating baseline for season {season}; add one to SEASON_BASELINES.")
    return baseline
