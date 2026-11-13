#!/usr/bin/env python3
# Integration rehearsal on a cached development prefix, in an in-memory scratch DB.
from __future__ import annotations

import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"

import argparse
import hashlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from app.db import models  # noqa: E402
from app.services.ratings import state_fitting as state  # noqa: E402
from app.services.ratings.calibrations import monday  # noqa: E402
from app.services.ratings.research_data import load_cached_season  # noqa: E402
from app.services.ratings.research_rehearsal import write_settings  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season",required=True,type=int,choices=(2023,2024))
    parser.add_argument("--cache",required=True,type=Path)
    parser.add_argument("--bootstrap",required=True,type=Path)
    parser.add_argument("--matches",type=int,default=300)
    parser.add_argument("--max-evals",type=int,default=400)
    parser.add_argument("--out",required=True,type=Path)
    args = parser.parse_args()
    if args.matches < 150 or args.matches > 2000 or not 1 <= args.max_evals <= 4000:
        parser.error("use 150–2000 development matches and 1–4000 optimizer evaluations per restart")
    started = time.perf_counter()
    previous = load_cached_season(args.cache,args.season-1)
    current = load_cached_season(args.cache,args.season)
    chosen = current.season.results[:args.matches]
    if not chosen:
        raise SystemExit("no development results")
    cutoff = monday(datetime.fromtimestamp(chosen[-1].card.time,timezone.utc))+timedelta(days=7)
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as db:
        rookies = {**previous.rookie_year,**current.rookie_year}
        for key in sorted({t for r in [*previous.season.results,*chosen] for t in r.card.red+r.card.blue}):
            db.add(models.Team(team_key=key,team_number=int(key[3:].rstrip("abcdefghijklmnopqrstuvwxyz")),rookie_year=rookies.get(key)))
        events = set()
        for season,results in ((args.season-1,previous.season.results),(args.season,chosen)):
            for result in results:
                card = result.card
                if card.event_key not in events:
                    db.add(models.Event(event_key=card.event_key,name=card.event_key,year=season))
                    events.add(card.event_key)
                db.add(models.Match(match_key=card.match_key,event_key=card.event_key,comp_level=card.comp_level,
                    set_number=1,match_number=1,time=card.time,actual_time=card.time))
                for color,teams,score,points in (("red",card.red,result.red_score,result.red_points),("blue",card.blue,result.blue_score,result.blue_points)):
                    db.add(models.MatchAllianceResult(match_key=card.match_key,event_key=card.event_key,season_year=season,
                        alliance=color,team_keys=list(teams),total_score=score,score_without_fouls=points))
        db.commit()
        bootstrap = state.install_bootstrap(db,json.loads(args.bootstrap.read_text()),"development-rehearsal")
        result = state.fit_week(db,args.season,cutoff,"development-rehearsal",max_evals=args.max_evals)
        db.commit()
        row = db.get(models.RatingCalibration,result["id"])
        report = {"code_sha256": {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),*sorted((Path(__file__).resolve().parents[1]/"app/services/ratings").glob("state_*.py"))]},
            "evidence":"development prefix; not the full rehearsal or a clean accuracy test", "season":args.season,
            "cutoff":cutoff.isoformat(), "previous_matches":bootstrap.matches,"current_matches":row.matches,
            "result":result,"params":row.params,"fit_metrics":row.fit_metrics,"seconds":round(time.perf_counter()-started,3)}
        path = write_settings(args.out,report)
        saved = json.loads(path.read_text())
        assert saved["result"]["id"] == row.id and not saved["fit_metrics"]["serving_eligible"]
        print(json.dumps({"report":str(path),"status":result["status"],"seconds":saved["seconds"],"fit_metrics":saved["fit_metrics"]},indent=2))


if __name__ == "__main__":
    main()
