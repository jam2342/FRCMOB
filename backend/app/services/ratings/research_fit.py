# Fitting the scale-free team model's settings for the rating plan's phase 2: by the
# filter's own likelihood (optionally pulled toward earlier seasons, MAP), or by a
# coordinate search on Brier like the 2026 grid. Every fit names the week it stops at
# and sees only matches from earlier weeks; ScheduledModel then predicts each week with
# the newest fit made before it. Research only: nothing in production reads this.
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from statistics import median

import numpy as np

from app.services.ratings.replay import MatchCard, MatchResult, Prediction
from app.services.ratings.research_data import ResearchSeason, event_week
from app.services.ratings.research_eval import _bucket, _sigmoid, collect_rows, fit_scale
from app.services.ratings.research_filter import ScaleFreeFilter, ScaleFreeParams
from app.services.ratings.research_models import mean_share, rookies_of

BASE = ("prior", "match", "event", "noise", "beta", "rookie")
WIDTH_FLOOR = 0.3  # plan 3.6: five seasons can't pin a width down
BOUND = 4.0  # each setting within 4x of where the pull centres it
FREE_BOUNDS = (1e-3, 20.0)  # a setting with no centre


@dataclass
class SeasonInputs:
    research: ResearchSeason
    weeks: list[int]  # per result, in playing order
    z_previous: dict[str, float]
    rookies: set[str]
    previous_unit: float | None
    championship_week: int

    def model(self, params: ScaleFreeParams, *, name: str = "scale_free", record: bool = False) -> ScaleFreeFilter:
        return ScaleFreeFilter(params, z_previous=self.z_previous, rookies=self.rookies, previous_unit=self.previous_unit, name=name, record=record)


def scale_free_z(results: list[MatchResult], params: ScaleFreeParams = ScaleFreeParams(), min_matches: int = 8, clip: float = 2.5) -> dict[str, float]:
    # last_season_z with the scale-free filter: phase 1's version ran 2026-point settings
    # on every game, so its z for a low-scoring game came from badly scaled noise.
    model = ScaleFreeFilter(params)
    played: dict[str, int] = {}
    for result in results:
        model.observe(result)
        for team in result.card.red + result.card.blue:
            played[team] = played.get(team, 0) + 1
    floor = 1.0 / max(model.unit.value(), 1e-9)  # one point, as phase 1's floor
    points = {team: model.belief_units(team) for team in played}
    qualified = [p for team, p in points.items() if played[team] >= min_matches]
    typical = median(qualified) if qualified else 1.0
    return {team: max(-clip, min(clip, math.log(max(p, floor) / typical))) for team, p in points.items()}


def season_inputs(research: ResearchSeason, previous: ResearchSeason | None) -> SeasonInputs:
    weeks = [event_week(research, r.card.event_key) for r in research.season.results]
    championship = max((event_week(research, e) for e, info in research.events.items() if info.get("event_type") in (3, 4)), default=max(weeks) + 1)
    return SeasonInputs(
        research=research,
        weeks=weeks,
        z_previous=scale_free_z(previous.season.results) if previous else {},
        rookies=rookies_of(research.rookie_year, research.season.year),
        previous_unit=mean_share(previous.season.results) if previous else None,
        championship_week=championship,
    )


def fittable(inputs: SeasonInputs, names: tuple[str, ...]) -> tuple[str, ...]:
    # beta means nothing without last season's strengths (2022 has no cached 2021).
    return tuple(n for n in names if n != "beta" or inputs.z_previous)


def fit_weeks(inputs: SeasonInputs) -> tuple[int, ...]:
    # The plan's fits "at weeks 1, 2, 4, 8", in TBA's 0-based weeks: after FRC weeks 1, 2
    # and 4, and after everything before the Championship (FRC seasons end by week 8).
    return tuple(sorted({1, 2, 4, inputs.championship_week}))


# ── Objectives ────────────────────────────────────────────────────────────

def innovations(inputs: SeasonInputs, params: ScaleFreeParams, until_week: int | None) -> tuple[np.ndarray, np.ndarray]:
    # Prediction errors and variances (units) of every alliance total from weeks before
    # `until_week` (None = all), as the filter met them.
    model = inputs.model(params)
    nus: list[float] = []
    ss: list[float] = []
    for result, week in zip(inputs.research.season.results, inputs.weeks):
        if until_week is not None and week >= until_week:
            continue
        for nu, s in model.observe(result):
            nus.append(nu)
            ss.append(s)
    return np.array(nus), np.array(ss)


def loglik(nu: np.ndarray, s: np.ndarray, df: float | None = None) -> float:
    # Gaussian, or Student-t with the same variance (fits only: it lets outliers count less).
    if df is None:
        return float(-0.5 * np.sum(np.log(2 * math.pi * s) + nu * nu / s))
    scale2 = s * (df - 2.0) / df
    const = math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi)
    return float(np.sum(const - 0.5 * np.log(scale2) - (df + 1) / 2 * np.log1p(nu * nu / (df * scale2))))


def truncated(research: ResearchSeason, weeks: list[int], until_week: int | None) -> ResearchSeason:
    if until_week is None:
        return research
    results = [r for r, w in zip(research.season.results, weeks) if w < until_week]
    return replace(research, season=replace(research.season, results=results))


def insample_brier(rows) -> float:
    # Brier after the same per-bucket logistic the harness serves, fitted on these rows:
    # what the 2026 grid search optimised.
    if not rows:
        return 0.25
    xs = np.array([r.x for r in rows])
    ys = np.array([r.y for r in rows])
    buckets = np.array([_bucket(r.min_here) for r in rows])
    single = fit_scale(xs, ys)
    probs = np.zeros(len(rows))
    for b in ("0", "1-2", "3+"):
        mask = buckets == b
        if not mask.any():
            continue
        k = fit_scale(xs[mask], ys[mask]) if mask.sum() >= 100 else single
        probs[mask] = _sigmoid(k * xs[mask])
    return float(np.mean((probs - ys) ** 2))


# ── Search ────────────────────────────────────────────────────────────────

def nelder_mead(f, x0: np.ndarray, *, step: float = 0.3, max_evals: int = 300, xtol: float = 1e-2, ftol: float = 0.5) -> tuple[np.ndarray, float, int]:
    # Plain Nelder-Mead with dimension-adapted coefficients (Gao & Han 2012).
    d = len(x0)
    alpha, gamma, rho, sigma = 1.0, 1.0 + 2.0 / d, 0.75 - 1.0 / (2 * d), 1.0 - 1.0 / d
    pts = [np.array(x0, dtype=float)] + [np.array(x0, dtype=float) + step * np.eye(d)[i] for i in range(d)]
    vals = [f(p) for p in pts]
    evals = d + 1
    while evals < max_evals:
        order = np.argsort(vals)
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if vals[-1] - vals[0] < ftol and max(np.max(np.abs(p - pts[0])) for p in pts[1:]) < xtol:
            break
        centroid = np.mean(pts[:-1], axis=0)
        xr = centroid + alpha * (centroid - pts[-1])
        fr = f(xr)
        evals += 1
        if vals[0] <= fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
            continue
        if fr < vals[0]:
            xe = centroid + gamma * (xr - centroid)
            fe = f(xe)
            evals += 1
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
            continue
        if fr < vals[-1]:
            xc = centroid + rho * (xr - centroid)
        else:
            xc = centroid + rho * (pts[-1] - centroid)
        fc = f(xc)
        evals += 1
        if fc < min(fr, vals[-1]):
            pts[-1], vals[-1] = xc, fc
            continue
        for i in range(1, d + 1):
            pts[i] = pts[0] + sigma * (pts[i] - pts[0])
            vals[i] = f(pts[i])
            evals += 1
    best = int(np.argmin(vals))
    return pts[best], vals[best], evals


@dataclass(frozen=True)
class Pull:
    # MAP centre and width per setting, in logs (plan 3.6). A setting with no centre is
    # free inside FREE_BOUNDS.
    centre: dict[str, float]
    width: dict[str, float]

    @classmethod
    def from_seasons(cls, fits: list[ScaleFreeParams], names: tuple[str, ...]) -> Pull:
        centre, width = {}, {}
        for name in names:
            logs = [math.log(getattr(p, name)) for p in fits if getattr(p, name) > 0]
            if not logs:
                continue
            centre[name] = float(np.mean(logs))
            width[name] = max(float(np.std(logs, ddof=1)) if len(logs) > 1 else 0.0, WIDTH_FLOOR)
        return cls(centre, width)

    @classmethod
    def around(cls, params: ScaleFreeParams, names: tuple[str, ...]) -> Pull:
        return cls({n: math.log(getattr(params, n)) for n in names if getattr(params, n) > 0}, {n: WIDTH_FLOOR for n in names if getattr(params, n) > 0})

    def centred(self, params: ScaleFreeParams) -> ScaleFreeParams:
        return replace(params, **{n: math.exp(c) for n, c in self.centre.items()})

    def bounds(self, name: str) -> tuple[float, float]:
        if name in self.centre:
            return self.centre[name] - math.log(BOUND), self.centre[name] + math.log(BOUND)
        return math.log(FREE_BOUNDS[0]), math.log(FREE_BOUNDS[1])

    def penalty(self, name: str, x: float) -> float:
        if name not in self.centre:
            return 0.0
        return 0.5 * ((x - self.centre[name]) / self.width[name]) ** 2


def _search_space(start: ScaleFreeParams, names: tuple[str, ...], pull: Pull | None):
    x0 = np.array([math.log(max(getattr(start, n), 1e-3)) for n in names])
    bounds = [(pull or Pull({}, {})).bounds(n) for n in names]

    def to_params(x: np.ndarray) -> tuple[ScaleFreeParams, float]:
        clipped = [min(max(v, lo), hi) for v, (lo, hi) in zip(x, bounds)]
        outside = sum((v - c) ** 2 for v, c in zip(x, clipped))
        return replace(start, **{n: math.exp(v) for n, v in zip(names, clipped)}), outside

    return x0, bounds, to_params


def fit_likelihood(
    inputs: SeasonInputs,
    start: ScaleFreeParams,
    names: tuple[str, ...],
    until_week: int | None,
    *,
    pull: Pull | None = None,
    df: float | None = None,
    max_evals: int = 400,
) -> dict:
    # Maximum likelihood (pull=None) or MAP over weeks before `until_week`, in logs.
    names = fittable(inputs, names)
    x0, _bounds, to_params = _search_space(start, names, pull)
    x0 = np.array([min(max(v, lo), hi) for v, (lo, hi) in zip(x0, _bounds)])

    def objective(x):
        params, outside = to_params(x)
        nu, s = innovations(inputs, params, until_week)
        value = -loglik(nu, s, df) + 1e3 * outside
        if pull is not None:
            value += sum(pull.penalty(n, v) for n, v in zip(names, x))
        return value

    x, value, evals = nelder_mead(objective, x0, max_evals=max_evals)
    params, _ = to_params(x)
    nu, s = innovations(inputs, params, until_week)
    return {"params": params, "objective": value, "evals": evals, "n_obs": len(nu), "loglik_per_obs_units": loglik(nu, s) / max(len(nu), 1)}


def fit_brier(
    inputs: SeasonInputs,
    start: ScaleFreeParams,
    names: tuple[str, ...],
    until_week: int | None,
    *,
    pull: Pull | None = None,
    passes: tuple[tuple[float, ...], ...] = ((0.5, 0.7, 1.4, 2.0), (0.8, 0.9, 1.1, 1.25)),
) -> dict:
    # Coordinate search on in-sample Brier, one setting at a time (the 2026 grid's
    # method), inside the same bounds as the likelihood fit.
    names = fittable(inputs, names)
    research = truncated(inputs.research, inputs.weeks, until_week)
    _x0, bounds, _ = _search_space(start, names, pull)
    evals = 0

    def brier(params: ScaleFreeParams) -> float:
        nonlocal evals
        evals += 1
        return insample_brier(collect_rows(research, inputs.model(params)))

    best = start
    best_value = brier(best)
    for factors in passes:
        for name, (lo, hi) in zip(names, bounds):
            current = getattr(best, name)
            for factor in factors:
                value = current * factor
                if not (math.exp(lo) <= value <= math.exp(hi)):
                    continue
                candidate = replace(best, **{name: value})
                score = brier(candidate)
                if score < best_value - 1e-7:
                    best, best_value = candidate, score
    return {"params": best, "objective": best_value, "evals": evals}


# ── As-of schedules ──────────────────────────────────────────────────────

class ScheduledModel:
    # Predicts a match in week w with the settings fitted at the newest fit week <= w
    # (each fitted on earlier weeks only). Every filter sees every result in order, so
    # its beliefs are as-of too; switching settings re-estimates beliefs from the start
    # of the season with the new settings (plan decision 6).
    def __init__(self, inputs: SeasonInputs, plan: list[tuple[int, ScaleFreeParams]], *, name: str = "scheduled", record: bool = False):
        self.name = name
        self.research = inputs.research
        self.plan = sorted(plan, key=lambda item: item[0])
        self.filters = [inputs.model(p, record=record) for _w, p in self.plan]
        self.week_of: dict[str, int] = {}

    def _for(self, card: MatchCard) -> ScaleFreeFilter:
        week = self.week_of.get(card.event_key)
        if week is None:
            week = self.week_of[card.event_key] = event_week(self.research, card.event_key)
        chosen = self.filters[0]
        for (from_week, _p), model in zip(self.plan, self.filters):
            if week >= from_week:
                chosen = model
        return chosen

    def predict(self, card: MatchCard) -> Prediction:
        return self._for(card).predict(card)

    def observe(self, result: MatchResult) -> None:
        for model in self.filters:
            model.observe(result)

    def team_points(self, team_key: str, event_key: str) -> float:
        return self.filters[-1].team_points(team_key, event_key)

    def detail(self) -> dict[str, tuple]:
        out: dict[str, tuple] = {}
        for model in self.filters:
            out.update(model.detail)
        return out


def as_of_plan(
    inputs: SeasonInputs,
    bootstrap: ScaleFreeParams,
    names: tuple[str, ...],
    *,
    method: str = "map",
    pull: Pull | None = None,
    df: float | None = None,
) -> list[dict]:
    # Week 0 runs on the bootstrap; each fit week's settings come from weeks before it,
    # warm-started from the previous fit. method: "map", "mle" (no pull) or "brier".
    plan = [{"from_week": 0, "params": bootstrap, "fit": "bootstrap"}]
    current = bootstrap
    for week in fit_weeks(inputs):
        if not any(w < week for w in inputs.weeks):
            continue
        if method == "brier":
            out = fit_brier(inputs, current, names, week, pull=pull)
        else:
            out = fit_likelihood(inputs, current, names, week, pull=pull if method == "map" else None, df=df)
        current = out["params"]
        plan.append({"from_week": week, "params": current, "fit": method, "evals": out["evals"], "objective": out["objective"]})
    return plan


# ── E3 diagnosis: does match noise grow with the predicted level? ─────────

def side_records(rows, detail: dict[str, tuple]) -> dict[str, np.ndarray]:
    # Per alliance side with a result: predicted total x, belief variance b and noise r
    # (units), the actual total y (units), week and event type.
    xs, bs, rs, ys, weeks, types = [], [], [], [], [], []
    for row in rows:
        d = detail.get(row.match_key)
        if d is None:
            continue
        (rx, rb, rr), (bx, bb, br), u = d
        for (x, b, r), actual in (((rx, rb, rr), row.red_actual), ((bx, bb, br), row.blue_actual)):
            if actual is None:
                continue
            xs.append(x)
            bs.append(b)
            rs.append(r)
            ys.append(actual / u)
            weeks.append(row.week)
            types.append(row.event_type if row.event_type is not None else -1)
    return {k: np.array(v, dtype=float) for k, v in (("x", xs), ("b", bs), ("r", rs), ("y", ys), ("week", weeks), ("type", types))}


def _fit_noise_form(x: np.ndarray, b: np.ndarray, nu: np.ndarray, level: bool) -> tuple[np.ndarray, float]:
    # The noise of R = r0^2 + r1 x + r2^2 x^2 (level) or a constant, by likelihood with
    # the predictions held fixed.
    xp = np.maximum(x, 0.0)

    def noise(theta):
        return np.exp(theta[0]) + (np.exp(theta[1]) * xp + np.exp(theta[2]) * xp * xp if level else 0.0)

    def objective(theta):
        return -loglik(nu, b + noise(theta))

    start = np.log(np.array([max(float(np.mean(nu * nu - b)), 0.05), 1e-3, 1e-3]))
    if not level:
        theta, value, _ = nelder_mead(lambda t: objective(np.array([t[0], -30.0, -30.0])), start[:1], step=0.5, max_evals=200, ftol=1e-4, xtol=1e-4)
        return np.array([np.exp(theta[0]), 0.0, 0.0]), -value
    theta, value, _ = nelder_mead(objective, start, step=1.0, max_evals=1500, ftol=1e-4, xtol=1e-4)
    return np.exp(theta), -value


def level_diagnosis(rec: dict[str, np.ndarray], deciles: int = 10) -> dict:
    x, b, y = rec["x"], rec["b"], rec["y"]
    nu = y - x
    n = len(x)
    if n < 200:
        return {"n": n}
    edges = np.quantile(x, np.linspace(0, 1, deciles + 1))
    which = np.clip(np.searchsorted(edges, x, side="right") - 1, 0, deciles - 1)
    table = []
    for d in range(deciles):
        m = which == d
        if not m.any():
            continue
        var = float(nu[m].var())
        table.append({
            "decile": d + 1,
            "n": int(m.sum()),
            "x": round(float(x[m].mean()), 3),
            "bias_pct": round(100 * float(nu[m].mean() / x[m].mean()), 2),
            "err_var": round(var, 4),
            "belief_var": round(float(b[m].mean()), 4),
            "implied_noise": round(var - float(b[m].mean()), 4),
            "model_noise": round(float(rec["r"][m].mean()), 4),
        })
    const, ll_const = _fit_noise_form(x, b, nu, level=False)
    form, ll_level = _fit_noise_form(x, b, nu, level=True)
    lo, hi = float(np.quantile(x, 0.05)), float(np.quantile(x, 0.95))

    def r_at(v, f):
        return float(f[0] + f[1] * v + f[2] * v * v)

    return {
        "n": n,
        "by_decile": table,
        "implied_noise_ratio_top_vs_bottom": round(table[-1]["implied_noise"] / table[0]["implied_noise"], 2) if table[0]["implied_noise"] > 0 else None,
        "constant_noise": round(float(const[0]), 4),
        "level_form": {"r0_sq": round(float(form[0]), 4), "r1": round(float(form[1]), 4), "r2_sq": round(float(form[2]), 4)},
        "level_noise_ratio_p95_vs_p5": round(r_at(hi, form) / r_at(lo, form), 2),
        "loglik_gain_per_obs": round((ll_level - ll_const) / n, 4),
    }
