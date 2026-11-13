from __future__ import annotations

from dataclasses import asdict, replace
import math

import numpy as np

from app.services.ratings.research_filter import ScaleFreeParams
from app.services.ratings.research_fit import BASE, Pull, WIDTH_FLOOR
from app.services.ratings.state_inputs import digest
from app.services.ratings import calibrations as layers

RECIPE = "state-map-total-v1"
BOOTSTRAP_RECIPE = "state-map-total-bootstrap-v1"
NAMES = (*BASE, "noise_count")


def validate_params(value):
    if set(value) != set(asdict(ScaleFreeParams())):
        raise ValueError("incomplete state parameters")
    params = ScaleFreeParams(**value)
    for name in NAMES:
        number = getattr(params, name)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if params.noise_prop != 0 or params.prior_level != 0 or params.drift_level is not False or params.margin_cov is not False:
        raise ValueError("state recipe permits counting noise only, without level drift/prior or margin covariance")
    return params


def build_bootstrap(season, hindsight, runs, hashes, *, layer_params=None, layer_years=()):
    years = sorted(int(year) for year in hindsight if int(year) < season)
    if not years or season-1 not in years:
        raise ValueError("need earlier-season fits including the immediately preceding season")
    states = [validate_params({**hindsight[str(y)]["mle"], "noise_count": 0.2, "margin_cov": False}) for y in years]
    pull = Pull.from_seasons(states, BASE)
    count = [r["plan"][-1]["params"]["noise_count"] for r in runs if int(r["season"]) < season]
    if not count or any(v <= 0 or not math.isfinite(v) for v in count):
        raise ValueError("need positive counting-noise fits from earlier seasons")
    logs = np.log(count)
    pull.centre["noise_count"] = float(logs.mean())
    pull.width["noise_count"] = max(float(logs.std(ddof=1)) if len(logs)>1 else 0.0, WIDTH_FLOOR)
    params = pull.centred(replace(ScaleFreeParams(), margin_cov=False))
    prior = replace(ScaleFreeParams(**hindsight[str(season-1)]["mle"]), match=1e-6, event=1e-6)
    return {"schema": RECIPE, "season": season, "params": asdict(params),
            "pull": {"centre": pull.centre, "width": pull.width}, "prior_params": asdict(prior),
            "source_seasons": years, "source_sha256": hashes,
            "layers": layer_params or layers.identity(), "layer_source_seasons": list(layer_years), "provisional_layers": not bool(layer_years)}


def validate_bootstrap(value, season):
    if value["schema"] != RECIPE or value["season"] != season:
        raise ValueError("bootstrap belongs to another recipe or season")
    years = value["source_seasons"]
    if not years or any(type(year) is not int or year >= season for year in years) or season-1 not in years:
        raise ValueError("bootstrap must use earlier seasons only")
    validate_params(value["params"])
    layers.validate_params(value["layers"])
    layer_years = value["layer_source_seasons"]
    if any(type(y) is not int or y < 2023 or y >= season for y in layer_years) or value["provisional_layers"] != (not bool(layer_years)):
        raise ValueError("invalid earlier-season layer provenance")
    for kind in ("centre", "width"):
        if set(value["pull"][kind]) != set(NAMES):
            raise ValueError("bootstrap pull must cover every fitted parameter")
        if any(not math.isfinite(v) or (kind == "width" and v < WIDTH_FLOOR) for v in value["pull"][kind].values()):
            raise ValueError("invalid MAP pull")
    if any(abs(math.log(value["params"][name])-value["pull"]["centre"][name]) > 1e-9 for name in NAMES):
        raise ValueError("bootstrap must be centred on its MAP pull")
    prior = ScaleFreeParams(**value["prior_params"])
    if prior.match != 1e-6 or prior.event != 1e-6:
        raise ValueError("previous-season strengths must use the no-drift average")
    if not value["source_sha256"]:
        raise ValueError("bootstrap is missing source hashes")
    digest(value)
