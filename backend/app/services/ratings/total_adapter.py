# A total-state model exposes its actual component, never fabricated auto/climb shares.
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import math

from app.services.ratings.replay import MatchCard
from app.services.ratings.team_model import Belief


class TotalStateAdapter:
    component_names = ("total",)
    bonus_rp_supported = False

    def __init__(self, model, spec):
        if spec.tier != "A":
            raise ValueError("the total-state adapter requires a declared Tier A spec")
        self.inner, self.spec = model, spec
        self.name = "state-map-total-v1"
        self.history = {}
        self.event_start = {}
        self.season_start = None

    def _team(self, team):
        return str(team).lower()

    def belief(self, team, event_key=None, event_start=None):
        team = self._team(team)
        event = event_key or self.inner.latest.get(team)
        u = self.inner.unit.value()
        if event is None:
            return {"total": Belief(self.inner._start(team)*u,self.inner.params.prior**2*u*u)}
        block = self.inner.blocks.get(event)
        if block is None or team not in block.index:
            # A read of a future event cannot move the model's latest-event pointers.
            model = copy.deepcopy(self.inner)
            block = model._block(event)
            i = model._enter(team,block,event)
        else:
            i = block.index[team]
        return {"total": Belief(float(block.mean[i])*u,max(float(block.cov[i,i]),0)*u*u)}

    def prior_variance(self, team):
        return self.inner.params.prior**2*self.inner.unit.value()**2

    def predict(self, card):
        return self.inner.predict(card)

    def forecast(self, event_key, red, blue, event_start=None):
        teams = tuple(self._team(t) for t in red)+tuple(self._team(t) for t in blue)
        if len(red) != 3 or len(blue) != 3 or len(set(teams)) != 6:
            raise ValueError("a forecast needs six distinct teams")
        card = MatchCard(f"{event_key}_preview",event_key,0,"qm",event_start or 0,teams[:3],teams[3:])
        return copy.deepcopy(self.inner).predict(card)

    def alliance_strength(self, event_key, teams, event_start=None):
        keys = tuple(self._team(t) for t in teams)
        if len(keys) != 3 or len(set(keys)) != 3:
            raise ValueError("an alliance needs three distinct teams")
        model = copy.deepcopy(self.inner)
        block = model._block(event_key)
        ids = tuple(model._enter(t,block,event_key) for t in keys)
        mean,var = model._side(block,ids)
        u = model.unit.value()
        return {"total":(mean*u,(var+model.noise_var(mean))*u*u),"hub":None,"tower":None}

    def team_points(self, team, event_key):
        return self.belief(team,event_key)["total"].mean

    def observe(self, result):
        card = result.card
        self.event_start[card.event_key] = min(self.event_start.get(card.event_key,card.event_start),card.event_start)
        self.season_start = min(self.season_start if self.season_start is not None else card.event_start,card.event_start)
        for team in card.red+card.blue:
            history = self.history.setdefault(team,[])
            if card.event_key not in history:
                history.append(card.event_key)
        return self.inner.observe(result)


@dataclass
class TotalStateRun:
    season: int
    model: TotalStateAdapter
    played_total: dict = field(default_factory=dict)
    played_here: dict = field(default_factory=dict)
    before_match: dict = field(default_factory=dict)
    changes: dict = field(default_factory=dict)
    results: list = field(default_factory=list)


def replay_adapter(inputs, params, spec):
    adapter = TotalStateAdapter(inputs.model(params),spec)
    run = TotalStateRun(spec.season,adapter)
    for result in inputs.research.season.results:
        card = result.card
        prediction = adapter.predict(card)
        seen = min(run.played_here.get((t,card.event_key),0) for t in card.red+card.blue)
        run.before_match[card.match_key] = (prediction,seen)
        before = {t:adapter.team_points(t,card.event_key) for t in card.red+card.blue}
        adapter.observe(result)
        for team in card.red+card.blue:
            run.changes.setdefault((team,card.event_key),[]).append((card.match_key,before[team],adapter.team_points(team,card.event_key)))
            run.played_here[(team,card.event_key)] = run.played_here.get((team,card.event_key),0)+1
            run.played_total[team] = run.played_total.get(team,0)+1
    run.results = inputs.research.season.results
    return run


def rating_confidence(model, team, event_key):
    variance = model.belief(team,event_key)["total"].var
    prior = model.prior_variance(team)
    return max(0.0,min(1.0,1-math.sqrt(variance/prior))) if prior > 0 else 0.0
