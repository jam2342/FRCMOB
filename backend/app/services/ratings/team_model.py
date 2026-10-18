# Team strength from alliance results, one match at a time (docs/RATING_ROADMAP.md,
# Phase 1).
#
# TBA counts hub fuel per alliance, so each match says: red's three robots scored this
# many points between them. Every team carries a belief per component, a mean (points
# per match) and a variance (how unsure we are); a match's surprise is shared among its
# three robots in proportion to how unsure we are about each (a Kalman filter on a sum of
# three). Within an event the beliefs keep their full covariance, which makes the update
# exactly a ridge regression on the event's alliance totals pulled toward each team's
# prior. Robots change: the variance grows a little every match and more at each new
# event. Tower (climb) points are recorded per robot, so they update one robot at a time.
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

# Alliance-total components: (name, key in match_alliance_results.components).
HUB_COMPONENTS = (("teleop", "teleop_hub_points"), ("auto", "auto_hub_points"))


@dataclass(frozen=True)
class ComponentParams:
    prior_var: float  # a team we haven't seen this season
    match_var: float  # added after every match the team plays
    event_var: float  # added when the team starts a new event
    noise_var: float  # match-to-match noise of one observation (an alliance total, or one robot's tower)
    # Never enter an event surer than this (variance), however many matches came before:
    # a long season mustn't freeze a robot that changed (v15's 24-match window, as a floor).
    entry_var_floor: float = 0.0


@dataclass(frozen=True)
class TeamModelParams:
    # Chosen on the older 2026 events (evaluate_rating_replay.py --grid-team, coordinate
    # search; the entry floor tested 0-1600 and validation kept 0).
    teleop: ComponentParams = ComponentParams(prior_var=4000.0, match_var=10.0, event_var=300.0, noise_var=4000.0)
    auto: ComponentParams = ComponentParams(prior_var=400.0, match_var=2.0, event_var=150.0, noise_var=400.0)
    tower: ComponentParams = ComponentParams(prior_var=80.0, match_var=1.0, event_var=10.0, noise_var=120.0)
    # A season's whole score as one component (any game; last season's strengths).
    total: ComponentParams = ComponentParams(prior_var=6000.0, match_var=15.0, event_var=400.0, noise_var=6000.0)
    # Duds (a robot that scores ~nothing in a match: dead, tipped, disconnected). The
    # season's base rate (0 = don't track), how many matches of evidence it is worth, and
    # whether predictions allow for duds / a likely dud stops dragging the robot's mean.
    dud_base_rate: float = 0.0
    dud_prior_matches: float = 10.0
    dud_predict: bool = False
    dud_robust: bool = False
    # Context (Phase 4), each off by default:
    # - fouls: foul points an alliance receives = the opponents' foul tendencies;
    # - defense: how much a team cuts the other alliance's teleop;
    # - event offset: a per-event shift in teleop scoring shared by every alliance there
    #   (variance of the shift; 0 = off), so the field's level isn't credited to teams.
    model_fouls: bool = False
    model_defense: bool = False
    event_offset_var: float = 0.0
    fouls: ComponentParams = ComponentParams(prior_var=25.0, match_var=0.5, event_var=5.0, noise_var=150.0)
    defense: ComponentParams = ComponentParams(prior_var=100.0, match_var=1.0, event_var=50.0, noise_var=4000.0)

    def of(self, name: str) -> ComponentParams:
        return getattr(self, name)


def component_priors(results: Iterable, events: set[str] | None = None) -> dict[str, float]:
    # Mean points per robot per component over the given events: the prior for a team
    # with no history. A mean, not a median: alliance totals are sums, and a median
    # (2026 teleop: 39 vs mean ~50) would bias every prediction low.
    sums = {"teleop": 0.0, "auto": 0.0, "tower": 0.0, "fouls": 0.0}
    counts = {"teleop": 0, "auto": 0, "tower": 0, "fouls": 0}
    for result in results:
        if events is not None and result.card.event_key not in events:
            continue
        for components in (result.red_components, result.blue_components):
            for name, key in HUB_COMPONENTS:
                value = components.get(key)
                if value is not None:
                    sums[name] += float(value) / 3.0
                    counts[name] += 1
            for robot in _robot_towers(components):
                if robot is not None:
                    sums["tower"] += robot
                    counts["tower"] += 1
            if components.get("foul_points_received") is not None:
                sums["fouls"] += float(components["foul_points_received"]) / 3.0
                counts["fouls"] += 1
    return {name: (sums[name] / counts[name] if counts[name] else 0.0) for name in sums}


def _robot_towers(components: dict) -> list[float | None]:
    auto = components.get("robot_auto_tower_points") or [None, None, None]
    endgame = components.get("robot_endgame_tower_points") or [None, None, None]
    out = []
    for a, e in zip(auto, endgame):
        out.append(None if a is None and e is None else float(a or 0.0) + float(e or 0.0))
    return out


@dataclass
class _EventBlock:
    # One event's beliefs for one hub component, with full covariance.
    index: dict[str, int] = field(default_factory=dict)
    mean: np.ndarray = field(default_factory=lambda: np.zeros(0))
    cov: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))

    def add(self, team: str, mean: float, var: float) -> None:
        n = len(self.index)
        self.index[team] = n
        self.mean = np.append(self.mean, mean)
        cov = np.zeros((n + 1, n + 1))
        cov[:n, :n] = self.cov
        cov[n, n] = var
        self.cov = cov


@dataclass(frozen=True)
class Belief:
    mean: float
    var: float


class SeasonTeamModel:
    # Feed results in playing order with observe(); ask predict() before each match.
    def __init__(
        self,
        priors: dict[str, float],
        params: TeamModelParams = TeamModelParams(),
        name: str = "team_model",
        *,
        components: tuple[tuple[str, str], ...] = HUB_COMPONENTS,
        team_multipliers: dict[str, float] | None = None,
        week_multipliers: list[float] | None = None,
        team_var_multipliers: dict[str, float] | None = None,
    ):
        self.name = name
        self.priors = priors
        self.params = params
        self.hub = components
        # A team's first belief this season: the prior times its multiplier (last
        # season's strength, rookie) times the multiplier for the week it first plays.
        self.team_multipliers = team_multipliers or {}
        self.week_multipliers = week_multipliers or []
        # A team we knew last season can start less unsure than a stranger.
        self.team_var_multipliers = team_var_multipliers or {}
        self.season_start: int | None = None
        self.blocks: dict[tuple[str, str], _EventBlock] = {}
        self.history: dict[str, list[str]] = {}  # events each team entered, in order
        self.towers: dict[tuple[str, str], Belief] = {}  # (team, event) -> latest tower belief there
        self.event_start: dict[str, int] = {}
        self.dud_evidence: dict[str, float] = {}  # summed posterior chance each match was a dud
        self.dud_matches: dict[str, int] = {}
        # Per-team side components (Phase 4), kept in event blocks like scoring.
        self.sides: tuple[str, ...] = tuple(
            name for name, on in (("fouls", params.model_fouls), ("defense", params.model_defense)) if on
        )

    # ── Beliefs ──

    def _enter(self, team: str, event_key: str) -> None:
        events = self.history.setdefault(team, [])
        if event_key in events:
            return
        previous = events[-1] if events else None
        first = self._first_multiplier(team, event_key) if previous is None else 1.0
        for name, _key in self.hub:
            params = self.params.of(name)
            if previous is None:
                mean, var = self.priors[name] * first, params.prior_var * self.team_var_multipliers.get(team, 1.0)
            else:
                old = self.blocks[(previous, name)]
                i = old.index[team]
                # A new event: robots get rebuilt between events.
                mean, var = float(old.mean[i]), max(float(old.cov[i, i]), params.entry_var_floor) + params.event_var
            self.blocks.setdefault((event_key, name), _EventBlock()).add(team, mean, var)
        for name in self.sides:
            params = self.params.of(name)
            if previous is None:
                mean, var = (self.priors.get("fouls", 0.0) if name == "fouls" else 0.0), params.prior_var
            else:
                old = self.blocks[(previous, name)]
                i = old.index[team]
                mean, var = float(old.mean[i]), float(old.cov[i, i]) + params.event_var
            self.blocks.setdefault((event_key, name), _EventBlock()).add(team, mean, var)
        tower = self.params.tower
        self.towers[(team, event_key)] = (
            Belief(self.priors.get("tower", 0.0) * first, tower.prior_var)
            if previous is None
            else Belief(
                self.towers[(team, previous)].mean,
                max(self.towers[(team, previous)].var, tower.entry_var_floor) + tower.event_var,
            )
        )
        events.append(event_key)

    def _first_multiplier(self, team: str, event_key: str) -> float:
        multiplier = self.team_multipliers.get(team, 1.0)
        start = self.event_start.get(event_key)
        if self.week_multipliers and start is not None and self.season_start is not None:
            week = max(0, int((start - self.season_start) // (7 * 86400)))
            multiplier *= self.week_multipliers[min(week, len(self.week_multipliers) - 1)]
        return multiplier

    def belief(self, team: str, event_key: str | None = None, event_start: int | None = None) -> dict[str, Belief]:
        # The team's belief per component at `event_key`: its latest there if it has
        # played there; otherwise as it would enter that event, from its last event that
        # started before (`event_start`, else the event's own recorded start, else its
        # latest event); the season prior when it has none.
        events = self.history.get(team, [])
        if event_key is not None and event_key in events:
            source, widen = event_key, False
        else:
            start = event_start if event_start is not None else self.event_start.get(event_key or "")
            earlier = [e for e in events if start is None or self.event_start.get(e, 0) < start]
            source, widen = (earlier[-1], True) if earlier else (None, False)
        out: dict[str, Belief] = {}
        for name, _key in self.hub:
            params = self.params.of(name)
            if source is None:
                out[name] = Belief(self.priors[name] * self.team_multipliers.get(team, 1.0), params.prior_var)
                continue
            block = self.blocks[(source, name)]
            i = block.index[team]
            var = float(block.cov[i, i])
            out[name] = Belief(float(block.mean[i]), max(var, params.entry_var_floor) + params.event_var if widen else var)
        if source is None:
            out["tower"] = Belief(self.priors.get("tower", 0.0) * self.team_multipliers.get(team, 1.0), self.params.tower.prior_var)
        else:
            tower = self.towers[(team, source)]
            floor = self.params.tower.entry_var_floor
            out["tower"] = Belief(tower.mean, max(tower.var, floor) + self.params.tower.event_var if widen else tower.var)
        return out

    def alliance_strength(self, event_key: str, teams, event_start: int | None = None) -> dict[str, tuple[float, float]]:
        # An alliance's expected points as (mean, variance), whole and by part, from
        # current beliefs: any three teams, played together or not (pick lists).
        mean, var, _fouls, _fouls_var, hub, tower = self._side(event_key, tuple(teams), (), event_start)
        return {"total": (mean, var), "hub": hub, "tower": tower}

    def _side(self, event_key: str, teams: tuple[str, ...], others: tuple[str, ...], event_start: int | None):
        mean = var = tower_mean = tower_var = 0.0
        for team in teams:
            for name, belief in self.belief(team, event_key, event_start).items():
                if name == "tower":
                    tower_mean += belief.mean
                    tower_var += belief.var
                else:
                    mean += belief.mean
                    var += belief.var
        var += sum(self.params.of(name).noise_var for name, _key in self.hub)
        tower_var += 3 * self.params.tower.noise_var
        block = self.blocks.get((event_key, "teleop"))
        offset_key = f"__event__{event_key}"
        if self.params.event_offset_var and block is not None and offset_key in block.index:
            i = block.index[offset_key]
            mean += float(block.mean[i])
            var += float(block.cov[i, i])
        if self.params.dud_predict and self.params.dud_base_rate > 0:
            for team in teams:
                belief = self.belief(team, event_key, event_start)
                contribution = max(0.0, sum(belief[name].mean for name, _key in self.hub))
                p = self.dud_rate(team)
                mean -= p * contribution
                var += p * (1 - p) * contribution * contribution
        hub = (mean, var)
        mean, var = mean + tower_mean, var + tower_var
        fouls = fouls_var = 0.0
        for team in others:
            if "defense" in self.sides:
                d = self.side_belief(team, "defense", event_key, event_start)
                mean -= d.mean
                var += d.var
            if "fouls" in self.sides:
                f = self.side_belief(team, "fouls", event_key, event_start)
                fouls += f.mean
                fouls_var += f.var
        if "fouls" in self.sides:
            fouls_var += self.params.fouls.noise_var
        return mean, var, fouls, fouls_var, hub, (tower_mean, tower_var)

    def side_belief(self, team: str, name: str, event_key: str | None = None, event_start: int | None = None) -> Belief:
        # A side component (fouls, defense) at `event_key`, by the same rules as belief().
        params = self.params.of(name)
        events = self.history.get(team, [])
        if event_key is not None and event_key in events and (event_key, name) in self.blocks:
            block = self.blocks[(event_key, name)]
            i = block.index[team]
            return Belief(float(block.mean[i]), float(block.cov[i, i]))
        start = event_start if event_start is not None else self.event_start.get(event_key or "")
        earlier = [e for e in events if (start is None or self.event_start.get(e, 0) < start) and (e, name) in self.blocks]
        if earlier:
            block = self.blocks[(earlier[-1], name)]
            i = block.index[team]
            return Belief(float(block.mean[i]), float(block.cov[i, i]) + params.event_var)
        return Belief(self.priors.get("fouls", 0.0) if name == "fouls" else 0.0, params.prior_var)

    def _offset(self, event_key: str) -> str | None:
        # The event's shared teleop shift lives in the teleop block as a pseudo-team.
        if not self.params.event_offset_var:
            return None
        key = f"__event__{event_key}"
        block = self.blocks.setdefault((event_key, "teleop"), _EventBlock())
        if key not in block.index:
            block.add(key, 0.0, self.params.event_offset_var)
        return key

    def forecast(self, event_key: str, red: tuple[str, ...], blue: tuple[str, ...], event_start: int | None = None):
        # The prediction for a match from current beliefs, without entering anyone into
        # the event: safe on a shared, cached model. Ignores covariance between
        # teammates (small next to the match noise).
        from app.services.ratings.replay import Prediction

        rm, rv, rf, rfv, rh, rt = self._side(event_key, tuple(red), tuple(blue), event_start)
        bm, bv, bf, bfv, bh, bt = self._side(event_key, tuple(blue), tuple(red), event_start)
        return Prediction(rm, bm, rv, bv, rf, bf, rfv, bfv, rh, bh, rt, bt)

    def dud_rate(self, team: str) -> float:
        # Shrunk toward the season's base rate: a weak robot's dud can't be told from a
        # bad match, so its evidence stays near the base rate unless it has many.
        p = self.params
        return (p.dud_prior_matches * p.dud_base_rate + self.dud_evidence.get(team, 0.0)) / (
            p.dud_prior_matches + self.dud_matches.get(team, 0)
        )

    def _hub_means(self, teams, event_key: str) -> dict[str, dict[str, float]]:
        out = {}
        for name, _key in self.hub:
            block = self.blocks[(event_key, name)]
            out[name] = {team: float(block.mean[block.index[team]]) for team in teams}
        return out

    def _dud_adjust(self, teams, event_key: str, mean: float, var: float) -> tuple[float, float]:
        # Expected points and variance when each robot may be a dud this match.
        if not (self.params.dud_predict and self.params.dud_base_rate > 0):
            return mean, var
        hub = self._hub_means(teams, event_key)
        for team in teams:
            contribution = max(0.0, sum(hub[name][team] for name, _key in self.hub))
            p = self.dud_rate(team)
            mean -= p * contribution
            var += p * (1 - p) * contribution * contribution
        return mean, var

    def _dud_posteriors(self, teams, event_key: str, components: dict) -> dict[str, float] | None:
        # Chance each robot was a dud, given the alliance's hub total, before updating.
        values = [components.get(key) for _name, key in self.hub]
        if any(v is None for v in values):
            return None
        observed = float(sum(values))
        hub = self._hub_means(teams, event_key)
        contributions = {team: max(0.0, sum(hub[name][team] for name, _key in self.hub)) for team in teams}
        mean = sum(contributions.values())
        var = 0.0
        for name, _key in self.hub:
            block = self.blocks[(event_key, name)]
            h = np.zeros(len(block.index))
            for team in teams:
                h[block.index[team]] = 1.0
            var += float(h @ block.cov @ h) + self.params.of(name).noise_var
        rates = {team: self.dud_rate(team) for team in teams}

        def log_normal(x: float) -> float:
            return -0.5 * (x * x) / var

        logs = {None: math.log(max(1e-12, 1.0 - sum(rates.values()))) + log_normal(observed - mean)}
        for team in teams:
            logs[team] = math.log(max(1e-12, rates[team])) + log_normal(observed - (mean - contributions[team]))
        top = max(logs.values())
        weights = {k: math.exp(v - top) for k, v in logs.items()}
        total = sum(weights.values())
        return {team: weights[team] / total for team in teams}

    def team_points(self, team_key: str, event_key: str) -> float:
        return sum(b.mean for b in self.belief(team_key, event_key).values())

    # ── Replay interface (ratings/replay.py) ──

    def predict(self, card):
        from app.services.ratings.replay import Prediction

        self.event_start.setdefault(card.event_key, card.event_start)
        if self.season_start is None or card.event_start < self.season_start:
            self.season_start = card.event_start
        for team in card.red + card.blue:
            self._enter(team, card.event_key)
        offset = self._offset(card.event_key)
        out = []
        for teams, others in ((card.red, card.blue), (card.blue, card.red)):
            mean = var = 0.0
            for name, _key in self.hub:
                block = self.blocks[(card.event_key, name)]
                members = teams + ((offset,) if offset and name == "teleop" else ())
                m, v = self._sum(block, members)
                mean += m
                var += v + self.params.of(name).noise_var
            hub_mean, hub_var = self._dud_adjust(teams, card.event_key, mean, var)
            tower_mean = sum(self.towers[(team, card.event_key)].mean for team in teams)
            tower_var = sum(self.towers[(team, card.event_key)].var for team in teams) + 3 * self.params.tower.noise_var
            mean, var = hub_mean + tower_mean, hub_var + tower_var
            fouls = fouls_var = 0.0
            if "defense" in self.sides:
                m, v = self._sum(self.blocks[(card.event_key, "defense")], others)
                mean -= m
                var += v
            if "fouls" in self.sides:
                fouls, fouls_var = self._sum(self.blocks[(card.event_key, "fouls")], others)
                fouls_var += self.params.fouls.noise_var
            out.append((mean, var, fouls, fouls_var, (hub_mean, hub_var), (tower_mean, tower_var)))
        (rm, rv, rf, rfv, rh, rt), (bm, bv, bf, bfv, bh, bt) = out
        return Prediction(rm, bm, rv, bv, rf, bf, rfv, bfv, rh, bh, rt, bt)

    @staticmethod
    def _sum(block: _EventBlock, members) -> tuple[float, float]:
        h = np.zeros(len(block.index))
        for member in members:
            h[block.index[member]] = 1.0
        return float(h @ block.mean), float(h @ block.cov @ h)

    def observe(self, result) -> None:
        card = result.card
        self.event_start.setdefault(card.event_key, card.event_start)
        if self.season_start is None or card.event_start < self.season_start:
            self.season_start = card.event_start
        for team in card.red + card.blue:
            self._enter(team, card.event_key)
        offset = self._offset(card.event_key)
        # Side components first, from beliefs before this match moves anything.
        suppression: dict[str, float] = {}
        if "defense" in self.sides:
            teleop = self.blocks[(card.event_key, "teleop")]
            defense = self.blocks[(card.event_key, "defense")]
            pending = []
            for teams, others, components in ((card.red, card.blue, result.red_components), (card.blue, card.red, result.blue_components)):
                observed = (components or {}).get("teleop_hub_points")
                suppression[teams[0]] = self._sum(defense, others)[0]
                if observed is not None:
                    expected = self._sum(teleop, teams + ((offset,) if offset else ()))[0]
                    pending.append((others, expected - float(observed)))
            for others, deficit in pending:
                self._update_sum(defense, others, deficit, self.params.defense)
        if "fouls" in self.sides:
            fouls = self.blocks[(card.event_key, "fouls")]
            for others, components in ((card.blue, result.red_components), (card.red, result.blue_components)):
                received = (components or {}).get("foul_points_received")
                if received is not None:
                    self._update_sum(fouls, others, float(received), self.params.fouls)
        for teams, components in ((card.red, result.red_components), (card.blue, result.blue_components)):
            duds = self._dud_posteriors(teams, card.event_key, components or {}) if self.params.dud_base_rate > 0 else None
            hub = self._hub_means(teams, card.event_key) if duds and self.params.dud_robust else None
            for name, key in self.hub:
                value = components.get(key) if components else None
                if value is None:
                    continue
                observed = float(value)
                if hub is not None:
                    # Fill in what a likely dud would have scored, so a breakdown doesn't
                    # read as the robot getting worse.
                    observed += sum(duds[team] * max(0.0, hub[name][team]) for team in teams)
                members = teams
                if name == "teleop":
                    # Defense the other side was expected to play explains part of a low total.
                    observed += suppression.get(teams[0], 0.0)
                    if offset:
                        members = teams + (offset,)
                self._update_sum(self.blocks[(card.event_key, name)], members, observed, self.params.of(name))
            if duds is not None:
                for team, q in duds.items():
                    self.dud_evidence[team] = self.dud_evidence.get(team, 0.0) + q
                    self.dud_matches[team] = self.dud_matches.get(team, 0) + 1
            for team, robot in zip(teams, _robot_towers(components or {})):
                if robot is not None:
                    self._update_one(team, card.event_key, robot)
        for team in card.red + card.blue:
            for name in [n for n, _k in self.hub] + list(self.sides):
                block = self.blocks[(card.event_key, name)]
                i = block.index[team]
                block.cov[i, i] += self.params.of(name).match_var
            tower = self.towers[(team, card.event_key)]
            self.towers[(team, card.event_key)] = Belief(tower.mean, tower.var + self.params.tower.match_var)

    @staticmethod
    def _update_sum(block: _EventBlock, teams: tuple[str, ...], observed: float, params: ComponentParams) -> None:
        h = np.zeros(len(block.index))
        for team in teams:
            h[block.index[team]] = 1.0
        ph = block.cov @ h
        s = float(h @ ph) + params.noise_var
        gain = ph / s
        block.mean = block.mean + gain * (observed - float(h @ block.mean))
        block.cov = block.cov - np.outer(gain, ph)
        block.cov = (block.cov + block.cov.T) / 2.0

    def _update_one(self, team: str, event_key: str, observed: float) -> None:
        belief = self.towers[(team, event_key)]
        s = belief.var + self.params.tower.noise_var
        gain = belief.var / s
        self.towers[(team, event_key)] = Belief(belief.mean + gain * (observed - belief.mean), belief.var * (1.0 - gain))

