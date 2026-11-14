import copy
from dataclasses import replace
import json

import pytest

from app.services.ratings import rp_comparison as comp
from app.services.ratings.replay import MatchCard, MatchResult, Prediction, Season
from app.services.ratings.research_data import ResearchSeason


def sample():
    results, metadata = [], {}
    for i,time in enumerate((100,100,200,300)):
        card = MatchCard(f"2026test_qm{i+1}","2026test",time,"qm",100,("frc1","frc2","frc3"),("frc4","frc5","frc6"))
        components = {"energized_rp":True,"hub_fuel_count":150-i*10}
        results.append(MatchResult(card,100,90,100,90,red_components=components,blue_components=components.copy()))
        metadata[card.match_key] = {"actual_time":time,"post_result_time":time+5,"valid_lineup":True,
            "flags":{color:{"energized":True,"supercharged":False,"traversal":False} for color in ("red","blue")}}
    return ResearchSeason(Season(2026,results,set(),{}),{"2026test":{"event_type":0}}),metadata


class Model:
    def __init__(self):
        self.seen = []

    def predict(self,card):
        # Counts make same-time result leakage visible in the predicted means.
        return Prediction(0,0,red_hub=(100+10*len(self.seen),100),blue_hub=(100,100),red_tower=(0,10),blue_tower=(0,10))

    def forecast(self,event,red,blue,event_start):
        return self.predict(None)

    def observe(self,result):
        self.seen.append(result.card.match_key)


def test_thresholds_and_model_history_exclude_same_time_and_later_results():
    research,meta = sample()
    rows,_ = comp.replay_comparison(research,meta,Model())
    a,b,c = [rows[f"2026test_qm{i}/red/energized"] for i in (1,2,3)]
    assert a["threshold"] == b["threshold"] == 100
    assert a["live"] == b["live"] and a["flag"] == b["flag"]
    assert c["threshold"] == 140
    changed = copy.deepcopy(research)
    changed.season.results[-1].red_components["hub_fuel_count"] = 1
    altered,_ = comp.replay_comparison(changed,meta,Model())
    assert rows == altered
    altered_meta = copy.deepcopy(meta)
    altered_meta["2026test_qm4"]["flags"]["red"]["energized"] = False
    altered,_ = comp.replay_comparison(research,altered_meta,Model())
    assert rows["2026test_qm3/red/energized"] == altered["2026test_qm3/red/energized"]


def test_common_labels_exclude_dq_unknown_unplayed_and_nonofficial():
    research,meta = sample()
    meta["2026test_qm1"]["flags"]["red"] = {rp:None for rp in comp.BONUS_RPS[2026]}
    meta["2026test_qm2"]["flags"]["blue"]["energized"] = None
    meta["2026test_qm3"]["actual_time"] = None
    meta["2026test_qm4"]["valid_lineup"] = False
    rows,coverage = comp.replay_comparison(research,meta,Model())
    assert len(rows) == 8
    assert coverage["history_matches"] == 2
    assert coverage["excluded_history"] == {"missing_actual_time":1,"invalid_lineup":1}
    research.events["2026test"]["event_type"] = 99
    assert not comp.replay_comparison(research,meta,Model())[0]


def test_playoffs_inform_live_state_but_are_not_rp_labels():
    research,meta = sample()
    first = research.season.results[0]
    research.season.results[0] = replace(first,card=replace(first.card,comp_level="sf"))
    model = Model()
    rows,_ = comp.replay_comparison(research,meta,model)
    assert len(rows) == 18 and len(model.seen) == 3
    assert not any(k.startswith(first.card.match_key+"/") for k in rows)


def test_reproduction_uses_retrospective_thresholds_separately():
    research,_ = sample()
    reproduction = comp.reproduce(research,Model())
    expected = comp.parts(Model().predict(research.season.results[0].card),{"energized":120,"supercharged":360,"traversal":50})
    assert reproduction["2026test_qm1"] == expected


def test_parity_requires_real_coverage_and_all_values_match(tmp_path):
    predictions = {"2026test_qm1":{"red":{"energized":.1234},"blue":{"energized":.9876}}}
    assert not comp.served_parity(predictions,tmp_path)["passed"]
    path = tmp_path/"served_2026test.json"
    path.write_text(json.dumps({"matches":[{"match_key":"2026test_qm1","prediction":{"bonus_rp_chances":{
        "red":{"energized":.123},"blue":{"energized":.988}}}}]}))
    assert comp.served_parity(predictions,tmp_path)["passed"]
    assert not comp.served_parity({},tmp_path)["passed"]
    predictions["2026test_qm1"]["red"]["energized"] = .13
    assert not comp.served_parity(predictions,tmp_path)["passed"]


def test_summary_is_paired_and_preserves_per_rp_failures():
    research,meta = sample()
    rows,_ = comp.replay_comparison(research,meta,Model())
    report = comp.summarize(rows)
    assert report["pooled"]["all"]["n"] == 24
    for rp in comp.BONUS_RPS[2026]:
        out = report[rp]["all"]
        assert out["n"] == 8
        difference = out["metrics"]["flag"]["brier"]-out["metrics"]["live"]["brier"]
        assert out["flag_minus"]["live"]["brier"]["mean_diff"] == pytest.approx(difference)


def test_private_prior_export_is_validated_and_not_replaced_by_fixture_defaults(tmp_path):
    path = tmp_path/"strengths.csv"
    path.write_text("BEGIN\nteam_key,z,n\nfrc1,0.2,10\nCOMMIT\n")
    assert comp.read_priors(path) == {"frc1":.2}
    path.write_text("frc1,0.2,10\nfrc1,0.3,20\n")
    with pytest.raises(ValueError,match="duplicate"):
        comp.read_priors(path)


def test_cached_loader_preserves_unknown_flags_and_fails_closed_without_actual_time(tmp_path):
    folder = tmp_path/"2026"
    folder.mkdir()
    raw = {"event":{"key":"2026test","event_type":0},"matches":[]}
    for i,actual in enumerate((100,None)):
        raw["matches"].append({"key":f"2026test_qm{i+1}","comp_level":"qm","actual_time":actual,"time":200,
            "alliances":{"red":{"score":100,"team_keys":["frc1","frc2","frc3"],"dq_team_keys":["frc1"]},
                         "blue":{"score":100,"team_keys":["frc4","frc5","frc6"]}},
            "score_breakdown":{"red":{"energizedAchieved":True},"blue":{"energizedAchieved":False,"superchargedAchieved":0}}})
    (folder/"2026test.json").write_text(json.dumps(raw))
    research,meta,sha = comp.load_inputs(tmp_path)
    assert len(sha) == 64
    results,skipped = comp.actual_results(research,meta)
    assert len(results) == 1 and skipped == {"missing_actual_time":1}
    assert meta["2026test_qm1"]["flags"]["red"]["energized"] is None
    assert meta["2026test_qm1"]["flags"]["blue"] == {"energized":False,"supercharged":None,"traversal":None}


def test_unpublished_and_same_time_posted_results_cannot_update_either_model():
    research,meta = sample()
    for key in ("2026test_qm1","2026test_qm2"):
        meta[key]["post_result_time"] = 200
    meta["2026test_qm3"]["post_result_time"] = None
    rows,coverage = comp.replay_comparison(research,meta,Model())
    early = rows["2026test_qm1/red/energized"]
    tied = rows["2026test_qm3/red/energized"]
    assert early["threshold"] == tied["threshold"] == 100
    assert early["flag"] == tied["flag"] and early["live"] == tied["live"]
    later = rows["2026test_qm4/red/energized"]
    assert later["threshold"] == 140
    assert coverage["missing_or_invalid_publication_time"] == 1


def test_comparison_calls_upcoming_forecast_without_entering_future_teams():
    research,meta = sample()
    class ForecastOnly(Model):
        def predict(self,card):
            raise AssertionError("played-match path is not a live upcoming prediction")

        def forecast(self,event,red,blue,event_start):
            return Model.predict(self,None)
    rows,_ = comp.replay_comparison(research,meta,ForecastOnly())
    assert len(rows) == 24
