# Team strength = expected points a robot adds to its alliance per match, from official
# results only, and the 0-100 rating that maps it onto one scale for the whole season.
#
# A team's own matches at this event count most; until a few are in, the estimate leans
# on its earlier events this season, and with no history at all on the season's typical
# robot. Pure functions: the ratings pipeline and scripts/evaluate_rating_replay.py share
# them, so the replayed numbers are the shipped numbers.
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

COMPONENTS = ("teleop", "auto", "climb")


@dataclass(frozen=True)
class MatchPoints:
    match_key: str
    time: int
    # Points this robot added in the match; None when the official breakdown can't say.
    teleop: float | None
    auto: float | None
    climb: float | None


@dataclass(frozen=True)
class StrengthParams:
    # Matches here before this event's own average and the earlier-events average
    # weigh the same.
    event_shrink: float = 3.0
    # Earlier matches before they outweigh the season's typical robot.
    prior_shrink: float = 4.0
    # How many of the latest earlier-event matches describe the team's form.
    prior_window: int = 24
    # The latest `recent_window` matches count `recent_weight` times as much.
    recent_window: int = 10
    recent_weight: float = 2.0
    match_window: int = 20


@dataclass(frozen=True)
class Strength:
    points: float
    teleop: float
    auto: float
    climb: float
    matches_here: int
    matches_before: int
    confidence: float
    basis: str  # "this_event" | "earlier_events" | "season_typical"


def _weighted_means(matches: Sequence[MatchPoints], params: StrengthParams, window: int) -> dict[str, float | None]:
    ordered = sorted(matches, key=lambda m: (m.time, m.match_key))[-window:] if window > 0 else []
    recent_from = len(ordered) - params.recent_window
    means: dict[str, float | None] = {}
    for name in COMPONENTS:
        num = den = 0.0
        for i, match in enumerate(ordered):
            value = getattr(match, name)
            if value is None:
                continue
            weight = params.recent_weight if i >= recent_from else 1.0
            num += weight * float(value)
            den += weight
        means[name] = num / den if den > 0 else None
    return means


def _shrink(value: float | None, count: float, target: float, k: float) -> float:
    if value is None or count <= 0:
        return target
    return (count * value + k * target) / (count + k)


def estimate_strength(
    here: Iterable[MatchPoints],
    before: Iterable[MatchPoints],
    typical: dict[str, float],
    params: StrengthParams = StrengthParams(),
) -> Strength:
    here = list(here)
    before = list(before)
    before_means = _weighted_means(before, params, params.prior_window)
    here_means = _weighted_means(here, params, params.match_window)
    n_before = min(len(before), params.prior_window)
    n_here = min(len(here), params.match_window)

    values: dict[str, float] = {}
    for name in COMPONENTS:
        prior = _shrink(before_means[name], n_before, float(typical.get(name, 0.0)), params.prior_shrink)
        values[name] = max(0.0, _shrink(here_means[name], n_here, prior, params.event_shrink))

    # Earlier events tell less than this one: a robot changes between events.
    effective = n_here + 0.5 * n_before
    basis = "this_event" if n_here else "earlier_events" if n_before else "season_typical"
    return Strength(
        points=sum(values.values()),
        teleop=values["teleop"],
        auto=values["auto"],
        climb=values["climb"],
        matches_here=n_here,
        matches_before=n_before,
        confidence=round(effective / (effective + 4.0), 4),
        basis=basis,
    )


def points_to_rating(points: float, scale_points: float, shape: float = 1.0) -> float:
    # 0 points -> 0, approaching 100 for the best: 100 * (1 - exp(-(points/scale)^shape)).
    # Unlike a percentile it keeps the very best robots apart, and unlike the old
    # within-event percentile a 90 means the same thing at every event.
    if scale_points <= 0:
        return 0.0
    return 100.0 * (1.0 - math.exp(-((max(0.0, float(points)) / float(scale_points)) ** shape)))


def scale_for_median(median_points: float) -> float:
    return float(median_points) / math.log(2.0)


def curve_for_anchors(median_points: float, p99_points: float) -> tuple[float, float]:
    # (scale, shape) putting the season's median robot at 50 and its 99th percentile at
    # 95, so a skewed season doesn't crowd its best robots against 100.
    shape = math.log(math.log(20.0) / math.log(2.0)) / math.log(float(p99_points) / float(median_points))
    scale = float(median_points) / math.log(2.0) ** (1.0 / shape)
    return scale, shape


def rating_to_points(rating: float, scale_points: float, shape: float = 1.0) -> float:
    rating = min(99.999, max(0.0, float(rating)))
    return float(scale_points) * (-math.log(1.0 - rating / 100.0)) ** (1.0 / shape)
