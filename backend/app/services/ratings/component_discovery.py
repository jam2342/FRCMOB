# Payload diagnostics propose inputs; they cannot declare a season or change a model.
from __future__ import annotations

from collections import Counter
from itertools import combinations
import re

import numpy as np

from app.services.ratings.component_spec import field, number, raw_field
from app.services.ratings.state_inputs import digest


def leaves(value, prefix=""):
    if isinstance(value,dict):
        for key, child in value.items():
            yield from leaves(child,f"{prefix}.{key}" if prefix else key)
    elif not isinstance(value,(list,dict)):
        yield prefix,value


def discover(payload):
    rows, quals = [], []
    skipped = Counter()
    for match in payload.get("matches",[]):
        alliances, breakdowns = match.get("alliances") or {}, match.get("score_breakdown") or {}
        teams = [str(t).lower() for c in ("red","blue") for t in alliances.get(c,{}).get("team_keys",[])]
        if len(teams) != 6 or len(set(teams)) != 6 or any(len(alliances.get(c,{}).get("team_keys",[])) != 3 for c in ("red","blue")):
            skipped["invalid_lineup"] += 1
            continue
        if any(number(alliances.get(c,{}).get("score")) is None or alliances[c]["score"] < 0 for c in ("red","blue")):
            skipped["unplayed"] += 1
            continue
        if any(alliances[c].get("dq_team_keys") for c in ("red","blue")):
            skipped["dq_match"] += 1
            continue
        for color in ("red","blue"):
            breakdown = breakdowns.get(color) or {}
            score, foul = number(alliances[color]["score"]), field(breakdown,"foulPoints")
            own = score-foul if foul is not None and 0 <= foul <= score else None
            row = (breakdown,own)
            rows.append(row)
            if match.get("comp_level") == "qm":
                quals.append(row)
    paths = sorted({path for breakdown,_ in rows for path,_ in leaves(breakdown)})
    numeric = {}
    for path in paths:
        values = [field(b,path) for b,_ in rows]
        known = [v for v in values if v is not None]
        if known:
            numeric[path] = {"observed":len(known),"missing":len(rows)-len(known),"min":min(known),"max":max(known)}
    candidates = [p for p in numeric if p not in ("totalPoints","foulPoints","adjustPoints")
                  and ("." not in p and p.endswith("Points") or p.endswith(".totalPoints")) and numeric[p]["max"] > 0]
    values = {p:np.array([np.nan if (v := field(b,p)) is None else v for b,_ in rows]) for p in candidates}
    own = np.array([np.nan if v is None else v for _,v in rows])
    has_adjust = any("adjustPoints" in b for b,_ in rows)
    adjust = np.array([np.nan if (v := field(b,"adjustPoints")) is None else v for b,_ in rows]) if has_adjust else np.zeros(len(rows))
    sums = []
    for size in range(1,min(3,len(candidates))+1):
        for sources in combinations(candidates,size):
            predicted = sum((values[p] for p in sources),start=adjust.copy())
            matched = np.isfinite(own) & np.isfinite(predicted) & np.isclose(predicted,own,rtol=0,atol=1e-6)
            matches = matched.reshape(-1,2).all(axis=1)
            if len(matches) and matches.mean() >= .99:
                sums.append({"sources":[*sources,*(["adjustPoints"] if has_adjust else [])],
                             "matches":len(matches),"matched":int(matches.sum()),"fraction":float(matches.mean())})
    robots = {}
    for path in paths:
        match = re.fullmatch(r"(.*Robot)([123])",path)
        if match:
            robots.setdefault(match[1],{})[int(match[2])] = path
    robot_fields = []
    for group, members in sorted(robots.items()):
        if set(members) != {1,2,3}:
            continue
        sources = [members[i] for i in (1,2,3)]
        seen = [raw_field(b,p) for b,_ in rows for p in sources if raw_field(b,p) is not None]
        kind = "points" if seen and all(number(v) is not None for v in seen) else "category"
        robot_fields.append({"name":group.removesuffix("Robot"),"sources":sources,"kind":kind,
                             "values":sorted({str(v) for v in seen}),"requires_point_mapping":kind == "category"})
    flags = []
    for path in paths:
        likely_bonus = bool(re.search(r"(?:RankingPoint|Achieved)$",path))
        # Include other top-level booleans so a new flag spelling is not hidden.
        if "." in path and not likely_bonus:
            continue
        observed = [(b,raw_field(b,path)) for b,_ in quals if type(raw_field(b,path)) is bool]
        if not observed:
            continue
        thresholds = []
        for count_path in numeric:
            if not count_path.lower().endswith(("count","points","total")) or count_path in ("foulPoints","totalPoints","adjustPoints"):
                continue
            pairs = [(field(b,count_path),flag) for b,flag in observed]
            yes = [v for v,flag in pairs if v is not None and flag]
            no = [v for v,flag in pairs if v is not None and not flag]
            if len(yes)+len(no) == len(observed) and yes and no and max(no) < min(yes):
                thresholds.append({"source":count_path,"not_earned_max":max(no),"earned_min":min(yes)})
        flags.append({"flag":path,"likely_bonus_flag":likely_bonus,"observed":len(observed),"missing":len(quals)-len(observed),
                      "earned":sum(flag for _,flag in observed),"observed_separators":thresholds})
    return {"event_key":payload.get("event",{}).get("key"),"source_sha256":digest(payload),
            "proposal_only":True,"eligible_matches":len(rows)//2,"qual_alliances":len(quals),
            "skipped":dict(skipped),"numeric_fields":numeric,"score_sums":sums,"robot_fields":robot_fields,
            "bonus_flag_candidates":flags,"limitations":["No spec is activated or written",
            "Closure is measured on eligible matches, including missing fields as failures",
            "Sums search up to three point fields plus adjustPoints; not exhaustive",
            "Achievement flags may not award an RP; confirm their meaning",
            "Observed separators are event-sample intervals, not scoring rules or manual thresholds"]}
