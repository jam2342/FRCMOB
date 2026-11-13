# The team model on a season's total score with every setting a pure number, for the
# rating plan's phase 2 experiments. Beliefs live in units of the season's as-of mean
# per-robot share (RunningUnit), so the same settings run on any game. With the level
# terms off and the stand-in ratios it gives exactly what ScaleFreeTotalModel gives
# (tests check), only faster and with the per-observation numbers a fit needs.
# Research only: nothing in production reads this.
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.services.ratings.replay import MatchCard, MatchResult, Prediction
from app.services.ratings.research_models import RunningUnit
from app.services.ratings.team_model import ComponentParams


@dataclass(frozen=True)
class ScaleFreeParams:
    # SDs in units of the as-of mean share. Defaults: the shipped total-score settings
    # (2026 points) over 2026's mean share, i.e. phase 1's stand-in.
    prior: float = 1.2126  # a team we haven't seen this season
    match: float = 0.0606  # drift after every match
    event: float = 0.3131  # drift entering a new event
    noise: float = 1.2126  # match noise of an alliance total
    beta: float = 0.9  # first belief = exp(beta * last season's z)
    rookie: float = 0.6  # ... or this for a rookie
    # Level terms (plan 3.4), all off by default. Noise variance of an alliance total
    # predicted at x units: noise^2 + noise_count * x + noise_prop^2 * x^2.
    noise_count: float = 0.0
    noise_prop: float = 0.0
    # Drift grows with the team's own level: q^2 (1 + m^2) / 2, the same at m = 1.
    drift_level: bool = False
    # Prior SD grows with the start value: prior^2 + prior_level^2 * m0^2.
    prior_level: float = 0.0

    @classmethod
    def from_points(cls, params: ComponentParams, unit: float, **extra) -> ScaleFreeParams:
        return cls(
            prior=math.sqrt(params.prior_var) / unit,
            match=math.sqrt(params.match_var) / unit,
            event=math.sqrt(params.event_var) / unit,
            noise=math.sqrt(params.noise_var) / unit,
            **extra,
        )


class _Block:
    # One event's beliefs with full covariance, in preallocated arrays.
    __slots__ = ("index", "n", "mean", "cov")

    def __init__(self, capacity: int = 48):
        self.index: dict[str, int] = {}
        self.n = 0
        self.mean = np.zeros(capacity)
        self.cov = np.zeros((capacity, capacity))

    def add(self, team: str, mean: float, var: float) -> None:
        if self.n == len(self.mean):
            size = 2 * self.n
            grown = np.zeros((size, size))
            grown[: self.n, : self.n] = self.cov[: self.n, : self.n]
            self.cov = grown
            self.mean = np.concatenate([self.mean, np.zeros(size - self.n)])
        i = self.n
        self.index[team] = i
        self.mean[i] = mean
        self.cov[i, i] = var
        self.n += 1


class ScaleFreeFilter:
    def __init__(
        self,
        params: ScaleFreeParams = ScaleFreeParams(),
        *,
        z_previous: dict[str, float] | None = None,
        rookies: set[str] | frozenset[str] = frozenset(),
        previous_unit: float | None = None,
        name: str = "scale_free",
        record: bool = False,
    ):
        self.name = name
        self.params = params
        self.z_previous = z_previous or {}
        self.rookies = rookies
        self.unit = RunningUnit(previous_unit)
        self.blocks: dict[str, _Block] = {}
        self.latest: dict[str, str] = {}  # team -> event of its latest belief
        # match_key -> ((x, belief var, noise var) red, (...) blue, unit), when recording.
        self.record = record
        self.detail: dict[str, tuple] = {}

    def _start(self, team: str) -> float:
        z = self.z_previous.get(team)
        if z is not None:
            return math.exp(self.params.beta * z)
        return self.params.rookie if team in self.rookies else 1.0

    def _drift(self, q: float, m: float) -> float:
        if self.params.drift_level:
            m = max(m, 0.0)
            return q * q * (1.0 + m * m) / 2.0
        return q * q

    def noise_var(self, x: float) -> float:
        p = self.params
        x = max(x, 0.0)
        return p.noise * p.noise + p.noise_count * x + p.noise_prop * p.noise_prop * x * x

    def _block(self, event_key: str) -> _Block:
        block = self.blocks.get(event_key)
        if block is None:
            block = self.blocks[event_key] = _Block()
        return block

    def _enter(self, team: str, block: _Block, event_key: str) -> int:
        i = block.index.get(team)
        if i is not None:
            return i
        previous = self.latest.get(team)
        if previous is None:
            m0 = self._start(team)
            p = self.params
            block.add(team, m0, p.prior * p.prior + p.prior_level * p.prior_level * m0 * m0)
        else:
            old = self.blocks[previous]
            j = old.index[team]
            m = float(old.mean[j])
            block.add(team, m, float(old.cov[j, j]) + self._drift(self.params.event, m))
        self.latest[team] = event_key
        return block.index[team]

    def _indices(self, card: MatchCard) -> tuple[_Block, tuple[int, ...], tuple[int, ...]]:
        block = self._block(card.event_key)
        red = tuple(self._enter(t, block, card.event_key) for t in card.red)
        blue = tuple(self._enter(t, block, card.event_key) for t in card.blue)
        return block, red, blue

    @staticmethod
    def _side(block: _Block, idx: tuple[int, ...]) -> tuple[float, float]:
        m, c = block.mean, block.cov
        mean = 0.0
        var = 0.0
        for a in idx:
            mean += m[a]
            for b in idx:
                var += c[a, b]
        return float(mean), float(var)

    def predict(self, card: MatchCard) -> Prediction:
        block, red, blue = self._indices(card)
        u = self.unit.value()
        rm, rb = self._side(block, red)
        bm, bb = self._side(block, blue)
        rr, br = self.noise_var(rm), self.noise_var(bm)
        if self.record:
            self.detail[card.match_key] = ((rm, rb, rr), (bm, bb, br), u)
        return Prediction(rm * u, bm * u, (rb + rr) * u * u, (bb + br) * u * u)

    @staticmethod
    def _update(block: _Block, idx: tuple[int, ...], y: float, noise: float) -> tuple[float, float]:
        n = block.n
        cov = block.cov[:n, :n]
        i, j, k = idx
        ph = cov[:, i] + cov[:, j] + cov[:, k]
        s = float(ph[i] + ph[j] + ph[k]) + noise
        nu = y - float(block.mean[i] + block.mean[j] + block.mean[k])
        gain = ph / s
        block.mean[:n] += gain * nu
        cov -= np.outer(gain, ph)
        return nu, s

    def observe(self, result: MatchResult) -> list[tuple[float, float]]:
        # Returns each observed side's prediction error and its variance, in units, as the
        # filter saw them (blue after red's update): the exact sequential likelihood.
        card = result.card
        block, red, blue = self._indices(card)
        u = self.unit.value()
        # Noise set from the pre-match prediction, the same one predict() serves.
        if self.params.noise_count or self.params.noise_prop:
            noise = (self.noise_var(self._side(block, red)[0]), self.noise_var(self._side(block, blue)[0]))
        else:
            noise = (self.noise_var(0.0),) * 2
        out = []
        for idx, points, components, r in (
            (red, result.red_points, result.red_components, noise[0]),
            (blue, result.blue_points, result.blue_components, noise[1]),
        ):
            if points is None or "total_points" not in (components or {}):
                continue
            out.append(self._update(block, idx, points / u, r))
        q = self.params.match
        for a in red + blue:
            block.cov[a, a] += self._drift(q, float(block.mean[a]))
        self.unit.add(result.red_points)
        self.unit.add(result.blue_points)
        return out

    def belief_units(self, team: str) -> float | None:
        event = self.latest.get(team)
        if event is None:
            return None
        block = self.blocks[event]
        return float(block.mean[block.index[team]])

    def team_points(self, team_key: str, event_key: str) -> float:
        block = self.blocks.get(event_key)
        if block is not None and team_key in block.index:
            return float(block.mean[block.index[team_key]]) * self.unit.value()
        m = self.belief_units(team_key)
        return (m if m is not None else self._start(team_key)) * self.unit.value()
