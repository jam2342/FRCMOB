#!/usr/bin/env python3
# Dry run of a state-served season on a scratch database: a past season replayed as a fake new
# season through the production code (install, Monday jobs, serving engine, ratings,
# predictions). With --parity, served forecasts for a sample of matches are compared with a
# weekly rehearsal of the same real season. Writes only under --out; never touches production.
from __future__ import annotations

import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import argparse
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db import models  # noqa: E402
from app.services import seasons  # noqa: E402
from app.services.ratings import calibrations as layers, component_spec, state_serving as serving, state_weekly as job  # noqa: E402
from app.services.ratings import weekly_rehearsal as w  # noqa: E402
from app.services.ratings.component_screen import load  # noqa: E402
from app.services.ratings.replay import MatchCard  # noqa: E402

WEEK = 7*86400


def relabel(data, source, target):
    # The same matches, keys and dates moved to `target`; whole weeks keep Mondays aligned.
    shift = 52*WEEK*(target-source)
    old, new = str(source), str(target)
    records = []
    for r in data.records:
        result = r["result"]
        card = replace(result.card, match_key=new+result.card.match_key[4:], event_key=new+result.card.event_key[4:],
                       time=result.card.time+shift, event_start=result.card.event_start+shift)
        records.append({**r, "result": replace(result, card=card), "posted": r["posted"]+shift if r["posted"] is not None else None})
    assert all(r["result"].card.match_key.startswith(new) for r in records) and all(k.startswith(old) for k in [d["result"].card.match_key for d in data.records])
    return SimpleNamespace(year=target, records=records, rookies=set(data.rookies), shift=shift)


def registry(path, source, target):
    raw = json.loads(Path(component_spec.REGISTRY).read_text())
    entry = copy.deepcopy(next(e for e in raw["seasons"] if e["season"] == source))
    entry["season"] = target
    raw["seasons"] = [e for e in raw["seasons"] if e["season"] != target]+[entry]
    path.write_text(json.dumps(raw))
    return path


def main():
    parser = argparse.ArgumentParser(description="Dry run of a state-served season (scratch database only)")
    parser.add_argument("--cache", required=True, type=Path, help="TBA research cache")
    parser.add_argument("--source", type=int, default=2026, help="real season replayed as the fake season")
    parser.add_argument("--target", type=int, default=2027)
    parser.add_argument("--artifact", required=True, type=Path, help="bootstrap for the source season, built from earlier seasons")
    parser.add_argument("--parity", type=Path, help="weekly rehearsal folder of the source season (same artifact and recipe)")
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if any(p.resolve().is_relative_to(ROOT.parent) for p in (args.out, args.cache, args.artifact)):
        parser.error("dry-run files stay outside the public repository")
    os.umask(0o077)
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out/"scratch.sqlite").exists():
        parser.error("use a fresh --out folder")
    started = time.perf_counter()
    report = {"source": args.source, "target": args.target, "errors": []}

    # Switches on in this process only, and the fake season declared Tier A like its source.
    for name in ("rating_state_serving_enabled", "rating_autotune", "rating_prediction_log_enabled"):
        setattr(settings, name, True)
    component_spec.REGISTRY = registry(args.out/"registry.json", args.source, args.target)
    serving.FIRST_STATE_SEASON = min(serving.FIRST_STATE_SEASON, args.target)

    current = relabel(load(args.cache, args.source), args.source, args.target)
    previous = relabel(load(args.cache, args.source-1), args.source-1, args.target-1)
    artifact = json.loads(args.artifact.read_text())
    artifact = artifact.get("artifact", artifact)
    shifted = {**artifact, "season": args.target, "source_seasons": [y+args.target-args.source for y in artifact["source_seasons"]],
               "layer_source_seasons": [y+args.target-args.source for y in artifact["layer_source_seasons"]]}

    engine = w.prepare_database(args.out/"scratch.sqlite")
    with Session(engine) as db:
        teams = sorted({t for c in (current, previous) for r in c.records for t in r["result"].card.red+r["result"].card.blue} | current.rookies | previous.rookies)
        for team in teams:
            year = args.target if team in current.rookies else args.target-1 if team in previous.rookies else None
            db.add(models.Team(team_key=team, team_number=int(team[3:].rstrip("abcdefghijklmnopqrstuvwxyz")), rookie_year=year))
        db.commit()
        boundary = datetime(args.target, 1, 1, tzinfo=timezone.utc).timestamp()
        w.ingest(db, [r for r in previous.records if r["posted"] is not None and r["posted"] < boundary], args.target-1)
        w.ingest(db, [r for r in current.records if r["posted"] is not None], args.target)
        db.commit()
        report["install"] = job.install(db, args.target, shifted, "dry-run")
        db.commit()

        # Timeline: Monday jobs and sampled matches, each at its own moment.
        times = sorted(r["result"].card.time for r in current.records)
        first, last = layers.monday(datetime.fromtimestamp(times[0], timezone.utc)), layers.monday(datetime.fromtimestamp(times[-1], timezone.utc))+timedelta(days=7)
        events = []
        week = first
        while week <= last:
            events += [(week+timedelta(hours=9), "fit", None), (week+timedelta(hours=10), "publish", None)]
            week += timedelta(days=7)
        sample = random.Random(7).sample(current.records, min(args.sample, len(current.records)))
        events += [(datetime.fromtimestamp(r["result"].card.time, timezone.utc), "forecast", r) for r in sample]
        events.sort(key=lambda e: (e[0], {"forecast": 0, "fit": 1, "publish": 2}[e[1]]))
        jobs, served = [], {}
        for when, kind, record in events:
            if kind == "fit":
                safety = job.safety(db, args.target, now=when)
                fitted = job.fit(db, args.target, now=when, code_sha="dry-run") if safety["status"] != "rolled_back" else None
                jobs.append({"week": when.date().isoformat(), "safety": safety["status"], "fit": (fitted or {}).get("status")})
            elif kind == "publish":
                jobs[-1]["publish"] = job.publish(db, args.target, now=when)["status"]
                db.commit()
            else:
                card = record["result"].card
                active = serving.active_version(db, args.target)
                run = serving.build(db, args.target, active, now=card.time)
                starts = [r.card.time for r in run.results if r.card.event_key == card.event_key]
                forecast_card = MatchCard(card.match_key, card.event_key, card.time, card.comp_level,
                                          min(starts+[card.time]), card.red, card.blue)
                _prediction, features = serving.forecast_row(run, forecast_card)
                served[card.match_key] = {"p": features.win_prob, "rm": features.red_points, "bm": features.blue_points,
                                          "version": active.id, "time": card.time}
        report["jobs"] = jobs
        report["published"] = sum(j.get("publish") == "published" for j in jobs)
        report["rollbacks"] = sum(j["safety"] == "rolled_back" for j in jobs)

        if args.parity:
            from app.services.ratings import age_score_rehearsal as age
            frames, _, _, _ = age.source_run(args.parity)
            rehearsed = {str(args.target)+f["key"][4:]: f["forecasts"]["active"] for f in frames}
            diffs = sorted((abs(v["p"]-rehearsed[k]["p"]), max(abs(v["rm"]-rehearsed[k]["rm"]), abs(v["bm"]-rehearsed[k]["bm"])), k)
                           for k, v in served.items())
            report["parity"] = {"matches": len(diffs), "exact_p": sum(d[0] == 0 for d in diffs),
                                "max_abs_p": diffs[-1][0] if diffs else None, "mean_abs_p": sum(d[0] for d in diffs)/len(diffs) if diffs else None,
                                "max_abs_points": max(d[1] for d in diffs) if diffs else None,
                                "worst": [{"key": k, "abs_p": a, "abs_points": b} for a, b, k in diffs[-5:]]}

        # End of season through the serving paths, with the clock at the last match. The prediction
        # log's cross-worker rebuild lock needs Redis, which a scratch run doesn't have; that path
        # is covered by CI and the live services.
        settings.rating_prediction_log_enabled = False
        end = times[-1]+3600
        serving.time = SimpleNamespace(time=lambda: end, perf_counter=time.perf_counter)
        from app.services.events.win_probability import custom_matchup, match_predictions
        from app.services.ratings import team_model_service as service
        from app.services.ratings.accuracy import season_accuracy
        from app.services.ratings.model import recompute_event_ratings
        service.clear_cache()
        smoke = {}
        try:
            run = service.season_run(db, args.target)
            smoke["engine"] = getattr(run, "engine", "legacy")
            event_keys = sorted({r["result"].card.event_key for r in current.records})[:3]
            for event in event_keys:
                db.merge(models.Event(event_key=event, year=args.target, name=event))
                here = sorted({t for r in current.records if r["result"].card.event_key == event for t in r["result"].card.red+r["result"].card.blue})
                for team in here:
                    db.merge(models.EventTeam(event_key=event, team_key=team))
                db.commit()
                ratings = recompute_event_ratings(db, event)
                values = [row["rating_0_100"] for row in ratings["ratings"]]
                cards = [r["result"].card for r in current.records if r["result"].card.event_key == event][:10]
                details = match_predictions(db, event, [(c.match_key, list(c.red), list(c.blue)) for c in cards])
                probs = [d[4].win_prob for d in details.values()]
                points = [p for d in details.values() for p in (d[4].red_points, d[4].blue_points)]
                matchup = custom_matchup(db, event, here[:3], here[3:6])
                smoke[event] = {"ratings": len(values), "rating_range": [min(values), max(values)] if values else None,
                                "null_subscores": all(row["subscores"]["throughput"] is None for row in ratings["ratings"]),
                                "predictions": len(probs), "p_range": [min(probs), max(probs)] if probs else None,
                                "min_points": min(points) if points else None, "matchup_p": matchup.get("red_win_prob")}
            smoke["season_info"] = seasons.season_info(db, args.target)
            smoke["accuracy_available"] = season_accuracy(db, args.target)["available"]
        except Exception as exc:  # report, don't hide: the dry run exists to find these
            report["errors"].append(f"{type(exc).__name__}: {exc}")
        report["smoke"] = smoke
    engine.dispose()
    report["seconds"] = round(time.perf_counter()-started, 1)
    (args.out/"report.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    print(json.dumps({k: report[k] for k in ("published", "rollbacks", "errors", "seconds")} | {"parity": report.get("parity")}, default=str))


if __name__ == "__main__":
    main()
