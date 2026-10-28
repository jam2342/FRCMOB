# Replay a season match by match and score any team model on what happened next.
#
# A model sees each match twice: predict() gets the match card (who plays, when, where;
# no scores), then observe() gets the result. It can't see a result before predicting,
# so every number this produces is a forecast. Held out: the newest 20% of events. Pure Python on purpose: the loader is the only part that
# touches the database, and tests build seasons by hand.
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable, Iterable, Protocol

from app.services.ratings.official_points import OFFICIAL_SOURCES, match_points
from app.services.ratings.strength import MatchPoints, StrengthParams, estimate_strength

COMPONENTS = ("teleop", "auto", "climb")
HOLDOUT_EVENT_FRACTION = 0.2


@dataclass(frozen=True)
class MatchCard:
    match_key: str
    event_key: str
    time: int
    comp_level: str
    event_start: int
    red: tuple[str, ...]
    blue: tuple[str, ...]


@dataclass(frozen=True)
class MatchResult:
    card: MatchCard
    red_score: int
    blue_score: int
    # Alliance points a team model can explain: the score without foul points received.
    red_points: float | None
    blue_points: float | None
    # Each robot's official points (findings); a robot can be missing.
    robots: dict[str, MatchPoints] = field(default_factory=dict)
    # Season components per alliance (match_alliance_results.components).
    red_components: dict = field(default_factory=dict)
    blue_components: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Prediction:
    red_mean: float
    blue_mean: float
    # Variance of each alliance's points; None for a model that doesn't know its spread
    # (the harness then fits a logistic on the plain margin).
    red_var: float | None = None
    blue_var: float | None = None
    # Foul points each alliance is expected to *receive* (from the other side's fouls);
    # they count toward winning but not toward a team's own points.
    red_fouls: float = 0.0
    blue_fouls: float = 0.0
    red_fouls_var: float = 0.0
    blue_fouls_var: float = 0.0
    # Parts behind the bonus RPs (Phase 5): hub fuel and tower points, mean and variance.
    red_hub: tuple[float, float] | None = None
    blue_hub: tuple[float, float] | None = None
    red_tower: tuple[float, float] | None = None
    blue_tower: tuple[float, float] | None = None


class TeamModel(Protocol):
    name: str

    def predict(self, card: MatchCard) -> Prediction: ...

    def observe(self, result: MatchResult) -> None: ...

    def team_points(self, team_key: str, event_key: str) -> float: ...


@dataclass
class Season:
    year: int
    results: list[MatchResult]  # in playing order
    holdout_events: set[str]
    # The season's typical robot per component, from training events only.
    typical: dict[str, float]


def _total(mp: MatchPoints) -> float:
    return sum(getattr(mp, name) or 0.0 for name in COMPONENTS)


def typical_robot(results: Iterable[MatchResult], events: set[str] | None = None, min_matches: int = 5) -> dict[str, float]:
    # Median over team-events with enough matches, per component and in total.
    by_team_event: dict[tuple[str, str], list[MatchPoints]] = defaultdict(list)
    for result in results:
        if events is not None and result.card.event_key not in events:
            continue
        for team, mp in result.robots.items():
            by_team_event[(team, result.card.event_key)].append(mp)
    rows = [mps for mps in by_team_event.values() if len(mps) >= min_matches]
    out: dict[str, float] = {}
    for name in COMPONENTS:
        means = [sum(vals) / len(vals) for mps in rows if (vals := [getattr(mp, name) for mp in mps if getattr(mp, name) is not None])]
        out[name] = round(median(means), 2) if means else 0.0
    out["total"] = round(median(sum(_total(mp) for mp in mps) / len(mps) for mps in rows), 2) if rows else 0.0
    out["team_events"] = len(rows)
    return out


def split_holdout(results: list[MatchResult], fraction: float = HOLDOUT_EVENT_FRACTION) -> set[str]:
    last: dict[str, int] = {}
    for result in results:
        last[result.card.event_key] = max(last.get(result.card.event_key, 0), result.card.time)
    ordered = sorted(last, key=lambda key: (last[key], key))
    return set(ordered[-max(1, round(len(ordered) * fraction)):]) if ordered else set()


# ── Scoring helpers ──────────────────────────────────────────────────────

def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def fit_logistic_scale(xs: list[float], ys: list[float]) -> float:
    # One parameter, no intercept (red and blue stay symmetric), by Newton.
    k = 0.0
    for _ in range(60):
        grad = hess = 0.0
        for x, y in zip(xs, ys):
            p = _sigmoid(k * x)
            grad += (p - y) * x
            hess += p * (1 - p) * x * x
        if hess <= 1e-12:
            break
        step = grad / hess
        k -= step
        if abs(step) < 1e-10:
            break
    return k


def floored(z: float, floor: float) -> float:
    # A logistic that never quite reaches 0 or 1: in FRC a robot can always die, so even
    # a lopsided match keeps `floor` chance of an upset.
    return floor + (1.0 - 2.0 * floor) * _sigmoid(z)


def _log_loss(probs: Iterable[float], ys: Iterable[float]) -> float:
    total = 0.0
    for p, y in zip(probs, ys):
        p = min(1 - 1e-9, max(1e-9, p))
        total -= y * math.log(p) + (1 - y) * math.log(1 - p)
    return total


def _golden(f: Callable[[float], float], lo: float, hi: float, iterations: int = 40) -> float:
    ratio = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(iterations):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - ratio * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + ratio * (b - a)
            fd = f(d)
    return (a + b) / 2


def fit_upset_floor(groups: dict[str, tuple[list[float], list[float]]], floors: Iterable[float] = tuple(i / 100 for i in range(0, 11))) -> tuple[float, dict[str, float]]:
    # One shared floor, a scale per group, by log loss.
    best: tuple[float, float, dict[str, float]] | None = None
    for floor in floors:
        scales, loss = {}, 0.0
        for name, (xs, ys) in groups.items():
            k0 = max(1e-6, abs(fit_logistic_scale(xs, ys)))
            k = _golden(lambda k: _log_loss((floored(k * x, floor) for x in xs), ys), 0.2 * k0, 4.0 * k0)
            scales[name] = k
            loss += _log_loss((floored(k * x, floor) for x in xs), ys)
        if best is None or loss < best[0]:
            best = (loss, floor, scales)
    assert best is not None
    return best[1], best[2]


def outcome_scores(probs: list[float], ys: list[float]) -> dict:
    if not probs:
        return {"n": 0}
    probs = [min(1 - 1e-6, max(1e-6, p)) for p in probs]
    right = sum(1.0 if (p > 0.5) == (y > 0.5) else 0.0 for p, y in zip(probs, ys) if p != 0.5) + 0.5 * sum(1 for p in probs if p == 0.5)
    return {
        "n": len(probs),
        "accuracy": round(right / len(probs), 4),
        "brier": round(sum((p - y) ** 2 for p, y in zip(probs, ys)) / len(probs), 4),
        "log_loss": round(-sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(probs, ys)) / len(probs), 4),
    }


def calibration_table(probs: list[float], ys: list[float], bins: int = 10) -> list[dict]:
    # Predicted red-win chance by tenths against how often red actually won.
    rows = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [j for j, p in enumerate(probs) if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if idx:
            rows.append({
                "predicted": f"{lo:.1f}-{hi:.1f}",
                "n": len(idx),
                "mean_predicted": round(sum(probs[j] for j in idx) / len(idx), 3),
                "red_won": round(sum(ys[j] for j in idx) / len(idx), 3),
            })
    return rows


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def spearman(xs: list[float], ys: list[float]) -> float | None:
    return pearson(_ranks(xs), _ranks(ys))


# ── The replay ───────────────────────────────────────────────────────────

def _bucket(n: int, comp_level: str = "qm", playoff_scale: bool = False) -> str:
    if playoff_scale and comp_level != "qm":
        return "playoff"
    return "0" if n <= 0 else "1-2" if n < 3 else "3+"


@dataclass
class _Record:
    result: MatchResult
    prediction: Prediction
    min_here: int  # fewest earlier matches at this event among the six teams
    min_before: int  # fewest matches at earlier events among the six teams

    @property
    def margin(self) -> float:
        p = self.prediction
        return (p.red_mean + p.red_fouls) - (p.blue_mean + p.blue_fouls)

    @property
    def x(self) -> float:
        # The logistic's input: the margin in standard deviations when the model knows
        # its spread, else the plain margin.
        p = self.prediction
        if p.red_var is not None and p.blue_var is not None and p.red_var + p.blue_var > 0:
            return self.margin / math.sqrt(p.red_var + p.blue_var + p.red_fouls_var + p.blue_fouls_var)
        return self.margin

    @property
    def y(self) -> float:
        return 1.0 if self.result.red_score > self.result.blue_score else 0.0


def run_replay(season: Season, model: TeamModel, *, upset_floor: bool = False, playoff_scale: bool = False) -> dict:
    played_here: dict[tuple[str, str], int] = defaultdict(int)
    played_total: dict[str, int] = defaultdict(int)
    played_before_event: dict[tuple[str, str], int] = {}
    last_match_of: dict[tuple[str, str], str] = {}
    for result in season.results:
        for team in result.card.red + result.card.blue:
            last_match_of[(team, result.card.event_key)] = result.card.match_key

    records: list[_Record] = []
    snapshots: dict[tuple[str, str], float] = {}
    for result in season.results:
        card = result.card
        teams = card.red + card.blue
        for team in teams:
            played_before_event.setdefault((team, card.event_key), played_total[team] - played_here[(team, card.event_key)])
        prediction = model.predict(card)
        if result.red_score != result.blue_score:
            records.append(_Record(
                result=result,
                prediction=prediction,
                min_here=min(played_here[(team, card.event_key)] for team in teams),
                min_before=min(played_before_event[(team, card.event_key)] for team in teams),
            ))
        model.observe(result)
        for team in teams:
            played_here[(team, card.event_key)] += 1
            played_total[team] += 1
            if last_match_of[(team, card.event_key)] == card.match_key:
                snapshots[(team, card.event_key)] = model.team_points(team, card.event_key)

    train = [r for r in records if r.result.card.event_key not in season.holdout_events]
    test = [r for r in records if r.result.card.event_key in season.holdout_events]
    single = fit_logistic_scale([r.x for r in train], [r.y for r in train])
    buckets = ("0", "1-2", "3+", "playoff") if playoff_scale else ("0", "1-2", "3+")

    def bucket_of(record: _Record) -> str:
        return _bucket(record.min_here, record.result.card.comp_level, playoff_scale)

    by_bucket = {
        b: fit_logistic_scale([r.x for r in train if bucket_of(r) == b], [r.y for r in train if bucket_of(r) == b])
        for b in buckets
    }

    floor = 0.0
    if upset_floor:
        groups = {b: ([r.x for r in train if bucket_of(r) == b], [r.y for r in train if bucket_of(r) == b]) for b in buckets}
        floor, by_bucket = fit_upset_floor(groups)

    def prob(record: _Record) -> float:
        return floored(by_bucket[bucket_of(record)] * record.x, floor)

    # Does the model's spread mean what it says? Share of held-out alliance totals inside
    # the predicted 80% interval (models that give variances).
    inside = total = 0
    for r in test:
        for mean, var, actual in (
            (r.prediction.red_mean, r.prediction.red_var, r.result.red_points),
            (r.prediction.blue_mean, r.prediction.blue_var, r.result.blue_points),
        ):
            if var is None or actual is None:
                continue
            total += 1
            inside += abs(actual - mean) <= 1.2816 * math.sqrt(max(0.0, var))

    def slice_scores(predicate: Callable[[_Record], bool]) -> dict:
        chosen = [r for r in test if predicate(r)]
        return outcome_scores([prob(r) for r in chosen], [r.y for r in chosen])

    # Alliance points error: does the model's number mean points?
    errors = [
        abs(mean - actual)
        for r in test
        for mean, actual in ((r.prediction.red_mean, r.result.red_points), (r.prediction.blue_mean, r.result.blue_points))
        if actual is not None
    ]

    # Next event: a team's estimate when an event ends against what it scored per match
    # at its next event (5+ matches each). The target is the robots' findings, whose
    # teleop is ingest's split of the alliance total: it favours a model that learns
    # from that same split. Alliance-points error has no such bias.
    robot_points: dict[tuple[str, str], list[float]] = defaultdict(list)
    event_first: dict[str, int] = {}
    for result in season.results:
        event_first.setdefault(result.card.event_key, result.card.time)
        for team, mp in result.robots.items():
            robot_points[(team, result.card.event_key)].append(_total(mp))
    events_of: dict[str, list[str]] = defaultdict(list)
    for (team, event_key) in snapshots:
        events_of[team].append(event_key)
    pairs = []
    for team, event_keys in events_of.items():
        ordered = sorted(event_keys, key=lambda e: event_first[e])
        for a, b in zip(ordered, ordered[1:]):
            if len(robot_points[(team, a)]) >= 5 and len(robot_points[(team, b)]) >= 5:
                actual = robot_points[(team, b)]
                pairs.append((snapshots[(team, a)], sum(actual) / len(actual)))

    # Bonus RPs (models that predict hub and tower parts): Brier of each RP's chance,
    # against always predicting the training events' base rate.
    from app.services.ratings.ranking_points import BONUS_RPS, bonus_chances, event_thresholds, fit_calibration, margin_z

    by_event: dict[str, list[dict]] = defaultdict(list)
    for result in season.results:
        by_event[result.card.event_key].extend(c for c in (result.red_components, result.blue_components) if c)
    thresholds = {event: event_thresholds(season.year, comps) for event, comps in by_event.items()}
    base_rates = {}
    for rp in BONUS_RPS.get(season.year, {}):
        flags = [bool(c.get(f"{rp}_rp")) for r in train for c in (r.result.red_components, r.result.blue_components) if c and f"{rp}_rp" in c]
        base_rates[rp] = sum(flags) / len(flags) if flags else 0.0
    rp_rules = BONUS_RPS.get(season.year, {})

    def rp_rows(records: list[_Record], rp: str):
        part = rp_rules[rp][0]
        for r in records:
            for comps, parts in (
                (r.result.red_components, {"hub": r.prediction.red_hub, "tower": r.prediction.red_tower}),
                (r.result.blue_components, {"hub": r.prediction.blue_hub, "tower": r.prediction.blue_tower}),
            ):
                if comps and f"{rp}_rp" in comps and parts[part] is not None:
                    yield r, comps, parts

    calibration = {}
    for rp in rp_rules:
        zs, ys = [], []
        for r, comps, parts in rp_rows(train, rp):
            value = parts[rp_rules[rp][0]]
            zs.append(margin_z(thresholds[r.result.card.event_key][rp], value[0], value[1]))
            ys.append(1.0 if comps[f"{rp}_rp"] else 0.0)
        if zs:
            calibration[rp] = fit_calibration(zs, ys)
    rp_scores: dict[str, dict] = {}
    for rp in rp_rules:
        calibrated_err = 0.0
        model_err, base_err, n = 0.0, 0.0, 0
        for r in test:
            for comps, hub, tower in (
                (r.result.red_components, r.prediction.red_hub, r.prediction.red_tower),
                (r.result.blue_components, r.prediction.blue_hub, r.prediction.blue_tower),
            ):
                if not comps or f"{rp}_rp" not in comps or hub is None:
                    continue
                chance = bonus_chances(season.year, thresholds[r.result.card.event_key], hub, tower).get(rp)
                if chance is None:
                    continue
                calibrated = bonus_chances(season.year, thresholds[r.result.card.event_key], hub, tower, calibration).get(rp, chance)
                y = 1.0 if comps[f"{rp}_rp"] else 0.0
                model_err += (chance - y) ** 2
                calibrated_err += (calibrated - y) ** 2
                base_err += (base_rates[rp] - y) ** 2
                n += 1
        if n:
            rp_scores[rp] = {
                "n": n, "brier": round(model_err / n, 4), "calibrated_brier": round(calibrated_err / n, 4),
                "calibration": [round(v, 4) for v in calibration.get(rp, (0.0, 0.0))],
                "base_rate_brier": round(base_err / n, 4), "base_rate": round(base_rates[rp], 4),
            }

    probs = [prob(r) for r in test]
    return {
        "bonus_rp": rp_scores,
        "model": model.name,
        "train_matches": len(train),
        "scale": {"single": round(single, 5), "by_matches_seen_here": {b: round(k, 5) for b, k in by_bucket.items()}, "upset_floor": floor},
        "alliance_interval_80": {"n": total, "coverage": round(inside / total, 4) if total else None},
        "matches": {
            "all": slice_scores(lambda r: True),
            "quals": slice_scores(lambda r: r.result.card.comp_level == "qm"),
            "playoffs": slice_scores(lambda r: r.result.card.comp_level != "qm"),
            "nobody has played here yet": slice_scores(lambda r: r.min_here == 0),
            "everyone has 3+ matches here": slice_scores(lambda r: r.min_here >= 3),
            "a team at its first event": slice_scores(lambda r: r.min_before == 0),
        },
        "alliance_points_error": {
            "n": len(errors),
            "mean_abs": round(sum(errors) / len(errors), 2) if errors else None,
        },
        "next_event": {
            "pairs": len(pairs),
            "spearman": round(spearman([a for a, _ in pairs], [b for _, b in pairs]) or 0.0, 4),
            "pearson": round(pearson([a for a, _ in pairs], [b for _, b in pairs]) or 0.0, 4),
        },
        "calibration": calibration_table(probs, [r.y for r in test]),
    }


# ── Models ───────────────────────────────────────────────────────────────

class StrengthV15:
    # The shipped rating (ratings/strength.py) as a replay model.
    def __init__(self, typical: dict[str, float], params: StrengthParams, name: str = "v15"):
        self.name = name
        self.typical = typical
        self.params = params
        self.matches: dict[str, list[tuple[str, MatchPoints]]] = defaultdict(list)
        self.event_start: dict[str, int] = {}

    def _estimate(self, team: str, event_key: str) -> float:
        start = self.event_start.get(event_key)
        items = self.matches.get(team, [])
        here = [mp for e, mp in items if e == event_key]
        before = [mp for e, mp in items if e != event_key and (start is None or mp.time < start)]
        return estimate_strength(here, before, self.typical, self.params).points

    def predict(self, card: MatchCard) -> Prediction:
        self.event_start.setdefault(card.event_key, card.event_start)
        return Prediction(
            red_mean=sum(self._estimate(t, card.event_key) for t in card.red),
            blue_mean=sum(self._estimate(t, card.event_key) for t in card.blue),
        )

    def observe(self, result: MatchResult) -> None:
        for team, mp in result.robots.items():
            self.matches[team].append((result.card.event_key, mp))

    def team_points(self, team_key: str, event_key: str) -> float:
        return self._estimate(team_key, event_key)


class FuelOnly:
    # The win probability before v15: teleop here shrunk n/(n+3) toward the team's
    # previous event's mean.
    def __init__(self, typical: dict[str, float], name: str = "fuel_only"):
        self.name = name
        self.typical_teleop = typical.get("teleop", 0.0)
        self.by_event: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.events_of: dict[str, list[tuple[int, str]]] = defaultdict(list)
        self.event_last: dict[str, int] = {}
        self.event_start: dict[str, int] = {}

    def _estimate(self, team: str, event_key: str) -> float:
        here = self.by_event.get((team, event_key), [])
        start = self.event_start.get(event_key, 0)
        earlier = [e for _t, e in self.events_of.get(team, []) if e != event_key and self.event_last.get(e, 0) < start]
        prior = None
        if earlier:
            values = self.by_event[(team, earlier[-1])]
            prior = sum(values) / len(values) if values else None
        if here and prior is not None:
            return (sum(here) + 3.0 * prior) / (len(here) + 3.0)
        if here:
            return sum(here) / len(here)
        return prior if prior is not None else self.typical_teleop

    def predict(self, card: MatchCard) -> Prediction:
        self.event_start.setdefault(card.event_key, card.event_start)
        return Prediction(
            red_mean=sum(self._estimate(t, card.event_key) for t in card.red),
            blue_mean=sum(self._estimate(t, card.event_key) for t in card.blue),
        )

    def observe(self, result: MatchResult) -> None:
        event_key = result.card.event_key
        self.event_last[event_key] = max(self.event_last.get(event_key, 0), result.card.time)
        for team, mp in result.robots.items():
            if mp.teleop is None:
                continue
            if (team, event_key) not in self.by_event:
                self.events_of[team].append((result.card.time, event_key))
            self.by_event[(team, event_key)].append(mp.teleop)

    def team_points(self, team_key: str, event_key: str) -> float:
        return self._estimate(team_key, event_key)


# ── Loading a season from the database ───────────────────────────────────

def _with_fouls(row) -> dict:
    # The season components plus the foul points this alliance received (columns, any game).
    if row is None:
        return {}
    out = dict(row.components or {})
    if row.foul_points_received is not None:
        out["foul_points_received"] = row.foul_points_received
    out["ranking_points"] = row.ranking_points
    out["surrogate_team_keys"] = list(row.surrogate_team_keys or [])
    out["result"] = row.result
    return out


def load_season(db, year: int, *, evaluation_split: bool = True) -> Season:
    # Read-only. Teams come from the alliance results when stored (they carry
    # surrogates), else from match_teams; scores from the alliance results, else from
    # the official findings' summaries.
    from sqlalchemy import func

    from app.db import models

    match_time = func.coalesce(models.Match.actual_time, models.Match.time, models.Match.predicted_time)
    cards_raw = {
        key.lower(): (str(event_key), int(t), str(level or "").lower())
        for key, event_key, t, level in db.query(models.Match.match_key, models.Match.event_key, match_time, models.Match.comp_level)
        .join(models.Event, models.Event.event_key == models.Match.event_key)
        .filter(models.Event.year == year, match_time.isnot(None))
    }
    # Only the columns used below: whole rows also decode raw_breakdown, the stored TBA
    # breakdown, and JSON decoding was most of the season's load time.
    result = models.MatchAllianceResult
    alliance_rows: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in db.query(
        result.match_key, result.alliance, result.team_keys, result.surrogate_team_keys, result.total_score,
        result.score_without_fouls, result.foul_points_received, result.ranking_points, result.result, result.components,
    ).filter(result.season_year == year):
        alliance_rows[row.match_key.lower()][row.alliance] = row

    robots: dict[str, dict[str, MatchPoints]] = defaultdict(dict)
    # Only the summary fields used here, picked out by the database as text: decoding
    # each finding's whole summary (per-shift hub tables and more), and then even small
    # JSON values one by one, dominated load time.
    summary = models.TeamMatchFinding.summary
    finding_filter = (
        models.TeamMatchFinding.event_key.like(f"{year}%"),
        models.TeamMatchFinding.source.in_(OFFICIAL_SOURCES),
    )
    rows = (
        db.query(
            func.lower(models.TeamMatchFinding.match_key),
            func.lower(models.TeamMatchFinding.team_key),
            models.TeamMatchFinding.source,
            models.TeamMatchFinding.fuel_scoring_rate,
            models.TeamMatchFinding.auto_contribution,
            summary[("status", "auto_allocation")].as_string(),
            summary[("status", "endgame_tower")].as_string(),
        )
        .filter(*finding_filter)
        .all()
    )
    # Ingest rows win over the old backfill rows for the same match.
    rows.sort(key=lambda row: row[2] != "tba_score_breakdown")
    for match_key, team_key, _source, fuel, auto, auto_allocation, endgame in rows:
        if match_key not in cards_raw or team_key in robots[match_key]:
            continue
        status = {key: value for key, value in (("auto_allocation", auto_allocation), ("endgame_tower", endgame)) if value is not None}
        robots[match_key][team_key] = match_points(
            match_key=match_key, time=cards_raw[match_key][1], fuel_rate=fuel, auto=auto, summary={"status": status},
        )

    def _complete(match_key: str) -> bool:
        stored = alliance_rows.get(match_key, {})
        return all(stored.get(side) is not None and stored[side].total_score is not None for side in ("red", "blue"))

    # A match without stored alliance results takes its score from a finding's summary.
    # Rare (results are stored at ingest), so the scores are read only for those.
    summary_scores: dict[str, tuple[int, int]] = {}
    unscored = [key for key in cards_raw if not _complete(key)]
    for i in range(0, len(unscored), 500):
        chunk = unscored[i : i + 500]
        score_rows = (
            db.query(
                func.lower(models.TeamMatchFinding.match_key),
                func.lower(models.TeamMatchFinding.team_key),
                models.TeamMatchFinding.source,
                summary["red_score"],
                summary["blue_score"],
            )
            .filter(*finding_filter, func.lower(models.TeamMatchFinding.match_key).in_(chunk))
            .all()
        )
        score_rows.sort(key=lambda row: row[2] != "tba_score_breakdown")
        seen: set[tuple[str, str]] = set()
        for match_key, team_key, _source, red, blue in score_rows:
            if (match_key, team_key) in seen:
                continue
            seen.add((match_key, team_key))
            if match_key not in summary_scores and isinstance(red, (int, float)) and isinstance(blue, (int, float)) and red >= 0 and blue >= 0:
                summary_scores[match_key] = (int(red), int(blue))

    # Lineups for those same matches (stored results carry their own teams).
    teams: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"red": [], "blue": []})
    scored_from_summary = list(summary_scores)
    for i in range(0, len(scored_from_summary), 500):
        chunk = scored_from_summary[i : i + 500]
        for match_key, team_key, alliance in db.query(models.MatchTeam.match_key, models.MatchTeam.team_key, models.MatchTeam.alliance).filter(
            models.MatchTeam.event_key.like(f"{year}%"), func.lower(models.MatchTeam.match_key).in_(chunk)
        ):
            if alliance in ("red", "blue"):
                teams[match_key.lower()][alliance].append(team_key.lower())

    event_start: dict[str, int] = {}
    for event_key, t, _level in cards_raw.values():
        event_start[event_key] = min(event_start.get(event_key, t), t)

    results: list[MatchResult] = []
    for match_key, (event_key, t, level) in cards_raw.items():
        stored = alliance_rows.get(match_key, {})
        red_row, blue_row = stored.get("red"), stored.get("blue")
        if red_row is not None and blue_row is not None and red_row.total_score is not None and blue_row.total_score is not None:
            red_teams, blue_teams = tuple(red_row.team_keys), tuple(blue_row.team_keys)
            red_score, blue_score = red_row.total_score, blue_row.total_score
            red_points, blue_points = red_row.score_without_fouls, blue_row.score_without_fouls
        elif match_key in summary_scores:
            red_teams, blue_teams = tuple(teams[match_key]["red"]), tuple(teams[match_key]["blue"])
            red_score, blue_score = summary_scores[match_key]
            red_points = blue_points = None
        else:
            continue
        if len(red_teams) != 3 or len(blue_teams) != 3:
            continue
        card = MatchCard(match_key, event_key, t, level, event_start[event_key], red_teams, blue_teams)
        results.append(MatchResult(
            card=card,
            red_score=red_score,
            blue_score=blue_score,
            red_points=float(red_points) if red_points is not None else None,
            blue_points=float(blue_points) if blue_points is not None else None,
            robots=robots.get(match_key, {}),
            red_components=_with_fouls(red_row),
            blue_components=_with_fouls(blue_row),
        ))
    results.sort(key=lambda r: (r.card.time, r.card.match_key))
    if not evaluation_split:
        # The live team model replays every result and never reads the split.
        return Season(year=year, results=results, holdout_events=set(), typical={})
    holdout = split_holdout(results)
    training = {r.card.event_key for r in results} - holdout
    return Season(year=year, results=results, holdout_events=holdout, typical=typical_robot(results, training))
