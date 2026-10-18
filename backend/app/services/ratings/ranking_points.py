# Bonus ranking points (docs/RATING_ROADMAP.md, Phase 5): the chance an alliance earns
# each one, from the team model's predicted hub fuel and tower points.
#
# 2026 (REBUILT, manual Table 6-5): ENERGIZED at 100 fuel in an active hub, SUPERCHARGED
# at 360, TRAVERSAL at 50 tower points, at regionals and districts; District
# Championships and Championship announce their own. So each event's thresholds are read
# from its own results (the smallest value that earned the RP), and the manual's are the
# fallback before any match there has earned it.
from __future__ import annotations

import math
from typing import Iterable

# rp -> (component it counts, manual threshold, components key of the observed value)
BONUS_RPS: dict[int, dict[str, tuple[str, float, str]]] = {
    2026: {
        "energized": ("hub", 100.0, "hub_fuel_count"),
        "supercharged": ("hub", 360.0, "hub_fuel_count"),
        "traversal": ("tower", 50.0, "tower_points"),
    },
}
# rp -> (a, b) for sigma(a + b z), fitted on the older 2026 events
# (evaluate_rating_replay.py "bonus_rp"); held-out Brier: ENERGIZED 0.257 -> 0.216
# (base rate 0.248), SUPERCHARGED 0.084 -> 0.072 (0.103). TRAVERSAL keeps the normal
# tail (0.0142; calibrated 0.0147; base rate 0.0165).
BONUS_RP_CALIBRATION: dict[int, dict[str, tuple[float, float]]] = {
    2026: {"energized": (-0.348, 0.4277), "supercharged": (-1.1893, 1.0388)},
}


def event_thresholds(season: int, components: Iterable[dict]) -> dict[str, float]:
    rules = BONUS_RPS.get(int(season), {})
    lowest: dict[str, float] = {}
    for comp in components:
        for rp, (_part, _manual, key) in rules.items():
            value = comp.get(key)
            if comp.get(f"{rp}_rp") and value is not None:
                lowest[rp] = min(lowest.get(rp, float("inf")), float(value))
    return {rp: lowest.get(rp, manual) for rp, (_part, manual, _key) in rules.items()}


def margin_z(threshold: float, mean: float, var: float) -> float:
    # How many standard deviations the expected value clears the bar by (whole points:
    # "at least 100" is above 99.5).
    return (mean - (threshold - 0.5)) / math.sqrt(max(var, 1e-6))


def bonus_chances(
    season: int,
    thresholds: dict[str, float],
    hub: tuple[float, float] | None,
    tower: tuple[float, float] | None,
    calibration: dict[str, tuple[float, float]] | None = None,
) -> dict[str, float]:
    # Chance of each bonus RP. With `calibration` (rp -> (a, b), fitted on past events)
    # it's sigma(a + b z): the raw normal tail trusts the predicted totals' level, which
    # runs high for the strongest alliances.
    parts = {"hub": hub, "tower": tower}
    out = {}
    for rp, (part, _manual, _key) in BONUS_RPS.get(int(season), {}).items():
        value = parts.get(part)
        if value is None:
            continue
        z = margin_z(thresholds[rp], value[0], value[1])
        if calibration and rp in calibration:
            a, b = calibration[rp]
            out[rp] = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, a + b * z))))
        else:
            out[rp] = 0.5 * math.erfc(-z / math.sqrt(2.0))
    return out


def fit_calibration(zs: list[float], ys: list[float]) -> tuple[float, float]:
    # Two-parameter logistic regression by Newton (a small ridge keeps it stable).
    a = b = 0.0
    for _ in range(50):
        ga = gb = haa = hab = hbb = 0.0
        for z, y in zip(zs, ys):
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, a + b * z))))
            w = p * (1 - p)
            ga += p - y
            gb += (p - y) * z
            haa += w
            hab += w * z
            hbb += w * z * z
        haa += 1e-6
        hbb += 1e-6
        det = haa * hbb - hab * hab
        if det <= 0:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b

