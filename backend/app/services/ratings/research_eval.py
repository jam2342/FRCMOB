# Walk-forward evaluation for the rating research harness (docs/RATING_ROADMAP.md method,
# several seasons at once). A model predicts every match before it sees the result; the
# win-probability scale for week w is fitted only on matches from earlier weeks, so every
# number here is a forecast. Pure numpy/python: seasons come from research_data.py.
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np

from app.services.ratings.replay import Prediction, TeamModel, calibration_table, outcome_scores
from app.services.ratings.research_data import ResearchSeason, event_week

Z80 = 1.2816
NORMAL_LOGISTIC_SCALE = 1.702  # a logistic with this scale is the normal CDF
MIN_FIT_MATCHES = 150  # fewer earlier matches than this: no fit, use the default scale
MIN_BUCKET_MATCHES = 100


@dataclass
class Row:
    match_key: str
    event_key: str
    week: int
    comp_level: str
    event_type: int | None
    min_here: int  # fewest earlier matches at this event among the six teams
    min_before: int  # fewest matches at earlier events among the six teams
    y: float  # 1 = red won
    prediction: Prediction
    red_actual: float | None
    blue_actual: float | None
    holdout: bool
    # forecast() path (production serves it for upcoming matches), when the model has one.
    alt: Prediction | None = None
    time: int = 0  # when it was played: what "earlier" means for every fit

    @property
    def margin(self) -> float:
        return _margin(self.prediction)

    @property
    def x(self) -> float:
        return _x(self.prediction)

    @property
    def alt_x(self) -> float | None:
        return _x(self.alt) if self.alt is not None else None

    @property
    def knows_spread(self) -> bool:
        p = self.prediction
        return p.red_var is not None and p.blue_var is not None and p.red_var + p.blue_var > 0


def _margin(p: Prediction) -> float:
    return (p.red_mean + p.red_fouls) - (p.blue_mean + p.blue_fouls)


def _x(p: Prediction) -> float:
    # The logistic's input: the margin in standard deviations when the model knows its
    # spread, else the plain margin. A model that tracks the covariance between the two
    # alliances' beliefs gives the margin's own variance (margin_var).
    margin_var = getattr(p, "margin_var", None)
    if margin_var is not None and margin_var > 0:
        return _margin(p) / math.sqrt(margin_var + p.red_fouls_var + p.blue_fouls_var)
    if p.red_var is not None and p.blue_var is not None and p.red_var + p.blue_var > 0:
        return _margin(p) / math.sqrt(p.red_var + p.blue_var + p.red_fouls_var + p.blue_fouls_var)
    return _margin(p)


def collect_rows(research: ResearchSeason, model: TeamModel, *, alt: Callable[[object], Prediction | None] | None = None, keep_ties: bool = False) -> list[Row]:
    # One as-of pass. `alt(card)` may return a second prediction for the same match, made
    # just before predict(), from the model's forecast() path. keep_ties: a tie stays as
    # y = 0.5 (score metrics use it; win fits and scores skip it).
    played_here: dict[tuple[str, str], int] = defaultdict(int)
    played_total: dict[str, int] = defaultdict(int)
    before_event: dict[tuple[str, str], int] = {}
    rows: list[Row] = []
    season = research.season
    for result in season.results:
        card = result.card
        teams = card.red + card.blue
        for team in teams:
            before_event.setdefault((team, card.event_key), played_total[team] - played_here[(team, card.event_key)])
        alternative = alt(card) if alt else None
        prediction = model.predict(card)
        if keep_ties or result.red_score != result.blue_score:
            rows.append(Row(
                match_key=card.match_key,
                event_key=card.event_key,
                week=event_week(research, card.event_key),
                comp_level=card.comp_level,
                event_type=research.events.get(card.event_key, {}).get("event_type"),
                min_here=min(played_here[(team, card.event_key)] for team in teams),
                min_before=min(before_event[(team, card.event_key)] for team in teams),
                y=1.0 if result.red_score > result.blue_score else 0.0 if result.red_score < result.blue_score else 0.5,
                prediction=prediction,
                red_actual=result.red_points,
                blue_actual=result.blue_points,
                holdout=card.event_key in season.holdout_events,
                alt=alternative,
                time=card.time,
            ))
        model.observe(result)
        for team in teams:
            played_here[(team, card.event_key)] += 1
            played_total[team] += 1
    return rows


# ── Probabilities, fitted only on earlier weeks ──────────────────────────

def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def fit_scale(xs: np.ndarray, ys: np.ndarray) -> float:
    # One-parameter logistic, no intercept (red and blue stay symmetric), by Newton.
    k = 0.0
    for _ in range(60):
        p = _sigmoid(k * xs)
        grad = float(np.sum((p - ys) * xs))
        hess = float(np.sum(p * (1 - p) * xs * xs))
        if hess <= 1e-12:
            break
        step = grad / hess
        k -= step
        if abs(step) < 1e-10:
            break
    return k


def _bucket(min_here: int) -> str:
    return "0" if min_here <= 0 else "1-2" if min_here < 3 else "3+"


def row_default_scale(row: Row) -> float:
    # With no earlier matches to fit on: the normal CDF for a model that knows its spread;
    # for a margin model, 1.702 over a typical margin SD (about 0.42 of an alliance's
    # mean), from this match's own pre-match prediction, so it's as-of and in this
    # season's points.
    if row.knows_spread:
        return NORMAL_LOGISTIC_SCALE
    means = [m for m in (row.prediction.red_mean, row.prediction.blue_mean) if m > 0]
    return NORMAL_LOGISTIC_SCALE / (0.42 * (sum(means) / len(means))) if means else 0.03


def walk_forward_probs(rows: list[Row], *, default_scale: float | None = None) -> tuple[list[float], list[float | None], dict]:
    # P(red wins) per row, and for the forecast() path where there is one. The scale
    # (per matches-seen-here bucket) for week w comes only from matches played before
    # week w's first match: a week label isn't playing order (late events, and 2026's
    # Championship sits after its June "week 16-18" events). Ties never enter a fit.
    # Where nothing can be fitted yet, each match uses default_scale or its own default.
    by_week: dict[int, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        by_week[row.week].append(i)
    xs = np.array([r.x for r in rows])
    ys = np.array([r.y for r in rows])
    buckets = np.array([_bucket(r.min_here) for r in rows])
    weeks = np.array([r.week for r in rows])
    times = np.array([r.time for r in rows])
    decided = ys != 0.5
    probs = [0.5] * len(rows)
    alt_probs: list[float | None] = [None] * len(rows)
    scales: dict[int, dict] = {}
    for week in sorted(by_week):
        start = int(times[weeks == week].min())
        earlier = (times < start) & decided
        n_earlier = int(earlier.sum())
        single: float | None = default_scale
        if n_earlier >= MIN_FIT_MATCHES:
            fitted = fit_scale(xs[earlier], ys[earlier])
            if fitted > 0:
                single = fitted
        per_bucket: dict[str, float | None] = {}
        for b in ("0", "1-2", "3+"):
            mask = earlier & (buckets == b)
            k = fit_scale(xs[mask], ys[mask]) if int(mask.sum()) >= MIN_BUCKET_MATCHES and n_earlier >= MIN_FIT_MATCHES else None
            per_bucket[b] = k if k is not None and k > 0 else single
        scales[week] = {"fitted_on": n_earlier, **{b: (round(k, 5) if k is not None else "per match") for b, k in per_bucket.items()}}
        for i in by_week[week]:
            k = per_bucket[_bucket(rows[i].min_here)]
            if k is None:
                k = row_default_scale(rows[i])
            probs[i] = float(_sigmoid(k * rows[i].x))
            if rows[i].alt is not None:
                alt_probs[i] = float(_sigmoid(k * rows[i].alt_x))
    return probs, alt_probs, scales


# ── Scores ───────────────────────────────────────────────────────────────

def calibration_slope(probs: list[float], ys: list[float]) -> float | None:
    # Slope of the outcome on the forecast's logit: 1 is calibrated, under 1 overconfident.
    if len(probs) < 30:
        return None
    p = np.clip(np.array(probs), 1e-6, 1 - 1e-6)
    return round(fit_scale(np.log(p / (1 - p)), np.array(ys)), 3)


def alliance_sides(rows: Iterable[Row]):
    # (predicted mean, predicted variance, actual) for every alliance side with all three.
    for r in rows:
        p = r.prediction
        for mean, var, actual in ((p.red_mean, p.red_var, r.red_actual), (p.blue_mean, p.blue_var, r.blue_actual)):
            if var is not None and var > 0 and actual is not None:
                yield mean, var, actual


def alliance_scores(rows: list[Row], deciles: int = 10) -> dict:
    sides = list(alliance_sides(rows))
    if not sides:
        return {"n": 0}
    mean = np.array([s[0] for s in sides])
    var = np.array([s[1] for s in sides])
    actual = np.array([s[2] for s in sides])
    nu = actual - mean
    loglik = -0.5 * (np.log(2 * math.pi * var) + nu * nu / var)
    z = nu / np.sqrt(var)
    edges = np.quantile(mean, np.linspace(0, 1, deciles + 1))
    which = np.clip(np.searchsorted(edges, mean, side="right") - 1, 0, deciles - 1)
    table = []
    for d in range(deciles):
        m = which == d
        if m.any():
            table.append({
                "decile": d + 1, "n": int(m.sum()), "mean_predicted": round(float(mean[m].mean()), 1),
                "mean_error": round(float(nu[m].mean()), 2), "z_variance": round(float(z[m].var()), 3),
                "coverage_80": round(float((np.abs(z[m]) <= Z80).mean()), 3),
            })
    return {
        "n": len(sides),
        "mean_abs_error": round(float(np.abs(nu).mean()), 2),
        "loglik_per_obs": round(float(loglik.mean()), 4),
        "coverage_80": round(float((np.abs(z) <= Z80).mean()), 4),
        "z_variance": round(float(z.var()), 3),
        "by_decile": table,
    }


def point_error(rows: list[Row]) -> dict:
    # Alliance-points error for models with or without a spread.
    errors = [abs(mean - actual) for r in rows for mean, actual in ((r.prediction.red_mean, r.red_actual), (r.prediction.blue_mean, r.blue_actual)) if actual is not None]
    return {"n": len(errors), "mean_abs": round(sum(errors) / len(errors), 2) if errors else None}


SLICES: dict[str, Callable[[Row], bool]] = {
    "all": lambda r: True,
    "holdout (newest 20% of events)": lambda r: r.holdout,
    "weeks 1-2": lambda r: r.week <= 1,
    "weeks 3-5": lambda r: 2 <= r.week <= 4,
    "district championships": lambda r: r.event_type in (2, 5),
    "Championship": lambda r: r.event_type in (3, 4),
    "quals": lambda r: r.comp_level == "qm",
    "playoffs": lambda r: r.comp_level != "qm",
    "nobody has played here yet": lambda r: r.min_here == 0,
    "everyone has 3+ matches here": lambda r: r.min_here >= 3,
    "a team at its first event": lambda r: r.min_before == 0,
}


def evaluate(rows: list[Row], probs: list[float], alt_probs: list[float | None] | None = None) -> dict:
    out = {"slices": {}}
    for name, keep in SLICES.items():
        idx = [i for i, r in enumerate(rows) if keep(r)]
        scores = outcome_scores([probs[i] for i in idx], [rows[i].y for i in idx])
        if scores.get("n"):
            chosen = [rows[i] for i in idx]
            scores["calibration_slope"] = calibration_slope([probs[i] for i in idx], [r.y for r in chosen])
            scores["points_mae"] = point_error(chosen)["mean_abs"]
            scores["coverage_80"] = alliance_scores(chosen).get("coverage_80")
        out["slices"][name] = scores
    out["alliance_points_error"] = point_error(rows)
    out["alliance_scores"] = alliance_scores(rows)
    out["calibration_all"] = calibration_table(probs, [r.y for r in rows])
    if alt_probs and any(p is not None for p in alt_probs):
        both = [i for i, p in enumerate(alt_probs) if p is not None]
        out["forecast_vs_predict"] = {
            "n": len(both),
            "predict": outcome_scores([probs[i] for i in both], [rows[i].y for i in both]),
            "forecast": outcome_scores([alt_probs[i] for i in both], [rows[i].y for i in both]),
            "mean_abs_prob_gap": round(float(np.mean([abs(probs[i] - alt_probs[i]) for i in both])), 5),
        }
    return out


def per_match_brier(rows: list[Row], probs: list[float]) -> dict[str, tuple[str, float]]:
    return {r.match_key: (r.event_key, (p - r.y) ** 2) for r, p in zip(rows, probs)}


# ── Paired bootstrap over whole events ───────────────────────────────────

def paired_cluster_bootstrap(a: dict[str, tuple[str, float]], b: dict[str, tuple[str, float]], *, n: int = 2000, seed: int = 0) -> dict:
    # Mean of (a - b) over the matches both scored, with a 90% interval from resampling
    # whole events (matches at an event share teams, so they aren't independent). A
    # negative difference means a is better when the numbers are losses.
    shared = sorted(set(a) & set(b))
    if not shared:
        return {"n": 0}
    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for key in shared:
        event = a[key][0]
        sums[event] += a[key][1] - b[key][1]
        counts[event] += 1
    events = sorted(sums)
    s = np.array([sums[e] for e in events])
    c = np.array([counts[e] for e in events], dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(events), size=(n, len(events)))
    means = s[draws].sum(axis=1) / c[draws].sum(axis=1)
    return {
        "n": len(shared),
        "events": len(events),
        "mean_diff": float(s.sum() / c.sum()),
        "lo90": float(np.quantile(means, 0.05)),
        "hi90": float(np.quantile(means, 0.95)),
    }
