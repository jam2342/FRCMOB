from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from itertools import groupby
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.services.ratings.component_spec import get_spec  # noqa: E402
from app.services.ratings.flag_rp import FlagRpModel, RECIPE  # noqa: E402
from app.services.ratings.flag_rp_inputs import cached_inputs  # noqa: E402
from app.services.ratings.research_eval import paired_cluster_bootstrap  # noqa: E402
from app.services.ratings.state_inputs import digest  # noqa: E402


def evaluate(cache, season):
    if season not in range(2022,2027):
        raise ValueError("development evaluation is restricted to examined 2022–2026 seasons")
    spec = get_spec(season)
    rows, skipped = cached_inputs(cache,spec)
    model = FlagRpModel(spec)
    losses = {name:{} for name in model.states}
    starts = {}
    for _, group in groupby(rows,key=lambda r:r[0].time):
        batch = list(group)
        # Simultaneous starts cannot supply one another's outcomes.
        for card, flags in batch:
            starts.setdefault(card.event_key,card.time)
            predictions = model.predict(card)
            for color in ("red","blue"):
                for name, forecast in predictions[color].items():
                    y = flags[color][name]
                    if type(y) is not bool:
                        continue
                    p = forecast["probability"]
                    q = min(1-1e-9,max(1e-9,p))
                    base = forecast["base_rate"]
                    losses[name][f"{card.match_key}/{color}"] = {"event":card.event_key,"brier":(p-y)**2,
                        "base_brier":(base-y)**2,"log_loss":-(y*math.log(q)+(1-y)*math.log(1-q))}
        for card, flags in batch:
            model.observe(card,flags)
    ordered = sorted(starts,key=lambda e:(starts[e],e))
    newest = set(ordered[-max(1,math.ceil(len(ordered)*.2)):])
    def summary(records):
        if not records:
            return {"n":0}
        return {"n":len(records),"brier":sum(r["brier"] for r in records.values())/len(records),
            "base_brier":sum(r["base_brier"] for r in records.values())/len(records),
            "log_loss":sum(r["log_loss"] for r in records.values())/len(records),
            "comparison":paired_cluster_bootstrap({k:(r["event"],r["brier"]) for k,r in records.items()},
                {k:(r["event"],r["base_brier"]) for k,r in records.items()},seed=7)}
    return {"season":season,"recipe":RECIPE,"params":asdict(model.params),"settings_sha256":model.settings_sha256,
            "data_sha256":digest([(asdict(c),flags) for c,flags in rows]),"skipped":skipped,
            "events":len(ordered),"newest_events":sorted(newest),"matches":len(rows),
            "by_rp":{name:{"all":summary(records),"newest_events":summary({k:r for k,r in records.items() if r["event"] in newest})}
                for name,records in losses.items()},"serving_eligible":False,
            "limitations":["Fixed, unfitted recipe; no parameter selection from this run",
                "Forecasts precede flags at the same or later actual start; baseline is the as-of Beta(1,1) season rate",
                "Examined seasons are development evidence, including newest events",
                "No comparison to production threshold RP model, tier gate or release rehearsal"]}


def main():
    parser = argparse.ArgumentParser(description="Walk-forward development check of flag-only bonus RP; no writes or network")
    parser.add_argument("--season",required=True,type=int,choices=range(2022,2027))
    parser.add_argument("--cache",required=True,type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.cache,args.season),indent=2,allow_nan=False))


if __name__ == "__main__":
    main()
