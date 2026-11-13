import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.ratings.component_discovery import discover
from app.services.ratings.component_spec import BonusRp, Component, ComponentSpec, bonus_flags, get_spec


def payload():
    matches = []
    for i in range(4):
        score = 100+10*i
        breakdown = {"autoPoints":20,"teleopPoints":score-25,"adjustPoints":0,"foulPoints":5,
                     "cargoCount":10*i,"cargoBonusRankingPoint":i >= 2,"g206Penalty":False,
                     "endGameRobot1":"None","endGameRobot2":"High","endGameRobot3":"Low"}
        matches.append({"key":f"2024test_qm{i+1}","comp_level":"qm","actual_time":1700000000+i,
            "alliances":{"red":{"score":score,"team_keys":["frc1","frc2","frc3"]},
                         "blue":{"score":score,"team_keys":["frc4","frc5","frc6"]}},
            "score_breakdown":{"red":copy.deepcopy(breakdown),"blue":copy.deepcopy(breakdown)}})
    return {"event":{"key":"2024test"},"matches":matches}


def test_discovery_sums_categories_and_observed_separators_are_proposals():
    raw = payload()
    before = copy.deepcopy(raw)
    out = discover(raw)
    assert raw == before and out["proposal_only"]
    assert {"sources":["autoPoints","teleopPoints","adjustPoints"],"matches":4,"matched":4,"fraction":1} in out["score_sums"]
    assert out["robot_fields"][0]["requires_point_mapping"]
    flag = next(r for r in out["bonus_flag_candidates"] if r["flag"] == "cargoBonusRankingPoint")
    assert {"source":"cargoCount","not_earned_max":10,"earned_min":20} in flag["observed_separators"]
    penalty = next(r for r in out["bonus_flag_candidates"] if r["flag"] == "g206Penalty")
    assert not penalty["likely_bonus_flag"]


def test_missing_adjust_does_not_make_a_split_pass_and_playoff_flags_are_excluded():
    raw = payload()
    raw["matches"][0]["score_breakdown"]["red"].pop("adjustPoints")
    raw["matches"][1]["comp_level"] = "sf"
    raw["matches"][2]["alliances"]["red"]["dq_team_keys"] = ["frc1"]
    raw["matches"][3]["alliances"]["red"]["score"] = -1
    out = discover(raw)
    assert not out["score_sums"] and out["qual_alliances"] == 2
    assert out["skipped"] == {"dq_match":1,"unplayed":1}
    assert discover({"matches":[]})["score_sums"] == []


def test_declared_flags_preserve_unknown_and_validate_names():
    spec = get_spec(2026)
    assert bonus_flags(spec,{"energizedAchieved":True,"superchargedAchieved":0}) == {"energized":True,"supercharged":None,"traversal":None}
    assert all(v is None for v in bonus_flags(spec,None).values())
    with pytest.raises(ValueError,match="unique"):
        replace(spec,bonus_rps=(BonusRp("a","flag"),BonusRp("b","flag")))
    with pytest.raises(ValueError,match="explicit"):
        replace(spec,bonus_rps=(BonusRp("a","nested..flag"),))
    with pytest.raises(ValueError,match="no declared"):
        get_spec(2027)
    nested = ComponentSpec(2027,"A",1,(Component("total",("score_without_fouls",)),),(BonusRp("a","nested.flag"),))
    assert bonus_flags(nested,{"nested":{"flag":False}}) == {"a":False}


def test_cli_uses_cache_only_and_does_not_write_spec(tmp_path):
    folder = tmp_path/"2024"
    folder.mkdir()
    source = folder/"2024test.json"
    source.write_text(json.dumps(payload()))
    script = Path(__file__).resolve().parents[1]/"scripts/discover_components.py"
    before = source.read_bytes()
    result = subprocess.run([sys.executable,str(script),"--event","2024test","--cache",str(tmp_path)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)["eligible_matches"] == 4
    assert source.read_bytes() == before and list(folder.iterdir()) == [source]
    rejected = subprocess.run([sys.executable,str(script),"--event","../../other","--cache",str(tmp_path)],capture_output=True,text=True)
    assert rejected.returncode != 0 and "invalid event key" in rejected.stderr


def test_flag_evaluation_uses_actual_times_and_rejects_clean_future_season(tmp_path):
    from scripts.evaluate_flag_rp import evaluate
    from app.services.ratings.flag_rp_inputs import cached_inputs
    raw = payload()
    raw["matches"][0]["actual_time"] = None
    # A plausible scheduled time must never replace a missing actual time.
    raw["matches"][0]["time"] = 1700000000
    raw["matches"][1]["comp_level"] = "sf"
    raw["matches"][2]["score_breakdown"]["red"]["melodyBonusAchieved"] = False
    raw["matches"][2]["score_breakdown"]["blue"]["melodyBonusAchieved"] = True
    raw["matches"][3]["score_breakdown"]["red"]["melodyBonusAchieved"] = True
    raw["matches"][3]["score_breakdown"]["blue"]["melodyBonusAchieved"] = False
    raw["matches"][3]["actual_time"] = raw["matches"][2]["actual_time"]
    folder = tmp_path/"2024"
    folder.mkdir()
    (folder/"2024test.json").write_text(json.dumps(raw))
    rows, skipped = cached_inputs(tmp_path,get_spec(2024))
    assert len(rows) == 2 and skipped == {"missing_actual_time":1,"not_qualification":1}
    out = evaluate(tmp_path,2024)
    assert out["by_rp"]["melody"]["all"]["n"] == 4 and not out["serving_eligible"]
    assert out["by_rp"]["melody"]["all"]["brier"] == .25
    assert out["by_rp"]["ensemble"]["all"] == {"n":0}
    with pytest.raises(ValueError,match="restricted"):
        evaluate(tmp_path,2027)
