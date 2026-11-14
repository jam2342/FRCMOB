# Research-only independent component states, sharing the total model's scoring unit.
from __future__ import annotations

from dataclasses import asdict
import math

import numpy as np

from app.services.ratings.component_spec import number
from app.services.ratings.replay import Prediction
from app.services.ratings.research_filter import _Block
from app.services.ratings.research_models import RunningUnit


class ComponentFilter:
    def __init__(self, spec, params, *, z_previous=None, rookies=(), previous_unit=None):
        if params.margin_cov or params.drift_level or params.prior_level or params.noise_prop:
            raise ValueError("component screen supports counting noise without level drift/prior or margin covariance")
        if any(number(v) is None or v <= 0 for v in (params.prior,params.match,params.event,params.noise,params.beta,params.rookie)) or number(params.noise_count) is None or params.noise_count < 0:
            raise ValueError("invalid component state parameters")
        self.spec,self.params = spec,params
        self.names = tuple(c.name for c in spec.components)+(() if spec.tier == "A" else ("other",))
        self.robot = {c.name for c in spec.components if c.level == "robot"}
        self.blocks = {name:{} for name in self.names}
        self.latest = {}
        self.unit = RunningUnit(previous_unit)
        self.z_previous,self.rookies = z_previous or {},set(rookies)
        self.population = {name:0. for name in self.names}
        self.population_total = 0.

    def fractions(self):
        if self.population_total <= 0:
            return {name:1/len(self.names) for name in self.names}
        return {name:value/self.population_total for name,value in self.population.items()}

    def start(self, team, name):
        multiplier = math.exp(self.params.beta*self.z_previous[team]) if team in self.z_previous else self.params.rookie if team in self.rookies else 1.
        return self.fractions()[name]*multiplier

    def entry(self, team, event, name):
        block = self.blocks[name].get(event)
        if block is not None and team in block.index:
            i = block.index[team]
            return float(block.mean[i]),float(block.cov[i,i])
        previous = self.latest.get(team)
        if previous is None:
            return self.start(team,name),self.params.prior**2/len(self.names)
        old = self.blocks[name][previous]
        i = old.index[team]
        return float(old.mean[i]),float(old.cov[i,i])+self.params.event**2/len(self.names)

    def noise(self, mean):
        return self.params.noise**2/len(self.names)+self.params.noise_count*max(mean,0.)

    def side(self, event, teams, name):
        values = [self.entry(team,event,name) for team in teams]
        mean,var = sum(x[0] for x in values),sum(x[1] for x in values)
        block = self.blocks[name].get(event)
        if block is not None:
            ids = [block.index[t] for t in teams if t in block.index]
            var += sum(float(block.cov[i,j]) for i in ids for j in ids if i != j)
        return mean,var+self.noise(mean)

    def predict(self, card):
        # Upcoming forecasts cannot transfer teams into events or change prior fractions.
        sides = [[self.side(card.event_key,teams,name) for name in self.names] for teams in (card.red,card.blue)]
        u = self.unit.value()
        return Prediction(sum(x[0] for x in sides[0])*u,sum(x[0] for x in sides[1])*u,
                          sum(x[1] for x in sides[0])*u*u,sum(x[1] for x in sides[1])*u*u)

    @staticmethod
    def update(block, ids, y, noise):
        cov = block.cov[:block.n,:block.n]
        ph = cov[:,ids].sum(axis=1)
        s = float(ph[ids].sum())+noise
        nu = y-float(block.mean[ids].sum())
        gain = ph/s
        block.mean[:block.n] += gain*nu
        cov -= np.outer(gain,ph)

    def observe(self, result, projected):
        card = result.card
        u = self.unit.value()
        fractions = self.fractions()
        for name in self.names:
            if card.event_key not in self.blocks[name]:
                self.blocks[name][card.event_key] = _Block()
            block = self.blocks[name][card.event_key]
            # All components transfer before any latest-event pointer is advanced.
            for team in card.red+card.blue:
                if team not in block.index:
                    mean,var = self.entry(team,card.event_key,name)
                    block.add(team,mean,var)
            sides = []
            for color,teams in (("red",card.red),("blue",card.blue)):
                ids = [block.index[t] for t in teams]
                mean = float(block.mean[ids].sum())
                sides.append((ids,projected[color].get(name),self.noise(mean)))
            for ids,value,noise in sides:
                if value is None:
                    continue
                if name in self.robot:
                    # Three robot observations have the same initial noise budget as a sum.
                    for i,y in zip(ids,value):
                        r = self.params.noise**2/(3*len(self.names))+self.params.noise_count*max(float(block.mean[i]),0.)
                        self.update(block,[i],y/u,r)
                else:
                    self.update(block,ids,value/u,noise)
            for team in card.red+card.blue:
                block.cov[block.index[team],block.index[team]] += self.params.match**2/len(self.names)
        for team in card.red+card.blue:
            self.latest[team] = card.event_key
        for color,points in (("red",result.red_points),("blue",result.blue_points)):
            values = projected[color]
            if points is not None and all(values.get(name) is not None for name in self.names):
                for name in self.names:
                    value = values[name]
                    self.population[name] += (sum(value) if isinstance(value,list) else value)/3
                self.population_total += points/3
        self.unit.add(result.red_points)
        self.unit.add(result.blue_points)
        # Catch accidental loss of the partition, including signed residuals.
        if abs(sum(self.fractions().values())-1) > 1e-8 or abs(sum(fractions.values())-1) > 1e-8:
            raise ValueError("component prior fractions do not close")

    def team_points(self, team):
        event = self.latest[team]
        return sum(self.entry(team,event,name)[0] for name in self.names)*self.unit.value()

    def settings(self):
        return {"params":asdict(self.params),"variance_budget":"equal across components including residual",
                "prior_shares":"earlier complete observations, uniform before first observation",
                "unit":"shared total-score RunningUnit","cross_component_covariance":False}
