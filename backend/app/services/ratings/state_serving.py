# Serving engine for a season rated by the frozen state recipe (total score, Tier A) instead of a
# hand-entered SEASON_BASELINES row. Off unless RATING_STATE_SERVING_ENABLED; seasons with a
# baseline row always keep the hand-tuned team model.
from __future__ import annotations

from dataclasses import dataclass, field
import time

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


def build(db, season, row, *, now=None):
    started = time.perf_counter()
    params, fitted, bootstrap = resolve(db, row)
    inputs, _, _ = inputs_for(db, int(season), int(now if now is not None else time.time()), bootstrap.params["context"])
    adapter = ServingAdapter(inputs.model(params), get_spec(int(season)))
    run = StateSeasonRun(int(season), adapter, row.id, fitted, setup=("state", row.id))
    results = inputs.research.season.results
    first = min((r.card.event_start for r in results), default=0)
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
