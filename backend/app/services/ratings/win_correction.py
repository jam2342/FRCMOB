# A learned correction to the team model's win probability (docs/RATING_ROADMAP.md,
# Phase 7): a small network on features known before the match, trained on 2026's
# as-of predictions (the leak that sank 2026's first ML attempt can't happen here: every
# feature comes from the replay before the match is played).
#
# Held-out Brier 0.1503 -> 0.1464, validation 0.1449 -> 0.1412 (5 seeds agree). The
# deterministic probability stays: the network's answer is blended in by
# CORRECTION_BLEND (0 = off), and a season without weights gets no correction.
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

CORRECTION_BLEND = 1.0
_WEIGHTS = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=4)
def _weights(season: int) -> dict | None:
    path = _WEIGHTS / f"win_correction_{int(season)}.json"
    if not path.exists():
        return None
    state = json.loads(path.read_text())["state"]
    return {k: np.array(v, dtype=float) for k, v in state.items()}


def features(model, event_key: str, red, blue, prediction, base_prob: float, matches_seen: int, matches_before: int,
             playoff: bool, event_start: int | None) -> list[float]:
    # The 14 inputs, in the order the network was trained on (keep in step with
    # scripts/train_win_correction.py).
    margin = (prediction.red_mean + prediction.red_fouls) - (prediction.blue_mean + prediction.blue_fouls)
    sd = math.sqrt(max(1e-6, (prediction.red_var or 0) + (prediction.blue_var or 0) + prediction.red_fouls_var + prediction.blue_fouls_var))
    base = min(1 - 1e-6, max(1e-6, base_prob))

    def side(teams):
        beliefs = [model.belief(t, event_key, event_start) for t in teams]
        points = sorted(sum(b.mean for b in x.values()) for x in beliefs)
        spread = max(sum(b.var for b in x.values()) for x in beliefs) ** 0.5
        return points[-1], points[0], sum(model.dud_rate(t) for t in teams), spread

    rs, bs = side(red), side(blue)
    season_start = model.season_start or (event_start or 0)
    red_tower = (prediction.red_tower or (0.0, 0.0))[0]
    blue_tower = (prediction.blue_tower or (0.0, 0.0))[0]
    return [
        margin / sd, margin / 100, (prediction.red_mean - prediction.blue_mean) / 100, math.log(base / (1 - base)),
        (rs[0] - bs[0]) / 100, (rs[1] - bs[1]) / 100, rs[2] - bs[2], (rs[3] - bs[3]) / 50,
        float(playoff), min(matches_seen, 10) / 10, min(matches_before, 40) / 40,
        ((event_start or season_start) - season_start) / (86400 * 120),
        (red_tower - blue_tower) / 30, (prediction.red_mean + prediction.blue_mean) / 400,
    ]


def active(season: int) -> bool:
    return CORRECTION_BLEND > 0 and _weights(season) is not None


def corrected(season: int, base_prob: float, feature_row: list[float] | None) -> float:
    # The blended win probability for red; the deterministic one when there are no weights.
    weights = _weights(season)
    if weights is None or feature_row is None or CORRECTION_BLEND <= 0:
        return base_prob
    x = np.array(feature_row, dtype=float)
    hidden = np.tanh(weights["0.weight"] @ x + weights["0.bias"])
    logit = float((weights["2.weight"] @ hidden)[0] + weights["2.bias"][0])
    network = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))
    return (1.0 - CORRECTION_BLEND) * base_prob + CORRECTION_BLEND * network
