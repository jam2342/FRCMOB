# The layers on top of the state model, for the rating plan's phase 3: a calibration of
# predicted alliance scores (actual on predicted per context, pulled to the identity), the
# win layer (Phi(z), a temperature per context pulled toward 1, then a scale-free
# logistic correction or a small network) and stacked side models. Every fit used for a
# week sees only matches played before that week's first match (a week label isn't
# playing order). Research only: nothing in production reads this.
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from app.services.ratings.replay import MatchCard, MatchResult, Prediction
from app.services.ratings.research_eval import MIN_BUCKET_MATCHES, MIN_FIT_MATCHES, NORMAL_LOGISTIC_SCALE, collect_rows, fit_scale, walk_forward_probs
from app.services.ratings.research_fit import ScheduledModel, SeasonInputs

MIN_FIT = 150  # fewer earlier decided matches than this: keep the centre
X0 = 3.0  # an average alliance, in units of the mean share
# By the fewest earlier matches here among the six (so context 0 = someone's first match).
CONTEXTS = ("someone's first match here", "1-2 here", "3+ here", "playoffs")
# Prior SDs. Score calibration: each context's intercept (units) and slope change around
# the centre, composition terms around it too. Temperatures: pooled, then each context
# around it. Correction weights: one SD for every weight (features are O(1) by design).
SCORE_SD = (0.3, 0.1)
SCORE_TERM_SD = 0.15
TEMP_POOLED_SD = 0.25
TEMP_CONTEXT_SD = 0.15
WEIGHT_SD = 0.25
Z_CLIP = 8.0


# ── The per-match table ──────────────────────────────────────────────────

class _Recording:
    # Wraps a ScheduledModel: after each prediction, notes what the filter in use knew
    # about the six teams (units of the as-of mean share), before the result is seen.
    def __init__(self, inner: ScheduledModel):
        self.inner = inner
        self.name = inner.name
        self.extra: dict[str, tuple] = {}
        self.played_here: dict[tuple[str, str], int] = defaultdict(int)

    def predict(self, card: MatchCard) -> Prediction:
        p = self.inner.predict(card)
        f = self.inner.current
        block = f.blocks[card.event_key]

        def side(teams):
            idx = [block.index[t] for t in teams]
            means = [float(block.mean[i]) for i in idx]
            sds = [math.sqrt(max(float(block.cov[i, i]), 0.0)) for i in idx]
            return max(means), min(means), max(sds)

        most_here = max(self.played_here[(t, card.event_key)] for t in card.red + card.blue)
        self.extra[card.match_key] = (*side(card.red), *side(card.blue), f.unit.value(), most_here)
        return p

    def observe(self, result: MatchResult) -> None:
        self.inner.observe(result)
        for team in result.card.red + result.card.blue:
            self.played_here[(team, result.card.event_key)] += 1


def _thirds(rows) -> list[int]:
    # 0/1/2: which third of the event's quals a match is in, by playing order; 3: playoffs.
    # Uses the event's final quals count, so it's an evaluation slice, never an input.
    total = Counter(r.event_key for r in rows if r.comp_level == "qm")
    seen: dict[str, int] = defaultdict(int)
    out = []
    for r in rows:
        if r.comp_level != "qm":
            out.append(3)
            continue
        out.append(min(2, 3 * seen[r.event_key] // max(total[r.event_key], 1)))
        seen[r.event_key] += 1
    return out


def side_probs(inputs: SeasonInputs, model) -> dict[str, float]:
    # A side model's own as-of win probabilities (its walk-forward logistic), by match.
    rows = collect_rows(inputs.research, model, keep_ties=True)
    probs, _, _ = walk_forward_probs(rows)
    return {r.match_key: p for r, p in zip(rows, probs)}


def bundle_table(inputs: SeasonInputs, plan: list[tuple[int, object]], side: dict[str, dict[str, float]] | None = None) -> dict[str, np.ndarray]:
    # One row per match (ties kept: y = 0.5). Inputs are known before the match; "third"
    # and "holdout" are labels for slicing results and never reach a fit.
    rec = _Recording(ScheduledModel(inputs, plan, name="bundle"))
    rows = collect_rows(inputs.research, rec, keep_ties=True)
    p_bucket, _, _ = walk_forward_probs(rows)
    event_start = {r.card.event_key: r.card.event_start for r in inputs.research.season.results}
    season_start: int | None = None
    t: dict[str, list] = defaultdict(list)
    for r, pb, third in zip(rows, p_bucket, _thirds(rows)):
        card_start = event_start[r.event_key]
        season_start = card_start if season_start is None else min(season_start, card_start)
        p = r.prediction
        best_r, worst_r, sd_r, best_b, worst_b, sd_b, u, most_here = rec.extra[r.match_key]
        t["key"].append(r.match_key)
        t["event"].append(r.event_key)
        t["week"].append(r.week)
        t["time"].append(r.time)
        t["event_type"].append(-1 if r.event_type is None else r.event_type)
        t["playoff"].append(float(r.comp_level != "qm"))
        t["min_here"].append(r.min_here)
        t["max_here"].append(most_here)
        t["min_before"].append(r.min_before)
        t["y"].append(r.y)
        t["holdout"].append(r.holdout)
        t["third"].append(third)
        t["rm"].append(p.red_mean)
        t["bm"].append(p.blue_mean)
        t["rv"].append(p.red_var)
        t["bv"].append(p.blue_var)
        t["mv"].append(getattr(p, "margin_var", None) or (p.red_var + p.blue_var))
        t["u"].append(u)
        t["ra"].append(np.nan if r.red_actual is None else r.red_actual)
        t["ba"].append(np.nan if r.blue_actual is None else r.blue_actual)
        t["best_r"].append(best_r)
        t["best_b"].append(best_b)
        t["worst_r"].append(worst_r)
        t["worst_b"].append(worst_b)
        t["sd_r"].append(sd_r)
        t["sd_b"].append(sd_b)
        t["season_frac"].append((card_start - season_start) / (86400 * 120))
        t["p_bucket"].append(pb)
        for name, probs in (side or {}).items():
            t[f"p_{name}"].append(probs[r.match_key])
    return {k: np.array(v) for k, v in t.items()}


def context(t: dict[str, np.ndarray]) -> np.ndarray:
    return np.where(t["playoff"] > 0, 3, np.where(t["min_here"] <= 0, 0, np.where(t["min_here"] < 3, 1, 2)))


def zscore(t: dict[str, np.ndarray]) -> np.ndarray:
    return (t["rm"] - t["bm"]) / np.sqrt(t["mv"])


def weekly(t: dict[str, np.ndarray]) -> list[tuple[int, int, np.ndarray]]:
    # (week label, its first match's time, its rows): the fit for a week uses rows played
    # before that time, whatever their label.
    out = []
    for w in sorted(set(t["week"].tolist())):
        idx = np.flatnonzero(t["week"] == w)
        out.append((w, int(t["time"][idx].min()), idx))
    return out


def bucket_probs(t: dict[str, np.ndarray], x: np.ndarray) -> np.ndarray:
    # The harness's walk-forward logistic (research_eval.walk_forward_probs) on any x per
    # row, for a model that knows its spread: a scale per matches-seen bucket, fitted on
    # decided matches played before the week began.
    y = t["y"]
    decided = y != 0.5
    buckets = np.where(t["min_here"] <= 0, 0, np.where(t["min_here"] < 3, 1, 2))
    probs = np.full(len(y), 0.5)
    for _week, start, idx in weekly(t):
        earlier = (t["time"] < start) & decided
        n = int(earlier.sum())
        single = None
        if n >= MIN_FIT_MATCHES:
            k = fit_scale(x[earlier], y[earlier])
            single = k if k > 0 else None
        for b in range(3):
            mask = earlier & (buckets == b)
            k = fit_scale(x[mask], y[mask]) if int(mask.sum()) >= MIN_BUCKET_MATCHES and n >= MIN_FIT_MATCHES else None
            k = k if k is not None and k > 0 else single
            rows = idx[buckets[idx] == b]
            probs[rows] = sigmoid((k if k is not None else NORMAL_LOGISTIC_SCALE) * x[rows])
    return probs


# ── Score calibration ────────────────────────────────────────────────────

SCORE_TERMS = ("weakest robot vs alliance average", "best robot vs alliance average", "opponent level")


def _ridge(x: np.ndarray, r: np.ndarray, centre: np.ndarray, sd: np.ndarray, s2: float) -> np.ndarray:
    # Posterior mean of r = x @ b + noise(s2) under the prior N(centre, diag(sd^2)).
    precision = np.diag(1.0 / sd**2)
    if len(r) == 0:
        return centre.copy()
    return np.linalg.solve(x.T @ x / s2 + precision, x.T @ r / s2 + precision @ centre)


@dataclass
class ScoreCalibration:
    # Predicted x (units) -> x + a_c + c_c (x - X0) [+ composition terms]: an intercept
    # and slope per context, and (when used) shared coefficients on the alliance's make-up.
    coef: np.ndarray
    terms: bool = False

    @classmethod
    def identity(cls, terms: bool = False) -> ScoreCalibration:
        return cls(np.zeros(2 * len(CONTEXTS) + (len(SCORE_TERMS) if terms else 0)), terms)

    @property
    def by_context(self) -> np.ndarray:
        k = len(CONTEXTS)
        return np.column_stack([self.coef[:k], self.coef[k: 2 * k]])

    def apply(self, sides: dict[str, np.ndarray]) -> np.ndarray:
        return sides["x"] + score_design(sides, self.terms) @ self.coef


def score_design(sides: dict[str, np.ndarray], terms: bool) -> np.ndarray:
    k = len(CONTEXTS)
    onehot = np.eye(k)[sides["ctx"]]
    cols = [onehot, onehot * (sides["x"] - X0)[:, None]]
    if terms:
        avg = sides["x"] / 3.0
        cols.append(np.column_stack([sides["worst"] - avg, sides["best"] - avg, sides["opp"] - X0]))
    return np.hstack(cols)


def _score_sd(terms: bool) -> np.ndarray:
    k = len(CONTEXTS)
    sd = [SCORE_SD[0]] * k + [SCORE_SD[1]] * k
    return np.array(sd + ([SCORE_TERM_SD] * len(SCORE_TERMS) if terms else []))


def fit_score_calibration(sides: dict[str, np.ndarray], centre: ScoreCalibration | None = None, *, terms: bool = False) -> ScoreCalibration:
    # sides: side_arrays() rows (units). centre: earlier seasons' fit, else the identity.
    centre = centre or ScoreCalibration.identity(terms)
    r = sides["y"] - sides["x"]
    s2 = float(np.var(r)) if len(r) >= 30 else 1.0
    return ScoreCalibration(_ridge(score_design(sides, terms), r, centre.coef, _score_sd(terms), s2), terms)


def side_arrays(t: dict[str, np.ndarray], rows: np.ndarray | None = None) -> dict[str, np.ndarray]:
    # Both alliance sides of the chosen rows that have a result: predicted and actual in
    # units (and points), the side's best and weakest robot, the opponent's prediction.
    rows = np.arange(len(t["y"])) if rows is None else rows
    u = np.concatenate([t["u"][rows], t["u"][rows]])
    x = np.concatenate([t["rm"][rows], t["bm"][rows]])
    v = np.concatenate([t["rv"][rows], t["bv"][rows]])
    a = np.concatenate([t["ra"][rows], t["ba"][rows]])
    opp = np.concatenate([t["bm"][rows], t["rm"][rows]])
    best = np.concatenate([t["best_r"][rows], t["best_b"][rows]])
    worst = np.concatenate([t["worst_r"][rows], t["worst_b"][rows]])
    ctx = np.concatenate([context(t)[rows], context(t)[rows]])
    row = np.concatenate([rows, rows])
    red = np.concatenate([np.ones(len(rows), bool), np.zeros(len(rows), bool)])
    ok = np.isfinite(a) & (v > 0)
    return {
        "x": x[ok] / u[ok], "y": a[ok] / u[ok], "opp": opp[ok] / u[ok], "best": best[ok], "worst": worst[ok], "u": u[ok],
        "var": v[ok], "actual": a[ok], "ctx": ctx[ok], "row": row[ok], "red": red[ok], "pred": x[ok],
    }


def all_sides(t: dict[str, np.ndarray], rows: np.ndarray) -> dict[str, np.ndarray]:
    # side_arrays() for prediction: every side of the rows, played or not.
    u = np.concatenate([t["u"][rows], t["u"][rows]])
    return {
        "x": np.concatenate([t["rm"][rows], t["bm"][rows]]) / u,
        "opp": np.concatenate([t["bm"][rows], t["rm"][rows]]) / u,
        "best": np.concatenate([t["best_r"][rows], t["best_b"][rows]]),
        "worst": np.concatenate([t["worst_r"][rows], t["worst_b"][rows]]),
        "ctx": np.concatenate([context(t)[rows], context(t)[rows]]),
        "u": u,
    }


def walk_forward_scores(t: dict[str, np.ndarray], centre: ScoreCalibration | None = None, *, terms: bool = False) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    # Calibrated red and blue predicted scores (points) per row, each from the fit made
    # before its week began.
    sides = side_arrays(t)
    red, blue = t["rm"].copy(), t["bm"].copy()
    fits = []
    for week, start, idx in weekly(t):
        earlier = t["time"][sides["row"]] < start
        if int(earlier.sum()) >= 2 * MIN_FIT:
            cal = fit_score_calibration({k: v[earlier] for k, v in sides.items()}, centre, terms=terms)
        else:
            cal = centre or ScoreCalibration.identity(terms)
        rows = all_sides(t, idx)
        out = rows["u"] * cal.apply(rows)
        red[idx], blue[idx] = out[: len(idx)], out[len(idx):]
        fits.append({"week": week, "before_time": start, "fitted_on_sides": int(earlier.sum()), "coef": cal.coef.tolist()})
    return red, blue, fits


# ── Win layer ────────────────────────────────────────────────────────────

_ERFC = np.frompyfunc(math.erfc, 1, 1)


def logit_phi(z: np.ndarray) -> np.ndarray:
    # logit(Phi(z)) without cancellation in the tails.
    z = np.clip(np.asarray(z, dtype=float), -Z_CLIP, Z_CLIP)
    upper = _ERFC(-z / math.sqrt(2)).astype(float) / 2.0  # Phi(z)
    lower = _ERFC(z / math.sqrt(2)).astype(float) / 2.0  # Phi(-z)
    return np.log(upper) - np.log(lower)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def _newton_logistic(f: np.ndarray, y: np.ndarray, offset: np.ndarray, centre: np.ndarray, precision: np.ndarray, iters: int = 50) -> np.ndarray:
    # Logistic regression with an offset and a Gaussian prior N(centre, precision^-1).
    w = centre.copy()
    for _ in range(iters):
        p = sigmoid(offset + f @ w)
        grad = f.T @ (p - y) + precision @ (w - centre)
        hess = (f * (p * (1 - p))[:, None]).T @ f + precision
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.max(np.abs(step)) < 1e-9:
            break
    return w


@dataclass
class Temperatures:
    pooled: float
    by_context: np.ndarray  # shape (4,)

    @classmethod
    def ones(cls) -> Temperatures:
        return cls(1.0, np.ones(len(CONTEXTS)))

    def apply(self, base_logit: np.ndarray, ctx: np.ndarray) -> np.ndarray:
        return self.by_context[ctx] * base_logit


def fit_temperatures(base_logit: np.ndarray, y: np.ndarray, ctx: np.ndarray, centre: Temperatures | None = None) -> Temperatures:
    # One multiplier on the base logit per context: pooled first (pulled to the centre,
    # 1 by default), then each context pulled to the pooled one plus the centre's offset.
    centre = centre or Temperatures.ones()
    zero = np.zeros(len(y))
    pooled = float(_newton_logistic(base_logit[:, None], y, zero, np.array([centre.pooled]), np.array([[1.0 / TEMP_POOLED_SD**2]]))[0])
    by_context = np.zeros(len(CONTEXTS))
    for c in range(len(CONTEXTS)):
        m = ctx == c
        mean = pooled + centre.by_context[c] - centre.pooled
        by_context[c] = _newton_logistic(base_logit[m][:, None], y[m], zero[m], np.array([mean]), np.array([[1.0 / TEMP_CONTEXT_SD**2]]))[0] if m.any() else mean
    return Temperatures(pooled, by_context)


FEATURES = (
    "red intercept", "playoff red intercept", "best robot gap", "worst robot gap", "belief SD gap", "margin in shares",
    "z x seen here", "z x seen before", "z x season time", "z x level", "z x |z|", "z x playoff",
)


def correction_features(t: dict[str, np.ndarray], stack: tuple[str, ...] = (), *, late: bool = False) -> np.ndarray:
    # Scale-free: gaps in units of the as-of mean share, z = margin / its own SD, counts
    # and season time as fractions. Swapping red and blue flips every column but the two
    # intercepts. stack: side models whose logits join as inputs.
    z = np.clip(zscore(t), -Z_CLIP, Z_CLIP)
    u = t["u"]
    level = (t["rm"] + t["bm"]) / (6.0 * u)
    cols = [
        np.ones_like(z),
        t["playoff"],
        t["best_r"] - t["best_b"],
        t["worst_r"] - t["worst_b"],
        t["sd_r"] - t["sd_b"],
        (t["rm"] - t["bm"]) / (3.0 * u),
        z * np.minimum(t["min_here"], 10) / 10.0,
        z * np.minimum(t["min_before"], 40) / 40.0,
        z * t["season_frac"],
        z * (level - 1.0),
        z * np.abs(z) / 4.0,
        z * t["playoff"],
    ]
    if late:
        cols.append(z * np.maximum(t["season_frac"] - 0.35, 0.0))
    for name in stack:
        p = np.clip(t[f"p_{name}"], 1e-4, 1 - 1e-4)
        cols.append(np.log(p / (1 - p)))
    return np.column_stack(cols)


def fit_correction(f: np.ndarray, y: np.ndarray, offset: np.ndarray, centre: np.ndarray | None = None) -> np.ndarray:
    centre = np.zeros(f.shape[1]) if centre is None else centre
    return _newton_logistic(f, y, offset, centre, np.eye(f.shape[1]) / WEIGHT_SD**2)


class Network:
    # The production correction's shape (16 tanh units, Adam lr 0.01, weight decay 1e-3,
    # 300 full-batch epochs, torch's default initialisation) in numpy.
    def __init__(self, hidden: int = 16, epochs: int = 300, lr: float = 0.01, weight_decay: float = 1e-3, seed: int = 0):
        self.hidden, self.epochs, self.lr, self.wd, self.seed = hidden, epochs, lr, weight_decay, seed
        self.params: list[np.ndarray] | None = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> Network:
        rng = np.random.default_rng(self.seed)
        n_in = x.shape[1]
        b1, b2 = 1 / math.sqrt(n_in), 1 / math.sqrt(self.hidden)
        params = [rng.uniform(-b1, b1, (self.hidden, n_in)), rng.uniform(-b1, b1, self.hidden), rng.uniform(-b2, b2, (1, self.hidden)), rng.uniform(-b2, b2, 1)]
        m = [np.zeros_like(p) for p in params]
        v = [np.zeros_like(p) for p in params]
        beta1, beta2, eps = 0.9, 0.999, 1e-8
        n = len(y)
        for step in range(1, self.epochs + 1):
            w1, c1, w2, c2 = params
            h = np.tanh(x @ w1.T + c1)
            out = (h @ w2.T)[:, 0] + c2[0]
            d_out = (sigmoid(out) - y) / n
            g_w2 = d_out[None, :] @ h
            g_c2 = np.array([d_out.sum()])
            d_h = (d_out[:, None] * w2) * (1 - h * h)
            grads = [d_h.T @ x, d_h.sum(axis=0), g_w2, g_c2]
            for i, (p, g) in enumerate(zip(params, grads)):
                g = g + self.wd * p  # torch's Adam weight_decay: L2 in the gradient
                m[i] = beta1 * m[i] + (1 - beta1) * g
                v[i] = beta2 * v[i] + (1 - beta2) * g * g
                p -= self.lr * (m[i] / (1 - beta1**step)) / (np.sqrt(v[i] / (1 - beta2**step)) + eps)
        self.params = params
        return self

    def logit(self, x: np.ndarray) -> np.ndarray:
        w1, c1, w2, c2 = self.params
        return (np.tanh(x @ w1.T + c1) @ w2.T)[:, 0] + c2[0]


@dataclass
class WinRecipe:
    base: str = "phi"  # "phi": logit Phi(z); "logistic": 1.702 z
    temperature: bool = True
    correction: str = "none"  # "none", "logistic", "network"
    past: bool = False  # start temperatures and correction from earlier seasons
    stack: tuple[str, ...] = ()
    late: bool = False


def base_logit(t: dict[str, np.ndarray], base: str) -> np.ndarray:
    z = zscore(t)
    return logit_phi(z) if base == "phi" else 1.702 * np.clip(z, -Z_CLIP, Z_CLIP)


@dataclass
class SeasonFit:
    # A finished season's end-of-season fits and as-of inputs, for the next seasons' start.
    temperatures: Temperatures
    weights: np.ndarray | None
    rows: dict[str, np.ndarray]  # "f", "offset", "y", "net_x" over its decided matches


def walk_forward_win(t: dict[str, np.ndarray], recipe: WinRecipe, past: list[SeasonFit] | None = None, *, seed: int = 0) -> tuple[np.ndarray, list[dict], SeasonFit]:
    # P(red wins) per row from the fits made before each row's week began, plus the
    # season's end-of-season fit for later seasons.
    past = past if recipe.past else []
    ctx = context(t)
    base = base_logit(t, recipe.base)
    y = t["y"]
    decided = y != 0.5
    f = correction_features(t, recipe.stack, late=recipe.late) if recipe.correction != "none" else None
    t_centre = _pooled_temperatures(past) if past else None
    w_centre = _pooled_weights(past, recipe) if past and recipe.correction == "logistic" else None
    probs = np.full(len(y), 0.5)
    fits = []

    def fit_on(mask):
        temps = fit_temperatures(base[mask], y[mask], ctx[mask], t_centre) if recipe.temperature and int(mask.sum()) >= MIN_FIT else (t_centre or Temperatures.ones())
        weights, net = None, None
        if recipe.correction == "logistic":
            if int(mask.sum()) >= MIN_FIT:
                weights = fit_correction(f[mask], y[mask], temps.apply(base[mask], ctx[mask]), w_centre)
            else:
                weights = w_centre
        if recipe.correction == "network":
            xs, ys = [], []
            for s in past:
                xs.append(s.rows["net_x"])
                ys.append(s.rows["y"])
            if int(mask.sum()) >= MIN_FIT:
                xs.append(_net_inputs(temps.apply(base[mask], ctx[mask]), f[mask]))
                ys.append(y[mask])
            if xs:
                net = Network(seed=seed).fit(np.vstack(xs), np.concatenate(ys))
        return temps, weights, net

    for week, start, idx in weekly(t):
        mask = (t["time"] < start) & decided
        temps, weights, net = fit_on(mask)
        logit = temps.apply(base[idx], ctx[idx]) if recipe.temperature else base[idx]
        if weights is not None:
            logit = logit + f[idx] @ weights
        if net is not None:
            logit = net.logit(_net_inputs(logit, f[idx]))
        probs[idx] = sigmoid(logit)
        fits.append({"week": week, "before_time": start, "pooled_temperature": temps.pooled, "fitted_on": int(mask.sum()), "temperatures": temps.by_context.tolist(), "weights": None if weights is None else weights.tolist(), "network": net is not None})
    # End of season (every decided match), for the seasons after this one.
    temps, weights, _net = fit_on(decided)
    offset = temps.apply(base[decided], ctx[decided])
    rows = {"y": y[decided], "offset": offset}
    if f is not None:
        rows["f"] = f[decided]
        rows["net_x"] = _net_inputs(offset, f[decided])
    return probs, fits, SeasonFit(temps, weights, rows)


def _net_inputs(offset: np.ndarray, f: np.ndarray) -> np.ndarray:
    # The base logit (as production feeds it) and the features without the constant.
    return np.column_stack([offset, f[:, 1:]])


def _pooled_temperatures(past: list[SeasonFit]) -> Temperatures:
    return Temperatures(float(np.mean([s.temperatures.pooled for s in past])), np.mean([s.temperatures.by_context for s in past], axis=0))


def _pooled_weights(past: list[SeasonFit], recipe: WinRecipe) -> np.ndarray | None:
    # One fit on every earlier season's as-of rows (each with its own season's offsets).
    usable = [s for s in past if "f" in s.rows]
    if not usable:
        return None
    f = np.vstack([s.rows["f"] for s in usable])
    return fit_correction(f, np.concatenate([s.rows["y"] for s in usable]), np.concatenate([s.rows["offset"] for s in usable]))
