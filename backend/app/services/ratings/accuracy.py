# The public accuracy record: only forecasts the system actually showed before each match
# (locked, never backfilled), judged on official results. Rebuilding or re-tuning never
# rewrites it (prediction_log.py).
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math

from sqlalchemy.orm import Session

from app.db import models
from app.services.ratings.prediction_log import stored_prediction

BINS = (.5, .6, .7, .8, .9, 1.0001)


def _summary(rows):
    if not rows:
        return {"matches": 0}
    decided = [r for r in rows if r["y"] != .5]
    out = {"matches": len(rows), "decided": len(decided)}
    if decided:
        out["accuracy"] = round(sum(1. if (r["p"] > .5) == (r["y"] == 1.) and r["p"] != .5 else .5 if r["p"] == .5 else 0. for r in decided)/len(decided), 4)
        out["brier"] = round(sum((r["p"]-r["y"])**2 for r in decided)/len(decided), 5)
        out["log_loss"] = round(sum(-math.log(max(1e-9, r["p"] if r["y"] else 1-r["p"])) for r in decided)/len(decided), 5)
    points = [e for r in rows for e in r["points_errors"]]
    if points:
        out["points_mae"] = round(sum(points)/len(points), 2)
    return out


def season_accuracy(db: Session, season: int) -> dict:
    matches = {m.match_key: m for m in db.query(models.Match).join(models.Event).filter(models.Event.year == season)}
    results = defaultdict(dict)
    for r in db.query(models.MatchAllianceResult).filter_by(season_year=season):
        if r.total_score is not None and r.total_score >= 0:
            results[r.match_key][r.alliance] = r
    rows = []
    for log in db.query(models.MatchPredictionLog).filter_by(season=season, backfilled=False).filter(models.MatchPredictionLog.locked_at.is_not(None)):
        match, sides = matches.get(log.match_key), results.get(log.match_key, {})
        if match is None or match.actual_time is None or {"red", "blue"} - sides.keys():
            continue
        shown = stored_prediction(log, match)
        p = shown.get("red_win_prob") if shown.get("available", True) else None
        if p is None:
            continue
        red, blue = sides["red"].total_score, sides["blue"].total_score
        errors = []
        for color, side in (("red", sides["red"]), ("blue", sides["blue"])):
            if shown.get(f"{color}_points") is not None and side.score_without_fouls is not None:
                errors.append(abs(float(side.score_without_fouls)-float(shown[f"{color}_points"])))
        week = datetime.fromtimestamp(match.actual_time, timezone.utc)
        rows.append({"p": float(p), "y": .5 if red == blue else float(red > blue), "points_errors": errors,
                     "week": (week-timedelta(days=week.weekday())).date().isoformat(), "playoff": (match.comp_level or "qm") != "qm"})
    calibration = []
    for lo, hi in zip(BINS, BINS[1:]):
        chosen = [r for r in rows if r["y"] != .5 and lo <= max(r["p"], 1-r["p"]) < hi]
        if chosen:
            calibration.append({"favourite_from": lo, "favourite_to": min(hi, 1.), "matches": len(chosen),
                                "predicted": round(sum(max(r["p"], 1-r["p"]) for r in chosen)/len(chosen), 4),
                                "won": round(sum((r["p"] >= .5) == (r["y"] == 1.) for r in chosen)/len(chosen), 4)})
    weeks = sorted({r["week"] for r in rows})
    return {"season": season, "available": bool(rows), "overall": _summary(rows),
            "qualifications": _summary([r for r in rows if not r["playoff"]]),
            "playoffs": _summary([r for r in rows if r["playoff"]]),
            "calibration": calibration, "by_week": [{"week": w, **_summary([r for r in rows if r["week"] == w])} for w in weeks],
            "source": "forecasts shown before each match, locked when it started; never backfilled or re-tuned"}
