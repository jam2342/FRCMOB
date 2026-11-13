# Candidate Bernoulli team model. Defaults are an unfitted development recipe.
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np

from app.services.ratings.research_filter import _Block
from app.services.ratings.state_inputs import digest

RECIPE = "flag-rp-laplace-v1"


@dataclass(frozen=True)
class FlagRpParams:
    prior_var: float = 1.0
    match_var: float = 0.01
    event_var: float = 0.1

    def __post_init__(self):
        for value in asdict(self).values():
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("RP variances must be positive and finite")


def sigmoid(x):
    return 1/(1+math.exp(-max(-30.0,min(30.0,x))))


class _RpState:
    def __init__(self):
        self.blocks = {}
        self.latest = {}
        self.observed = self.earned = 0

    def base_rate(self):
        # Beta(1,1) uses only flags already observed this season.
        return (self.earned+1)/(self.observed+2)


class FlagRpModel:
    def __init__(self, spec, params=FlagRpParams()):
        self.spec, self.params = spec, params
        self.states = {rp.name:_RpState() for rp in spec.bonus_rps}

    def _enter(self, state, event, teams):
        block = state.blocks.get(event)
        if block is None:
            block = state.blocks[event] = _Block()
        for team in teams:
            if team in block.index:
                continue
            previous = state.latest.get(team)
            if previous is None:
                rate = state.base_rate()
                block.add(team,math.log(rate/(1-rate))/3,self.params.prior_var)
            else:
                old = state.blocks[previous]
                i = old.index[team]
                block.add(team,float(old.mean[i]),float(old.cov[i,i])+self.params.event_var)
            state.latest[team] = event
        return block

    @staticmethod
    def _sides(card):
        sides = {"red":tuple(str(t).lower() for t in card.red),"blue":tuple(str(t).lower() for t in card.blue)}
        teams = sides["red"]+sides["blue"]
        if len(sides["red"]) != 3 or len(sides["blue"]) != 3 or len(set(teams)) != 6:
            raise ValueError("RP prediction needs six distinct teams")
        return sides

    def predict(self, card):
        if card.comp_level != "qm":
            return {}
        sides = self._sides(card)
        out = {color:{} for color in sides}
        for name, state in self.states.items():
            block = self._enter(state,card.event_key.lower(),sides["red"]+sides["blue"])
            for color, teams in sides.items():
                ids = [block.index[t] for t in teams]
                mean = float(block.mean[ids].sum())
                var = float(block.cov[np.ix_(ids,ids)].sum())
                out[color][name] = self._prediction(state,mean,var)
        return out

    @staticmethod
    def _prediction(state, mean, var):
        return {"probability":sigmoid(mean),"logit_sd":math.sqrt(max(var,0)),
                "observations":state.observed,"base_rate":state.base_rate()}

    def forecast(self, card):
        if card.comp_level != "qm":
            return {}
        sides = self._sides(card)
        out = {color:{} for color in sides}
        for name, state in self.states.items():
            block = state.blocks.get(card.event_key.lower())
            for color, teams in sides.items():
                ids = [block.index[t] for t in teams if block is not None and t in block.index]
                mean = float(block.mean[ids].sum()) if ids else 0.0
                var = float(block.cov[np.ix_(ids,ids)].sum()) if ids else 0.0
                for team in teams:
                    if block is not None and team in block.index:
                        continue
                    previous = state.latest.get(team)
                    if previous is None:
                        rate = state.base_rate()
                        mean += math.log(rate/(1-rate))/3
                        var += self.params.prior_var
                    else:
                        old = state.blocks[previous]
                        i = old.index[team]
                        mean += float(old.mean[i])
                        var += float(old.cov[i,i])+self.params.event_var
                out[color][name] = self._prediction(state,mean,var)
        return out

    def observe(self, card, flags):
        if card.comp_level != "qm":
            return
        sides = self._sides(card)
        for name, state in self.states.items():
            block = self._enter(state,card.event_key.lower(),sides["red"]+sides["blue"])
            for color, teams in sides.items():
                y = flags.get(color,{}).get(name)
                if type(y) is not bool:
                    continue
                ids = [block.index[t] for t in teams]
                p = sigmoid(float(block.mean[ids].sum()))
                cov = block.cov[:block.n,:block.n]
                ph = cov[:,ids].sum(axis=1)
                # One Newton/Laplace step for a Bernoulli logit with Gaussian prior.
                denom = 1+p*(1-p)*float(ph[ids].sum())
                block.mean[:block.n] += ph*(float(y)-p)/denom
                cov -= p*(1-p)*np.outer(ph,ph)/denom
                state.observed += 1
                state.earned += int(y)
            for team in sides["red"]+sides["blue"]:
                i = block.index[team]
                block.cov[i,i] += self.params.match_var

    @property
    def settings_sha256(self):
        return digest({"recipe":RECIPE,"params":asdict(self.params),"spec":self.spec.fingerprint})
