# Anti-defense scoring helpers for the rating model.

from __future__ import annotations

from app.services.utils import _clamp

from app.services.ratings.constants import (
    ANTI_DEFENSE_ELITE_DROP_PCT,
    ANTI_DEFENSE_GOOD_DROP_PCT,
    ANTIDEFENSE_STAGE_EARLY_QUALS_MULTIPLIER,
    ANTIDEFENSE_STAGE_ELIMS_MULTIPLIER,
    ANTIDEFENSE_STAGE_LATE_QUALS_MULTIPLIER,
    ANTIDEFENSE_STAGE_SUPPORT_MATCHES,
)

def _anti_defense_stage_multiplier(
    stage_weight_totals: dict[str, float],
    stage_match_count: int,
) -> tuple[float, dict[str, float]]:
    total_weight = max(0.0, sum(float(value) for value in stage_weight_totals.values()))
    if total_weight <= 1e-9:
        shares = {"early_quals": 0.0, "late_quals": 0.0, "elims": 0.0}
        return 1.0, shares

    shares = {
        key: _clamp(float(stage_weight_totals.get(key) or 0.0) / total_weight, 0.0, 1.0)
        for key in ("early_quals", "late_quals", "elims")
    }
    stage_mix = (
        (shares["early_quals"] * ANTIDEFENSE_STAGE_EARLY_QUALS_MULTIPLIER)
        + (shares["late_quals"] * ANTIDEFENSE_STAGE_LATE_QUALS_MULTIPLIER)
        + (shares["elims"] * ANTIDEFENSE_STAGE_ELIMS_MULTIPLIER)
    )
    support = _clamp(
        float(stage_match_count) / float(max(1, ANTIDEFENSE_STAGE_SUPPORT_MATCHES)),
        0.0,
        1.0,
    )
    # Keep early quals less noisy; allow sharper anti-defense influence once late quals/elims evidence exists.
    stage_multiplier = 1.0 + ((stage_mix - 1.0) * (0.45 + (0.55 * support)))
    return _clamp(stage_multiplier, 0.55, 1.35), shares

def _anti_defense_tier(drop_pct: float | None) -> str:
    if drop_pct is None:
        return "Unknown"
    drop = max(0.0, float(drop_pct))
    if drop <= ANTI_DEFENSE_ELITE_DROP_PCT:
        return "Elite anti-defense"
    if drop <= ANTI_DEFENSE_GOOD_DROP_PCT:
        return "Good anti-defense"
    return "Weak anti-defense"

def _anti_defense_drop_band_score(drop_pct: float | None) -> float | None:
    if drop_pct is None:
        return None
    drop = _clamp(float(drop_pct), 0.0, 1.2)
    if drop <= ANTI_DEFENSE_ELITE_DROP_PCT:
        return _clamp(100.0 - ((drop / max(1e-6, ANTI_DEFENSE_ELITE_DROP_PCT)) * 15.0), 85.0, 100.0)
    if drop <= ANTI_DEFENSE_GOOD_DROP_PCT:
        ratio = (drop - ANTI_DEFENSE_ELITE_DROP_PCT) / max(
            1e-6,
            ANTI_DEFENSE_GOOD_DROP_PCT - ANTI_DEFENSE_ELITE_DROP_PCT,
        )
        return _clamp(85.0 - (ratio * 30.0), 55.0, 85.0)
    ratio = (drop - ANTI_DEFENSE_GOOD_DROP_PCT) / max(1e-6, 0.65 - ANTI_DEFENSE_GOOD_DROP_PCT)
    return _clamp(55.0 - (ratio * 55.0), 0.0, 55.0)
