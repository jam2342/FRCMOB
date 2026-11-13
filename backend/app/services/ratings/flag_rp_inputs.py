from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from app.db import models
from app.services.ratings.component_spec import bonus_flags, number
from app.services.ratings.flag_rp import FlagRpModel
from app.services.ratings.replay import MatchCard
from app.services.ratings.state_inputs import digest, load_results


def database_inputs(db, spec, through):
    results, skipped = load_results(db,spec.season,through)
    sides = {(r.match_key,r.alliance):r for r in db.query(models.MatchAllianceResult).filter_by(season_year=spec.season)}
    rows = []
    for result in results:
        if result.card.comp_level != "qm":
            continue
        flags = {}
        for color in ("red","blue"):
            row = sides[(result.card.match_key,color)]
            flags[color] = bonus_flags(spec,{} if row.dq_team_keys else row.raw_breakdown)
        rows.append((result.card,flags))
    return rows,skipped


def cached_inputs(cache, spec):
    rows, skipped = [], {}
    def skip(reason):
        skipped[reason] = skipped.get(reason,0)+1
    folder = Path(cache)/str(spec.season)
    if not folder.is_dir():
        raise ValueError("missing cached season")
    for path in sorted(folder.glob("*.json")):
        payload = json.loads(path.read_text())
        event = payload["event"]["key"].lower()
        if not event.startswith(str(spec.season)):
            raise ValueError("cached event belongs to another season")
        for match in payload["matches"]:
            if match.get("comp_level") != "qm":
                skip("not_qualification")
                continue
            when = number(match.get("actual_time"))
            if when is None or when <= 0:
                skip("missing_actual_time")
                continue
            sides = match.get("alliances") or {}
            teams = {c:tuple(str(t).lower() for t in sides.get(c,{}).get("team_keys",[])) for c in ("red","blue")}
            if any(len(t) != 3 for t in teams.values()) or len(set(teams["red"]+teams["blue"])) != 6:
                skip("invalid_lineup")
                continue
            if any((v := number(sides.get(c,{}).get("score"))) is None or v < 0 for c in ("red","blue")):
                skip("unplayed")
                continue
            breakdowns = match.get("score_breakdown") or {}
            flags = {c:bonus_flags(spec,{} if sides[c].get("dq_team_keys") else breakdowns.get(c)) for c in ("red","blue")}
            card = MatchCard(match["key"].lower(),event,int(when),"qm",int(when),teams["red"],teams["blue"])
            rows.append((card,flags))
    rows.sort(key=lambda r:(r[0].time,r[0].match_key))
    if len({c.match_key for c,_ in rows}) != len(rows):
        raise ValueError("duplicate cached matches")
    return rows,skipped


def replay_flags(spec, rows):
    model = FlagRpModel(spec)
    for card, flags in rows:
        model.observe(card,flags)
    return model


def preview(db, spec, event_key, through):
    if not spec.bonus_rps:
        return None
    rows, skipped = database_inputs(db,spec,through)
    model = replay_flags(spec,rows)
    lineups = {}
    for team in db.query(models.MatchTeam).filter_by(event_key=event_key):
        lineups.setdefault(team.match_key,{}).setdefault(team.alliance,[]).append(team.team_key.lower())
    scored = {r.match_key for r in db.query(models.MatchAllianceResult).filter_by(event_key=event_key).filter(models.MatchAllianceResult.total_score >= 0)}
    forecasts = []
    for match in db.query(models.Match).filter_by(event_key=event_key,comp_level="qm",actual_time=None).order_by(models.Match.match_key):
        start = match.predicted_time or match.time
        teams = lineups.get(match.match_key,{})
        if not start or start <= through or match.match_key in scored or set(teams) != {"red","blue"}:
            continue
        if any(len(keys) != 3 for keys in teams.values()) or len(set(teams["red"]+teams["blue"])) != 6:
            continue
        card = MatchCard(match.match_key,event_key,start,"qm",start,tuple(teams["red"]),tuple(teams["blue"]))
        forecasts.append({"match_key":match.match_key,"scheduled_start":start,"teams":teams,"chances":model.forecast(card)})
    return {"recipe":"flag-rp-laplace-v1","provisional":True,"serving_eligible":False,
            "params":asdict(model.params),"settings_sha256":model.settings_sha256,
            "data_sha256":digest([(asdict(c),flags) for c,flags in rows]),"skipped":skipped,
            "observed_alliances":{name:state.observed for name,state in model.states.items()},"forecasts":forecasts,
            "limitations":["Unfitted development recipe; RP accuracy gate has not run",
                "Qualifying-match flags only; unknown flags and DQ sides are excluded",
                "Probabilities use the posterior mean logit; logit_sd reports latent uncertainty",
                "Current-state inspection is not prospective evidence"]}
