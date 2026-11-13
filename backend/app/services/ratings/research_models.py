# Models for the rating research harness: public-style baselines (EPA, ridge cOPR with a
# prior) and the team model run on any game's total score. Each is as-of by construction:
# predict() sees no score, and every starting belief uses earlier seasons or earlier
# matches only. All start a team from the same place (last season's strength, rookie
# discount) so a comparison isolates the update rule.
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from statistics import median

import numpy as np

from app.services.ratings.replay import MatchCard, MatchResult, Prediction
from app.services.ratings.team_model import ComponentParams, SeasonTeamModel, TeamModelParams

TOTAL = (("total", "total_points"),)
PRIOR_BETA = 0.9  # shipped 2026 values (constants.py)
ROOKIE_MULTIPLIER = 0.6
UNIT_PSEUDO_SCORES = 24  # alliance scores the previous season's unit is worth


def last_season_z(results: list[MatchResult], min_matches: int = 8, clip: float = 2.5) -> dict[str, float]:
    # Each team's end-of-season strength as z = ln(points / the season's median team), by
    # the team model on whole scores: the same as scripts/build_prior_strengths.py.
    shares = [v / 3.0 for r in results for v in (r.red_points, r.blue_points) if v is not None]
    model = SeasonTeamModel({"total": sum(shares) / len(shares) if shares else 0.0, "tower": 0.0}, TeamModelParams(), components=TOTAL)
    played: dict[str, int] = defaultdict(int)
    for result in results:
        model.predict(result.card)
        model.observe(result)
        for team in result.card.red + result.card.blue:
            played[team] += 1
    points = {team: model.belief(team)["total"].mean for team in played}
    qualified = [p for team, p in points.items() if played[team] >= min_matches]
    typical = median(qualified) if qualified else 1.0
    return {team: max(-clip, min(clip, math.log(max(p, 1.0) / typical))) for team, p in points.items()}


def start_multipliers(z_previous: dict[str, float], rookies: set[str], *, beta: float = PRIOR_BETA, rookie: float = ROOKIE_MULTIPLIER) -> dict[str, float]:
    out = {team: math.exp(beta * z) for team, z in z_previous.items()}
    for team in rookies:
        out.setdefault(team, rookie)
    return out


def rookies_of(rookie_year: dict[str, int], season: int) -> set[str]:
    return {team for team, year in rookie_year.items() if year == season}


def mean_share(results: list[MatchResult]) -> float:
    shares = [v / 3.0 for r in results for v in (r.red_points, r.blue_points) if v is not None]
    return sum(shares) / len(shares) if shares else 0.0


class RunningUnit:
    # The season's scoring unit, as-of: mean points per robot over earlier alliance
    # scores, with the previous season's mean worth UNIT_PSEUDO_SCORES scores to start.
    def __init__(self, previous: float | None):
        self.previous = previous
        self.total = 0.0
        self.count = 0

    def value(self) -> float:
        n0 = UNIT_PSEUDO_SCORES if self.previous else 0
        denominator = n0 + self.count
        value = ((self.previous or 0.0) * n0 + self.total) / denominator if denominator else 0.0
        # The very first match, or only shutouts so far: something positive to divide by.
        return value if value > 0 else 30.0

    def add(self, points: float | None) -> None:
        if points is not None:
            self.total += points / 3.0
            self.count += 1


def _seen_teams(card: MatchCard) -> tuple[str, ...]:
    return card.red + card.blue


# ── The team model on any game's total score ─────────────────────────────

class ScaleFreeTotalModel:
    # SeasonTeamModel on the whole score without fouls, in units of the season's as-of
    # mean per-robot share. Its settings are the shipped total-score settings
    # (TeamModelParams().total, 2026 points) divided by 2026's mean share squared, so the
    # same ratios run on every game. A stand-in until phase 2 fits them from data.
    def __init__(self, multipliers: dict[str, float], previous_unit: float | None, reference_unit: float, *, name: str = "team_model_total"):
        self.name = name
        self.unit = RunningUnit(previous_unit)
        p = TeamModelParams().total
        r2 = reference_unit * reference_unit
        # No tower in a total-only game: zero its variances so they don't leak into the spread.
        scaled = replace(TeamModelParams(), tower=ComponentParams(0.0, 0.0, 0.0, 0.0), total=replace(p, prior_var=p.prior_var / r2, match_var=p.match_var / r2, event_var=p.event_var / r2, noise_var=p.noise_var / r2, entry_var_floor=p.entry_var_floor / r2))
        self.inner = SeasonTeamModel({"total": 1.0, "tower": 0.0}, scaled, name=name, components=TOTAL, team_multipliers=multipliers)

    def predict(self, card: MatchCard) -> Prediction:
        u = self.unit.value()
        p = self.inner.predict(card)
        return replace(p, red_mean=p.red_mean * u, blue_mean=p.blue_mean * u, red_var=p.red_var * u * u, blue_var=p.blue_var * u * u)

    def observe(self, result: MatchResult) -> None:
        u = self.unit.value()

        def side(points: float | None, components: dict) -> tuple[float | None, dict]:
            # Only the total: a game's per-robot fields (2026 towers) are not this model's.
            if points is None or "total_points" not in components:
                return None, {}
            return points / u, {"total_points": points / u}

        red_points, red_components = side(result.red_points, result.red_components)
        blue_points, blue_components = side(result.blue_points, result.blue_components)
        self.inner.observe(replace(result, red_points=red_points, blue_points=blue_points, red_components=red_components, blue_components=blue_components))
        self.unit.add(result.red_points)
        self.unit.add(result.blue_points)

    def team_points(self, team_key: str, event_key: str) -> float:
        return self.inner.team_points(team_key, event_key) * self.unit.value()


# ── Statbotics EPA ───────────────────────────────────────────────────────

def _ramp(n: int, start: int, end: int, a: float, b: float) -> float:
    if n <= start:
        return a
    if n >= end:
        return b
    return a + (b - a) * (n - start) / (end - start)


class EpaClone:
    # ΔEPA = K/(1+M) × ((own − own EPA sum) − M × (opp − opp EPA sum)), K 0.5 → 0.3 over a
    # team's matches 6-12, M 0 → 1 over matches 12-36 (statbotics.io/blog/epa). The
    # alliance error is shared by its three robots. No spread, so the harness fits a
    # logistic on the margin.
    def __init__(self, multipliers: dict[str, float], previous_unit: float | None, name: str = "epa_clone"):
        self.name = name
        self.multipliers = multipliers
        self.unit = RunningUnit(previous_unit)
        self.epa: dict[str, float] = {}
        self.played: dict[str, int] = defaultdict(int)

    def _value(self, team: str) -> float:
        if team not in self.epa:
            self.epa[team] = self.unit.value() * self.multipliers.get(team, 1.0)
        return self.epa[team]

    def predict(self, card: MatchCard) -> Prediction:
        return Prediction(sum(self._value(t) for t in card.red), sum(self._value(t) for t in card.blue))

    def observe(self, result: MatchResult) -> None:
        card = result.card
        red = sum(self._value(t) for t in card.red)
        blue = sum(self._value(t) for t in card.blue)
        err_red = result.red_points - red if result.red_points is not None else None
        err_blue = result.blue_points - blue if result.blue_points is not None else None
        for teams, own, opp in ((card.red, err_red, err_blue), (card.blue, err_blue, err_red)):
            for team in teams:
                n = self.played[team]
                k = _ramp(n, 6, 12, 0.5, 0.3)
                m = _ramp(n, 12, 36, 0.0, 1.0)
                if own is not None:
                    self.epa[team] += k / (1 + m) * (own - m * (opp if opp is not None else 0.0)) / 3.0
        for team in _seen_teams(card):
            self.played[team] += 1
        self.unit.add(result.red_points)
        self.unit.add(result.blue_points)

    def team_points(self, team_key: str, event_key: str) -> float:
        return self._value(team_key)


# ── Ridge cOPR with a prior ──────────────────────────────────────────────

@dataclass
class _Event:
    index: dict[str, int]
    x0: list[float]
    rows: list[tuple[tuple[int, ...], float]]
    solution: np.ndarray


class RidgePriorOpr:
    # Per event: least squares on alliance totals, penalised toward each team's value when
    # it entered the event (its estimate from earlier events, else the season prior), one
    # pretend match of weight (TBA's ridge cOPR, github.com/the-blue-alliance PR 11189).
    def __init__(self, multipliers: dict[str, float], previous_unit: float | None, *, penalty: float = 1.0, name: str = "ridge_prior"):
        self.name = name
        self.multipliers = multipliers
        self.unit = RunningUnit(previous_unit)
        self.penalty = penalty
        self.latest: dict[str, float] = {}
        self.events: dict[str, _Event] = {}

    def _entry_value(self, team: str) -> float:
        return self.latest.get(team, self.unit.value() * self.multipliers.get(team, 1.0))

    def _estimate(self, team: str, event_key: str) -> float:
        event = self.events.get(event_key)
        if event is not None and team in event.index:
            return float(event.solution[event.index[team]])
        return self._entry_value(team)

    def predict(self, card: MatchCard) -> Prediction:
        return Prediction(
            sum(self._estimate(t, card.event_key) for t in card.red),
            sum(self._estimate(t, card.event_key) for t in card.blue),
        )

    def observe(self, result: MatchResult) -> None:
        card = result.card
        event = self.events.setdefault(card.event_key, _Event({}, [], [], np.zeros(0)))
        for team in _seen_teams(card):
            if team not in event.index:
                event.index[team] = len(event.index)
                event.x0.append(self._entry_value(team))
        for teams, points in ((card.red, result.red_points), (card.blue, result.blue_points)):
            if points is not None:
                event.rows.append((tuple(event.index[t] for t in teams), points))
        n = len(event.index)
        a = np.zeros((len(event.rows), n))
        b = np.zeros(len(event.rows))
        for i, (idx, points) in enumerate(event.rows):
            a[i, list(idx)] = 1.0
            b[i] = points
        x0 = np.array(event.x0)
        event.solution = np.linalg.solve(a.T @ a + self.penalty * np.eye(n), a.T @ b + self.penalty * x0)
        for team in _seen_teams(card):
            self.latest[team] = float(event.solution[event.index[team]])
        self.unit.add(result.red_points)
        self.unit.add(result.blue_points)

    def team_points(self, team_key: str, event_key: str) -> float:
        return self._estimate(team_key, event_key)


# ── Margin Elo (phase 3 stacking input) ─────────────────────────────────

class MarginElo:
    # Elo on the full score margin, fouls included (Sykes-style: what a points model can't
    # see, such as fouls drawn and opponents held down). Ratings are in units of the as-of
    # mean share, so k carries across games. Each side's three teams share the error; the
    # K ramp is EPA's (0.5 -> 0.3 over matches 6-12) times k_scale. No spread: the harness
    # fits a logistic on the margin.
    def __init__(self, multipliers: dict[str, float], previous_unit: float | None, *, k_scale: float = 0.5, name: str = "margin_elo"):
        self.name = name
        self.multipliers = multipliers
        self.unit = RunningUnit(previous_unit)
        self.k_scale = k_scale
        self.rating: dict[str, float] = {}
        self.played: dict[str, int] = defaultdict(int)

    def _value(self, team: str) -> float:
        if team not in self.rating:
            self.rating[team] = self.multipliers.get(team, 1.0)
        return self.rating[team]

    def predict(self, card: MatchCard) -> Prediction:
        u = self.unit.value()
        # Only the difference means anything; the level is the ratings' sum in points.
        return Prediction(u * sum(self._value(t) for t in card.red), u * sum(self._value(t) for t in card.blue))

    def observe(self, result: MatchResult) -> None:
        card = result.card
        u = self.unit.value()
        if result.red_score is not None and result.blue_score is not None:
            expected = sum(self._value(t) for t in card.red) - sum(self._value(t) for t in card.blue)
            error = (result.red_score - result.blue_score) / u - expected
            for teams, sign in ((card.red, 1.0), (card.blue, -1.0)):
                for team in teams:
                    k = self.k_scale * _ramp(self.played[team], 6, 12, 0.5, 0.3)
                    self.rating[team] += sign * k * error / 3.0
        for team in _seen_teams(card):
            self.played[team] += 1
        self.unit.add(result.red_points)
        self.unit.add(result.blue_points)

    def team_points(self, team_key: str, event_key: str) -> float:
        return self._value(team_key) * self.unit.value()
