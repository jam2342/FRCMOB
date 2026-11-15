# Offline paired comparison of a weekly rehearsal's saved forecasts against production's
# correction network, retrained on the same publication clock. Historical development
# evidence only: nothing here serves, activates or writes a production setting.
from __future__ import annotations

import copy
from bisect import bisect_right
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings import calibrations as layers, weekly_rehearsal as w
from app.services.ratings.research_eval import paired_cluster_bootstrap
from app.services.ratings.research_filter import ScaleFreeParams
from app.services.ratings.research_rehearsal import noninferiority
from app.services.ratings.state_inputs import digest

SCHEMA = "network-pairing-v1"
POLICY = {
    "season": 2026,
    "matches": "the source rehearsal's official-event records (actual time, three teams a side, scored)",
    "forecast_clock": "actual start time; a forecast sees results posted strictly before it",
    "state_updates": "both team models observe every posted result, replayed in playing order (time, key)",
    "job_hour_utc": w.POLICY["job_hour_utc"], "publication_hour_utc": w.POLICY["publication_hour_utc"],
    "version_rule": "a version applies to forecasts strictly after its publication",
    "network_labels": "results posted strictly before the Monday job; ties excluded; no event or week filter",
    "network_features": "fresh playing-order replay of the labels' results at the job, as production's trainer does",
    "network_trainer": "scripts/train_win_correction.fit (torch, seed 0, 300 epochs, 16 tanh, Adam .01, decay 1e-3, one thread)",
    "network_serving": "weights rounded to 6 decimals as the trainer writes them; production's numpy forward pass; blend 1",
    "minimum_network_labels": w.POLICY["minimum_fit_matches"],
    "before_first_network": "production base probability (production serves a season without weights this way)",
    "candidate": "source rehearsal's saved active forecasts, unchanged",
    "static": "both pipelines frozen at the parameters in effect for the first newest-event forecast; states keep observing posted results",
    "newest_events": "latest ceil(20%) of events by first actual match (source rehearsal definition)",
    "eligible": "both alliances have explainable points and the match is not a tie (source rehearsal win rule)",
    "slices": ["all", "early_14_days", "week4_on", "newest_events", "championships", "first_here", "playoffs"],
    "metrics": ["brier", "log_loss"], "bootstrap": {"draws": 2000, "seed": 7, "level": .9, "cluster": "event"},
    "gate": {"comparison": "rolling candidate minus rolling network, newest events, Brier",
             "rule": "research_rehearsal.noninferiority", "margin": 0.0,
             "criterion": "late-event non-regression is established only if the rolling gate passes and the static check has not failed"},
    "serving_eligible": False,
}
SNAPSHOT_EVERY, SNAPSHOT_KEEP = 100, 8
TRAINER = Path(__file__).resolve().parents[3] / "scripts" / "train_win_correction.py"


def label(result):
    return .5 if result.red_score == result.blue_score else float(result.red_score > result.blue_score)


def production_factory(baseline, priors, rookies):
    # Production's 2026 team model as phase 3 reproduced it (served-probability parity).
    from app.services.ratings.team_model import SeasonTeamModel

    beta = float(baseline.get("prior_beta", 0.0))
    multipliers = {team: math.exp(beta * value) for team, value in priors.items()}
    for team, year in rookies.items():
        if year == POLICY["season"]:
            multipliers.setdefault(team, float(baseline.get("rookie_multiplier", 1.0)))
    known = float(baseline.get("prior_known_var_multiplier", 1.0))

    def factory():
        return SeasonTeamModel(dict(baseline["team_model_priors"]), baseline["team_model_params"],
                               team_multipliers=multipliers, team_var_multipliers={t: known for t in priors},
                               week_multipliers=list(baseline.get("week_multipliers") or []))
    return factory


def shadow(model, card):
    # predict() enters teams into the target event; copy only what it writes.
    s = copy.copy(model)
    s.blocks = {k: (copy.deepcopy(v) if k[0] == card.event_key else v) for k, v in model.blocks.items()}
    s.history = dict(model.history)
    for team in card.red + card.blue:
        if team in s.history:
            s.history[team] = list(s.history[team])
    s.towers = dict(model.towers)
    s.event_start = dict(model.event_start)
    return s


def production_forecast(model, card, here, total):
    from app.services.events.win_probability import red_win_prob
    from app.services.ratings.team_model_service import correction_features

    s = shadow(model, card)
    prediction = s.predict(card)
    teams = card.red + card.blue
    seen = min(here.get((t, card.event_key), 0) for t in teams)
    before = min(total.get(t, 0) - here.get((t, card.event_key), 0) for t in teams)
    x = correction_features(s, card, prediction, seen, before)
    margin = (prediction.red_mean + prediction.red_fouls) - (prediction.blue_mean + prediction.blue_fouls)
    sd = math.sqrt(max((prediction.red_var or 0) + (prediction.blue_var or 0) + prediction.red_fouls_var + prediction.blue_fouls_var, 1e-6))
    return red_win_prob(margin, sd, seen, playoff=card.comp_level != "qm"), x


def count(here, total, card):
    for team in card.red + card.blue:
        total[team] = total.get(team, 0) + 1
        here[(team, card.event_key)] = here.get((team, card.event_key), 0) + 1


def immediate(results, factory):
    # Production's trainer and phase 3's replay: each result is known right after its match.
    model, here, total, out = factory(), {}, {}, []
    for r in sorted(results, key=lambda r: (r.card.time, r.card.match_key)):
        base, x = production_forecast(model, r.card, here, total)
        out.append((r, base, x))
        model.observe(r)
        count(here, total, r.card)
    return out


class PublishedReplay:
    # The production team model fed only results posted before each forecast; a late
    # result for an earlier match rewinds to a snapshot and replays in playing order.
    def __init__(self, results, posted, factory):
        self.factory = factory
        self.pending = sorted((posted[r.card.match_key], r.card.match_key, r) for r in results
                              if posted.get(r.card.match_key) is not None)
        self.model, self.applied, self.ordered, self.keys = factory(), 0, [], []
        self.here, self.total, self.last, self.snapshots, self.rewinds = {}, {}, (-1, ""), [], 0

    def advance(self, time):
        while self.applied < len(self.pending) and self.pending[self.applied][0] < time:
            r = self.pending[self.applied][2]
            count(self.here, self.total, r.card)
            key = (r.card.time, r.card.match_key)
            position = bisect_right(self.keys, key)
            self.keys.insert(position, key)
            self.ordered.insert(position, r)
            if key < self.last:
                self.rewinds += 1
                self.snapshots = [s for s in self.snapshots if s["key"] < key]
                start = 0
                if self.snapshots:
                    self.model, start = copy.deepcopy(self.snapshots[-1]["model"]), self.snapshots[-1]["count"]
                else:
                    self.model = self.factory()
                for known in self.ordered[start:]:
                    self.model.observe(known)
            else:
                self.model.observe(r)
            self.last = max(self.last, key)
            self.applied += 1
            if not self.snapshots or len(self.ordered) - self.snapshots[-1]["count"] >= SNAPSHOT_EVERY:
                self.snapshots.append({"key": self.last, "count": len(self.ordered), "model": copy.deepcopy(self.model)})
                self.snapshots = self.snapshots[-SNAPSHOT_KEEP:]

    def forecast(self, card):
        return production_forecast(self.model, card, self.here, self.total)


def network_probability(state, x):
    # win_correction.corrected's forward pass with CORRECTION_BLEND = 1.
    hidden = np.tanh(np.array(state["0.weight"]) @ np.array(x, dtype=float) + np.array(state["0.bias"]))
    logit = float((np.array(state["2.weight"]) @ hidden)[0] + state["2.bias"][0])
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))


def fit_network(rows):
    import torch

    spec = importlib.util.spec_from_file_location("train_win_correction", TRAINER)
    trainer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trainer)
    torch.set_num_threads(1)
    net = trainer.fit(np.array([r["x"] for r in rows]), np.array([r["y"] for r in rows]))
    return {k: v.detach().numpy().round(6).tolist() for k, v in net.state_dict().items()}


def jobs(records):
    first = layers.monday(datetime.fromtimestamp(records[0]["result"].card.time, timezone.utc))
    final = layers.monday(datetime.fromtimestamp(records[-1]["result"].card.time, timezone.utc)) + timedelta(days=7)
    out, week = [], first
    while week <= final:
        out.append({"week": week.isoformat(), "job": int((week + timedelta(hours=POLICY["job_hour_utc"])).timestamp()),
                    "publication": int((week + timedelta(hours=POLICY["publication_hour_utc"])).timestamp())})
        week += timedelta(days=7)
    return out


def train_networks(records, production, factory, fit=fit_network):
    posted = {r["result"].card.match_key: r["posted"] for r in records}
    out = []
    for job in jobs(records):
        known = [production[k] for k, p in posted.items() if p is not None and p < job["job"]]
        rows = [{"key": r.card.match_key, "event": r.card.event_key, "x": x, "y": label(r)}
                for r, _, x in immediate(known, factory) if label(r) != .5]
        entry = {**job, "labels": len(rows), "results": len(known),
                 "label_keys_sha256": digest(sorted(r["key"] for r in rows)),
                 "latest_label_posted": max((posted[r["key"]] for r in rows), default=None)}
        if len(rows) >= POLICY["minimum_network_labels"]:
            entry["state"] = fit(rows)
            entry["weights_sha256"] = digest(entry["state"])
        out.append(entry)
    return out


def in_effect(versions, time):
    chosen = None
    for v in versions:
        if v["publication"] < time and "state" in v:
            chosen = v
    return chosen


def load_source(root):
    head = w.read_object(root / "checkpoints", json.loads((root / "head.json").read_text())["sha256"])
    w.journal_hashes(root, head["journals"])
    frames = [f for sha in head["journals"] for f in w.read_object(root / "journals", sha)["forecasts"]]
    return head, frames


def read_only(path):
    return create_engine(f"sqlite:///file:{Path(path).resolve()}?mode=ro&uri=true")


def frozen_candidate(root, data, frames, version_id, keys):
    # Replays the source state with one saved version's parameters and layers.
    engine = read_only(root / "scratch.sqlite")
    try:
        with Session(engine) as db:
            bootstrap = db.query(models.RatingCalibration).filter_by(season=data.year, recipe=w.state.BOOTSTRAP_RECIPE).one()
            params, fitted, sha = w.version(db, version_id)
            artifact = bootstrap.params["artifact"]
            context = bootstrap.params["context"]
    finally:
        engine.dispose()
    manifest = json.loads((root / "manifest.json").read_text())
    if digest(artifact) != manifest["artifact_sha256"] or data.source_sha256 != manifest["data_sha256"]:
        raise ValueError("source rehearsal inputs changed")
    replay = w.Replay(data, context, ScaleFreeParams(**artifact["params"]))
    replay.add_model(version_id, params)
    wanted, out = set(keys), {}
    for record in data.records:
        card = record["result"].card
        replay.advance(card.time)
        if card.match_key not in wanted:
            continue
        card = replace(card, event_start=min(replay.starts.get(card.event_key, card.time), card.time))
        raw = w.forecast(replay.models[version_id], card, replay.here, replay.total, min(replay.first or card.time, card.time))
        out[card.match_key] = w.predicted(raw, fitted, version_id, sha)
    return out, sha


def newest_events(frames):
    starts = {}
    for f in frames:
        starts[f["event"]] = min(starts.get(f["event"], f["time"]), f["time"])
    return starts, set(sorted(starts, key=lambda k: (starts[k], k))[-max(1, math.ceil(len(starts) * .2)):])


def slices(frames):
    starts, newest = newest_events(frames)
    first = min(starts.values())
    return {"all": lambda r: True, "early_14_days": lambda r: r["time"] < first + 14 * 86400,
            "week4_on": lambda r: r["time"] >= first + 21 * 86400, "newest_events": lambda r: r["event"] in newest,
            "championships": lambda r: r["event_type"] in (2, 3, 4, 5), "first_here": lambda r: r["first_here"],
            "playoffs": lambda r: r["playoff"]}


def eligibility(result):
    if result.red_points is None or result.blue_points is None:
        return "unexplained_points"
    return "tie" if label(result) == .5 else "eligible"


def loss(p, y, metric):
    q = min(1 - 1e-9, max(1e-9, p))
    return (q - y) ** 2 if metric == "brier" else -(y * math.log(q) + (1 - y) * math.log(1 - q))


def compare(rows, a, b, metric):
    values = {name: {r["key"]: (r["event"], loss(r[name], r["y"], metric)) for r in rows
                     if r["eligibility"] == "eligible" and r.get(name) is not None} for name in (a, b)}
    if set(values[a]) != set(values[b]):
        raise ValueError("paired comparison must use identical eligible matches")
    out = paired_cluster_bootstrap(values[a], values[b], n=POLICY["bootstrap"]["draws"], seed=POLICY["bootstrap"]["seed"])
    for name in (a, b):
        out[name] = sum(v[1] for v in values[name].values()) / len(values[name]) if values[name] else None
    out["keys_sha256"] = digest(sorted(values[a]))
    return out


def assemble(frames, data, issued, networks, static_network, frozen, in_sample):
    source = {r["result"].card.match_key: r for r in data.records}
    if [f["key"] for f in frames] != [r["result"].card.match_key for r in data.records]:
        raise ValueError("source forecasts and records differ")
    _, newest = newest_events(frames)
    rows = []
    for f in frames:
        record, (base, x) = source[f["key"]], issued[f["key"]]
        if record["result"].card.time != f["time"]:
            raise ValueError("source forecast time differs from the actual start")
        rolling = in_effect(networks, f["time"])
        active = f["forecasts"]["active"]
        row = {"key": f["key"], "event": f["event"], "event_type": f["event_type"], "time": f["time"], "posted": record["posted"],
               "playoff": f["playoff"], "first_here": f["first_here"], "newest": f["event"] in newest,
               "y": label(record["result"]), "eligibility": eligibility(record["result"]), "features": x,
               "candidate": active["p"], "candidate_version": active["version"], "candidate_params_sha256": active["params_sha256"],
               "production_base": base,
               "network_rolling": network_probability(rolling["state"], x) if rolling else base,
               "network_rolling_weights_sha256": rolling["weights_sha256"] if rolling else None,
               "network_in_sample": in_sample(base, x)}
        if f["event"] in newest:
            row["network_static"] = network_probability(static_network["state"], x) if static_network else base
            row["network_static_weights_sha256"] = static_network["weights_sha256"] if static_network else None
            row["candidate_static"] = frozen[f["key"]]["p"]
            row["candidate_static_params_sha256"] = frozen[f["key"]]["params_sha256"]
        rows.append(row)
    return rows


def static_versions(frames, networks):
    _, newest = newest_events(frames)
    first = min((f for f in frames if f["event"] in newest), key=lambda f: (f["time"], f["key"]))
    network = in_effect(networks, first["time"])
    if network and network["latest_label_posted"] is not None and network["latest_label_posted"] >= first["time"]:
        raise ValueError("static network saw a label from the evaluation window")
    return first, first["forecasts"]["active"]["version"], network


ROLLING = (("candidate", "network_rolling"), ("candidate", "production_base"), ("network_rolling", "production_base"))
STATIC = (("candidate_static", "network_static"), ("network_rolling", "network_static"), ("candidate", "candidate_static"))
REFERENCE = (("candidate", "network_in_sample"),)


def summarize(rows, frames):
    chosen = slices(frames)
    report = {"rolling": {}, "static": {}, "in_sample_reference": {}}
    for name, keep in chosen.items():
        subset = [r for r in rows if keep(r)]
        report["rolling"][name] = {f"{a}_minus_{b}": {m: compare(subset, a, b, m) for m in POLICY["metrics"]} for a, b in ROLLING}
        report["in_sample_reference"][name] = {f"{a}_minus_{b}": {m: compare(subset, a, b, m) for m in POLICY["metrics"]} for a, b in REFERENCE}
    newest = [r for r in rows if chosen["newest_events"](r)]
    report["static"]["newest_events"] = {f"{a}_minus_{b}": {m: compare(newest, a, b, m) for m in POLICY["metrics"]} for a, b in STATIC}
    rolling = report["rolling"]["newest_events"]["candidate_minus_network_rolling"]["brier"]
    static = report["static"]["newest_events"]["candidate_static_minus_network_static"]["brier"]
    report["gate"] = {"rolling": noninferiority(rolling, POLICY["gate"]["margin"]),
                      "static": noninferiority(static, POLICY["gate"]["margin"])}
    report["gate"]["late_event_non_regression_established"] = report["gate"]["rolling"] == "passed" and report["gate"]["static"] != "failed"
    reasons = {}
    for r in rows:
        reasons[r["eligibility"]] = reasons.get(r["eligibility"], 0) + 1
    report["exclusions"] = {"all": reasons, "newest_events": {k: sum(r["eligibility"] == k for r in newest) for k in reasons},
                            "missing_publication_scored_never_trained": sum(r["posted"] is None for r in rows)}
    return report
