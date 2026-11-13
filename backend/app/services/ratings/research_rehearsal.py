# Offline candidates and immutable settings; never imported by the serving pipeline.
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from app.services.ratings.research_win import (
    MIN_FIT, SCORE_TERM_SD, WinRecipe, _ridge, _score_sd, all_sides,
    score_design, side_arrays, weekly,
)

CHAMPION = WinRecipe(base="logistic", correction="logistic", past=True)
CHALLENGERS = {
    "stacking": WinRecipe(base="logistic", correction="logistic", past=True, stack=("epa", "elo")),
    "network": WinRecipe(base="logistic", correction="network", past=True),
    "late_term": WinRecipe(base="logistic", correction="logistic", past=True, late=True),
}
PROTOCOL = {
    "version": 2, "development": [2023, 2024, 2025, 2026], "clean_test": [2027],
    "champion": asdict(CHAMPION), "challengers": {k: asdict(v) for k, v in CHALLENGERS.items()},
    "score_damping_candidate": "half phase3 correction when someone plays their first qualification match here",
    "score_candidate": "separate composition coefficients for first-match context; prior SD 0.15",
    "late_term": "z * max(season_frac - 0.35, 0)",
    "add_bar": 0.0015, "max_season_regression": 0.0005,
    "network_noninferiority": 0.0, "first_match_bias_limit_pct": 3.0,
    "serving_scores": "uncalibrated", "promotion": "blocked until clean 2027 and all rehearsal gates pass",
}


def first_match_design(sides):
    x = score_design(sides, True)
    # Shared make-up terms were correcting early alliances using later-match behavior.
    first = sides["ctx"] == 0
    terms = x[:, -3:].copy()
    x[first, -3:] = 0.0
    return np.column_stack([x, terms * first[:, None]])


def first_match_scores(t, centre=None):
    sd = np.r_[_score_sd(True), np.full(3, SCORE_TERM_SD)]
    centre = np.zeros(len(sd)) if centre is None else centre
    sides = side_arrays(t)
    red, blue = t["rm"].copy(), t["bm"].copy()
    fits = []
    for week, start, idx in weekly(t):
        mask = t["time"][sides["row"]] < start
        train = {k: v[mask] for k, v in sides.items()}
        coef = centre.copy()
        if mask.sum() >= 2 * MIN_FIT:
            residual = train["y"] - train["x"]
            coef = _ridge(first_match_design(train), residual, centre, sd, max(float(np.var(residual)), 1e-9))
        rows = all_sides(t, idx)
        out = rows["u"] * (rows["x"] + first_match_design(rows) @ coef)
        red[idx], blue[idx] = out[:len(idx)], out[len(idx):]
        fits.append({"week": week, "before_time": start, "coef": coef.tolist(), "fitted_on_sides": int(mask.sum())})
    residual = sides["y"] - sides["x"]
    final = _ridge(first_match_design(sides), residual, centre, sd, max(float(np.var(residual)), 1e-9))
    return red, blue, fits, final


def write_settings(root: Path, payload: dict) -> Path:
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    path = root / (hashlib.sha256(encoded).hexdigest() + ".json")
    root.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(encoded)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise ValueError("settings content does not match its version")
    return path


def noninferiority(comparison, margin=0.0005):
    if comparison.get("n", 0) == 0:
        return "inconclusive"
    if comparison["hi90"] <= margin:
        return "passed"
    return "failed" if comparison["lo90"] > margin else "inconclusive"


def load_settings(path: Path) -> dict:
    encoded = path.read_bytes()
    if path.stem != hashlib.sha256(encoded).hexdigest():
        raise ValueError("settings version does not match content")
    payload = json.loads(encoded)
    if payload["schema"] != 1 or not payload["win_uses_uncalibrated_scores"]:
        raise ValueError("unsupported settings recipe")
    return payload


def apply_settings(t, settings):
    from app.services.ratings.research_win import (
        ScoreCalibration, base_logit, context, correction_features, sigmoid,
    )

    rows = all_sides(t, np.arange(len(t["y"])))
    cal = ScoreCalibration(np.array(settings["score_coef"]), settings["score_terms"])
    score = rows["u"] * cal.apply(rows)
    recipe = WinRecipe(**settings["recipe"])
    offset = np.array(settings["temperatures"])[context(t)] * base_logit(t, recipe.base)
    if settings["weights"] is not None:
        offset += correction_features(t, recipe.stack, late=recipe.late) @ np.array(settings["weights"])
    return score[:len(t["y"])], score[len(t["y"]):], sigmoid(offset)
