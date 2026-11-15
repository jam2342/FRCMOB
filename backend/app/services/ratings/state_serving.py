# Serving engine for a season rated by the frozen state recipe (total score, Tier A) instead of a
# hand-entered SEASON_BASELINES row. Off unless RATING_STATE_SERVING_ENABLED; seasons with a
# baseline row always keep the hand-tuned team model.
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import time

import numpy as np

from app.core.config import settings
from app.db import models
from app.services.ratings import calibrations as layers
from app.services.ratings.component_spec import get_spec
from app.services.ratings.constants import SEASON_BASELINES
from app.services.ratings.state_bootstrap import BOOTSTRAP_RECIPE, validate_bootstrap, validate_params
from app.services.ratings.state_fitting import feature
from app.services.ratings.state_inputs import inputs_for
from app.services.ratings.total_adapter import TotalStateAdapter

# The bootstrap and the weekly versions of the recipe frozen for 2027 (PR #161).
SERVED_RECIPES = (BOOTSTRAP_RECIPE, "state-map-gradual-early-fast-v1")
# Earlier seasons are history: they keep whatever they had and never become state-rated.
FIRST_STATE_SEASON = 2027
# 0-100 anchors before a season has a population: the state model's end-of-season shape,
# averaged over 2023-2026 (median robot 0.81-1.07 x the unit, 99th percentile 2.4-6.1 x the
# median). The measured population takes over with weight n / (n + 300) in team-events.
ANCHOR_DEFAULT = {"median_over_unit": .981, "p99_over_median": 3.561}
ANCHOR_POPULATION = 300
# The same population the weekly job's anchors use (weekly_rehearsal.rating_impact): teams with
# 8+ matches this season, at their latest event.
ANCHOR_MIN_MATCHES = 8


def declared(season) -> bool:
    # A season the state recipe may rate: switch on, 2027 or later, no hand-entered baseline,
    # and a Tier A spec.
    if not settings.rating_state_serving_enabled or season is None or int(season) < FIRST_STATE_SEASON or int(season) in SEASON_BASELINES:
        return False
    try:
        return get_spec(int(season)).tier == "A"
    except ValueError:
        return False


def active_version(db, season):
    if not declared(season):
        return None
    bundle = db.get(models.RatingActiveBundle, int(season))
    row = db.get(models.RatingCalibration, bundle.calibration_id) if bundle and bundle.calibration_id else None
    if row is None or row.season != int(season) or row.recipe not in SERVED_RECIPES or row.status == "rejected":
        return None
    return row


def resolve(db, row):
    # (state settings, v2 layers, bootstrap row) for a bootstrap or a weekly version.
    bootstrap = row if row.recipe == BOOTSTRAP_RECIPE else db.get(models.RatingCalibration, row.params.get("bootstrap_id"))
    if bootstrap is None or bootstrap.recipe != BOOTSTRAP_RECIPE or bootstrap.season != row.season or bootstrap.status == "rejected":
        raise ValueError("served state version has no valid bootstrap")
    validate_bootstrap(bootstrap.params["artifact"], row.season)
    params = bootstrap.params["artifact"]["params"] if row.recipe == BOOTSTRAP_RECIPE else row.params["state"]
    fitted = bootstrap.params["artifact"]["layers"] if row.recipe == BOOTSTRAP_RECIPE else row.params["layers"]
    layers.validate_params(fitted)
    if not layers.is_v2(fitted):
        raise ValueError("served state versions need v2 score layers")
    return validate_params(params), fitted, bootstrap


class ServingAdapter(TotalStateAdapter):
    # What the season's consumers read beyond the total-state adapter. Unknowable values
    # (a dud rate from alliance totals) are None, never invented.
    def __init__(self, model, spec):
        super().__init__(model, spec)
        self.rookies = set(model.rookies)

    def dud_rate(self, team):
        return None

    def start_basis(self, team):
        team = self._team(team)
        return "last_season" if team in self.inner.z_previous else "rookie" if team in self.rookies else "season_typical"


@dataclass
class StateSeasonRun:
    season: int
    model: ServingAdapter
    version_id: int
    layers: dict
    # match_key -> (prediction, fewest earlier matches here, state feature row): the row is
    # what the v2 layers read, as the rehearsal scored it.
    before_match: dict = field(default_factory=dict)
    played_total: dict = field(default_factory=dict)
    played_here: dict = field(default_factory=dict)
    robots_here: dict = field(default_factory=dict)  # per-robot points need a component parser (Tier B/C)
    changes: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    seconds: float = 0.0
    setup: tuple = ()
    state_results_sha256: str | None = None
    engine: str = "state"
    # Weekly-damped anchors the Monday job stored on the version, if any (relative 0-100).
    stored_anchors: tuple | None = None
    first: int = 0  # the season's first event start, for season_frac
    built_at: int = 0
    _comparator: object = field(default=None, repr=False, compare=False)


STATE_FEATURE_SCHEMA = "state-recipe-v1"


@dataclass
class StateFeatures:
    # A match's state feature row with the version's v2 layers: the recipe's win chance and
    # its calibrated, positive alliance points (before fouls), as the rehearsal scored them.
    row: dict
    layers: dict
    version_id: int
    source_label: str = "state_recipe_v1"
    _out: tuple | None = field(default=None, repr=False, compare=False)

    def _apply(self):
        if self._out is None:
            t = {k: np.array([float(v)]) for k, v in self.row.items()}
            t["y"] = np.array([.5])
            red, blue, p = layers.apply(t, self.layers)
            self._out = (float(red[0]), float(blue[0]), float(p[0]))
        return self._out

    @property
    def red_points(self):
        return self._apply()[0]

    @property
    def blue_points(self):
        return self._apply()[1]

    @property
    def win_prob(self):
        return self._apply()[2]


def forecast_row(run, card):
    # An upcoming match on a shadow of the model: predict() only enters teams into the target
    # event's block and moves latest-event pointers, so only those are copied (as rehearsed).
    inner = run.model.inner
    shadow = copy.copy(inner)
    shadow.blocks = inner.blocks.copy()
    if card.event_key in inner.blocks:
        shadow.blocks[card.event_key] = copy.deepcopy(inner.blocks[card.event_key])
    shadow.latest = inner.latest.copy()
    shadow.record = False
    prediction = shadow.predict(card)
    row = feature(shadow, card, prediction, run.played_here, run.played_total, run.first)
    return prediction, StateFeatures(row, run.layers, run.version_id)


def anchors(run):
    # (median, p99) robot points for the 0-100 curve: the stored weekly anchors, else the
    # default shape blended toward this season's measured population.
    if run.stored_anchors is not None:
        return tuple(float(v) for v in run.stored_anchors)
    unit = run.model.inner.unit.value()
    median = ANCHOR_DEFAULT["median_over_unit"]*unit
    default = (median, median*ANCHOR_DEFAULT["p99_over_median"])
    inner = run.model.inner
    points = sorted(inner.team_points(t, inner.latest[t]) for t, n in run.played_total.items() if n >= ANCHOR_MIN_MATCHES)
    if not points:
        return default
    import numpy as np
    weight = len(points)/(len(points)+ANCHOR_POPULATION)
    measured = (float(np.median(points)), float(np.quantile(points, .99)))
    blended = tuple(weight*m+(1-weight)*d for m, d in zip(measured, default))
    # The curve needs p99 above the median; a degenerate early population keeps the default shape.
    return blended if blended[0] > 0 and blended[1] > blended[0] else default


def comparator(db, run):
    # The frozen bootstrap's run over the same results: the safety check's comparator, logged
    # beside each served forecast. Built once per served run.
    if getattr(run, "_comparator", None) is None:
        row = db.get(models.RatingCalibration, run.version_id)
        _params, _layers, bootstrap = resolve(db, row)
        run._comparator = run if bootstrap.id == row.id else build(db, run.season, bootstrap, now=run.built_at)
    return run._comparator


def comparator_forecast(boot, card):
    played = boot.before_match.get(card.match_key)
    features = StateFeatures(played[2], boot.layers, boot.version_id) if played else forecast_row(boot, card)[1]
    return {"p": features.win_prob, "red_points": features.red_points, "blue_points": features.blue_points, "version": boot.version_id}


def build(db, season, row, *, now=None):
    started = time.perf_counter()
    params, fitted, bootstrap = resolve(db, row)
    built_at = int(now if now is not None else time.time())
    inputs, _, _ = inputs_for(db, int(season), built_at, bootstrap.params["context"])
    adapter = ServingAdapter(inputs.model(params), get_spec(int(season)))
    stored = row.params.get("anchors") if row.recipe != BOOTSTRAP_RECIPE else None
    run = StateSeasonRun(int(season), adapter, row.id, fitted, setup=("state", row.id), stored_anchors=tuple(stored) if stored else None)
    results = inputs.research.season.results
    first = min((r.card.event_start for r in results), default=0)
    run.first = first
    run.built_at = built_at
    for result in results:
        card = result.card
        teams = card.red+card.blue
        prediction = adapter.predict(card)
        seen = min(run.played_here.get((t, card.event_key), 0) for t in teams)
        run.before_match[card.match_key] = (prediction, seen, feature(adapter.inner, card, prediction, run.played_here, run.played_total, first))
        before = {t: adapter.team_points(t, card.event_key) for t in teams}
        adapter.observe(result)
        for team in teams:
            run.changes.setdefault((team, card.event_key), []).append((card.match_key, before[team], adapter.team_points(team, card.event_key)))
            run.played_here[(team, card.event_key)] = run.played_here.get((team, card.event_key), 0)+1
            run.played_total[team] = run.played_total.get(team, 0)+1
    run.results = results
    run.seconds = round(time.perf_counter()-started, 2)
    return run
