#!/usr/bin/env python3
# Export a private bootstrap from saved earlier-season research fits, without refitting.
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ratings.research_rehearsal import write_settings  # noqa: E402
import numpy as np  # noqa: E402
from app.services.ratings import calibrations as layers  # noqa: E402
from app.services.ratings.research_win import (WinRecipe, fit_score_calibration, side_arrays, walk_forward_win, _pooled_temperatures, _pooled_weights)  # noqa: E402
from app.services.ratings.state_bootstrap import build_bootstrap, validate_bootstrap  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--reports", required=True, type=Path)
    parser.add_argument("--win-tables", required=True, type=Path, help="Earlier-season phase 3 feature tables")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    path = args.reports / "hindsight.json"
    all_fits = json.loads(path.read_text())
    fits = {year: data for year,data in all_fits.items() if int(year) < args.season}
    paths = sorted((args.reports / "runs").glob("e3_noise_count_zstatic-*.json"))
    runs, hashes = [], {f"hindsight-before-{args.season}": hashlib.sha256(json.dumps(fits,sort_keys=True,allow_nan=False).encode()).hexdigest()}
    for path in paths:
        source_year = int(path.stem.rsplit("-",1)[1])
        if source_year >= args.season:
            continue
        run = json.loads(path.read_text())
        if run["season"] != source_year:
            raise SystemExit("State report filename and season differ")
        if run["season"] < args.season:
            runs.append(run)
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    scores, wins, layer_years = [], [], []
    recipe = WinRecipe(base="logistic", correction="logistic", past=True)
    for year in sorted(int(y) for y in fits if 2023 <= int(y) < args.season):
        path = args.win_tables / f"{year}.npz"
        if not path.exists():
            raise SystemExit(f"Missing earlier-season win table: {year}")
        with np.load(path, allow_pickle=False) as raw:
            table = {k:raw[k] for k in raw.files}
        scores.append(fit_score_calibration(side_arrays(table), terms=True).coef)
        _, _, fit = walk_forward_win(table, recipe, wins)
        wins.append(fit)
        layer_years.append(year)
        hashes[f"table-{year}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not wins:
        raise SystemExit("At least one earlier-season win table from 2023 onward is required")
    temperatures = _pooled_temperatures(wins)
    prior_layers = {"schema":layers.SCHEMA,"score_coef":np.mean(scores,axis=0).tolist(),
                    "temperatures":temperatures.by_context.tolist(),"pooled_temperature":temperatures.pooled,
                    "weights":_pooled_weights(wins,recipe).tolist(),"blend":1.0}
    layers.validate_params(prior_layers)
    artifact = build_bootstrap(args.season, fits, runs, hashes, layer_params=prior_layers, layer_years=layer_years)
    validate_bootstrap(artifact, args.season)
    result = write_settings(args.out, artifact)
    assert json.loads(result.read_text()) == artifact
    print(result)


if __name__ == "__main__":
    main()
