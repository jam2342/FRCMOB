import copy
from dataclasses import asdict
import json

import numpy as np
import pytest

from app.services.ratings import rp_fitting as fit
from app.services.ratings.flag_rp import FlagRpParams
from app.services.ratings.state_inputs import digest
from scripts.fit_flag_rp import write_artifact
from scripts.evaluate_rp_fit import evaluate, load_frozen


def cache_season(root,year):
    folder = root/str(year)
    folder.mkdir(parents=True,exist_ok=True)
    names = {2022:"cargoBonusRankingPoint",2023:"activationBonusAchieved",2024:"melodyBonusAchieved",2025:"autoBonusAchieved"}
    matches = []
    for i in range(8):
        time = 100+i*20
        matches.append({"key":f"{year}test_qm{i+1}","comp_level":"qm","actual_time":time,"post_result_time":time+5,
            "alliances":{"red":{"score":100,"team_keys":["frc1","frc2","frc3"]},
                         "blue":{"score":100,"team_keys":["frc4","frc5","frc6"]}},
            "score_breakdown":{"red":{names[year]:i%3 != 0},"blue":{names[year]:i%3 == 0}}})
    payload = {"event":{"key":f"{year}test","event_type":0},"matches":matches}
    path = folder/f"{year}test.json"
    path.write_text(json.dumps(payload))
    return path,payload


def test_training_never_reads_validation_or_evaluation_files_and_is_deterministic(tmp_path):
    for year in (2022,2023):
        cache_season(tmp_path,year)
    artifact = fit.fit(tmp_path)
    for year in (2024,2025,2026,2027):
        folder = tmp_path/str(year)
        folder.mkdir()
        (folder/f"{year}bad.json").write_text("unreadable future payload")
    assert digest(fit.fit(tmp_path)) == digest(artifact)
    assert set(artifact["source_sha256"]) == {"2022","2023"}
    assert len(artifact["trials"]) <= 13
    assert artifact["policy"] is not fit.POLICY
    fit.validate_artifact(artifact)


def test_publication_time_gates_learning_and_missing_flags_are_not_failures(tmp_path):
    path,raw = cache_season(tmp_path,2024)
    raw["matches"][0]["post_result_time"] = 140
    raw["matches"][1]["post_result_time"] = 140
    raw["matches"][2]["post_result_time"] = None
    raw["matches"][3]["score_breakdown"]["red"]["melodyBonusAchieved"] = 0
    raw["matches"][4]["alliances"]["red"]["dq_team_keys"] = ["frc1"]
    raw["matches"][5]["actual_time"] = None
    raw["matches"][5]["time"] = 200
    path.write_text(json.dumps(raw))
    data = fit.load_published(tmp_path,2024)
    rows = fit.forecasts(data,FlagRpParams())
    by = {r["key"]:r for r in rows}
    assert by["2024test_qm1/red/melody"]["p"] == by["2024test_qm3/red/melody"]["p"] == .5
    assert "2024test_qm4/red/melody" not in by and "2024test_qm5/red/melody" not in by
    assert not any(r["key"].startswith("2024test_qm6/") for r in rows)
    assert data.skipped["missing_actual_time"] == 1
    assert data.skipped["missing_or_invalid_publication"] == 1


def test_source_season_and_match_identity_fail_closed(tmp_path):
    path,raw = cache_season(tmp_path,2022)
    raw["matches"][0]["key"] = "2026other_qm1"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match="another event"):
        fit.load_published(tmp_path,2022)


def test_calibration_identity_and_shrinkage_to_asof_rate():
    identity = {"intercept":0.,"slope":1.,"reference":"asof_base_logit"}
    for p in (.01,.1,.5,.9,.99):
        assert fit.calibrated(p,.2,identity) == pytest.approx(p)
    softer = {**identity,"slope":.5}
    assert .1 < fit.calibrated(.99,.1,softer) < .99
    assert .0001 < fit.calibrated(.0001,.01,softer) < .01


def test_bounded_calibration_recovers_known_overconfidence():
    rows = []
    for p,successes in ((.99,8),(.01,2)):
        rows.extend({"rp":"bonus","p":p,"base":.5,"y":i<successes} for i in range(10))
    c = fit.fit_calibration([rows])
    assert abs(c["intercept"]) < .01
    assert c["slope"] == pytest.approx(np.log(4)/np.log(99),abs=.03)
    probabilities = {str(i):fit.calibrated(r["p"],r["base"],c) for i,r in enumerate(rows)}
    prepared = [{**r,"key":str(i),"event":"event","time":100} for i,r in enumerate(rows)]
    assert fit.compare(prepared,prepared,calibrated_rows=probabilities)["pooled"]["log_loss"]["mean_diff"] < 0


def test_loss_weights_balance_seasons_and_rp_types():
    groups = [[{"rp":"a","p":.5,"y":True}]*8+[{"rp":"b","p":.5,"y":False}]*2,
              [{"rp":"c","p":.5,"y":True}]*3]
    weights = fit.weights(groups)
    assert weights.sum() == pytest.approx(1)
    assert weights[:8].sum() == pytest.approx(.25)
    assert weights[8:10].sum() == pytest.approx(.25)
    assert weights[10:].sum() == pytest.approx(.5)
    assert fit.objective(groups) == pytest.approx(np.log(2))


def test_artifacts_reject_future_training_invalid_coefficients_and_hash_changes(tmp_path):
    for year in (2022,2023):
        cache_season(tmp_path,year)
    artifact = fit.fit(tmp_path)
    altered = copy.deepcopy(artifact)
    altered["source_sha256"]["2026"] = "future"
    with pytest.raises(ValueError,match="only"):
        fit.validate_artifact(altered)
    altered = copy.deepcopy(artifact)
    altered["calibration"]["slope"] = float("nan")
    with pytest.raises(ValueError,match="bounds"):
        fit.validate_artifact(altered)
    altered = copy.deepcopy(artifact)
    altered["params"] = asdict(FlagRpParams(prior_var=5))
    with pytest.raises(ValueError,match="bounds"):
        fit.validate_artifact(altered)
    out = tmp_path/"fits"
    path = write_artifact(out,artifact)
    assert path == write_artifact(out,artifact)
    path.write_text("{}")
    with pytest.raises(ValueError,match="different contents"):
        write_artifact(out,artifact)


def test_failed_2024_validation_blocks_frozen_evaluation_before_loading_data(tmp_path):
    for year in (2022,2023):
        cache_season(tmp_path,year)
    artifact = fit.fit(tmp_path)
    out = tmp_path/"fits"
    write_artifact(out,artifact)
    validation = {"season":2024,"artifact_sha256":digest(artifact),"passed":False,"guards":{"log_loss":False}}
    write_artifact(out,validation)
    frozen = {"schema":"flag-rp-frozen-v1","artifact_sha256":digest(artifact),"validation_sha256":digest(validation),
              "validation_passed":False,"evaluation_seasons":[2025,2026],"serving_eligible":False}
    path = write_artifact(out,frozen)
    with pytest.raises(ValueError,match="validation failed"):
        load_frozen(path)
    frozen["validation_passed"] = True
    path = write_artifact(out,frozen)
    with pytest.raises(ValueError,match="validation failed"):
        load_frozen(path)


def test_comparisons_reject_unmatched_or_duplicate_labels():
    rows = [{"key":"one","event":"event","rp":"bonus","y":True,"p":.5}]
    with pytest.raises(ValueError,match="identical"):
        fit.compare(rows,[])
    with pytest.raises(ValueError,match="identical"):
        fit.compare(rows,rows*2)


def test_frozen_manifest_cannot_reference_an_arbitrary_path(tmp_path):
    frozen = {"schema":"flag-rp-frozen-v1","artifact_sha256":"../future-data",
        "validation_sha256":"a"*64,"validation_passed":True,"evaluation_seasons":[2025,2026],"serving_eligible":False}
    path = write_artifact(tmp_path,frozen)
    with pytest.raises(ValueError,match="hash reference"):
        load_frozen(path)


def test_successful_frozen_recipe_evaluates_without_refitting(tmp_path):
    for year in (2022,2023,2024,2025):
        cache_season(tmp_path,year)
    artifact = fit.fit(tmp_path)
    # An identity candidate should tie the default exactly on separate synthetic data.
    artifact["params"] = asdict(FlagRpParams())
    artifact["calibration"] = {"intercept":0.,"slope":1.,"reference":"asof_base_logit"}
    validation = fit.validate_on_2024(tmp_path,artifact)
    assert validation["passed"]
    out = tmp_path/"fits"
    write_artifact(out,artifact)
    write_artifact(out,validation)
    frozen = {"schema":"flag-rp-frozen-v1","artifact_sha256":digest(artifact),"validation_sha256":digest(validation),
              "validation_passed":True,"evaluation_seasons":[2025,2026],"serving_eligible":False}
    path = write_artifact(out,frozen)
    before = path.read_bytes()
    result = evaluate(tmp_path,2025,path)
    assert result["comparisons"]["calibrated_minus_default"]["pooled"]["log_loss"]["mean_diff"] == pytest.approx(0,abs=1e-12)
    assert not result["serving_eligible"] and path.read_bytes() == before
    with pytest.raises(ValueError,match="frozen examined"):
        evaluate(tmp_path,2027,path)


def test_calibration_only_fixes_state_and_never_searches_variances(tmp_path,monkeypatch):
    for year in (2022,2023):
        cache_season(tmp_path,year)
    def forbidden(*args,**kwargs):
        raise AssertionError("calibration-only must not search state settings")
    monkeypatch.setattr(fit,"replace",forbidden)
    artifact = fit.fit(tmp_path,calibration_only=True)
    assert artifact["schema"] == fit.CALIBRATION_SCHEMA
    assert artifact["params"] == asdict(FlagRpParams())
    assert len(artifact["trials"]) == 1 and artifact["policy"]["coordinate_rounds"] == 0
    fit.validate_artifact(artifact)
    artifact["params"]["match_var"] *= 2
    with pytest.raises(ValueError,match="original state"):
        fit.validate_artifact(artifact)


def test_calibration_only_stays_independent_of_later_data_and_uses_same_screen(tmp_path):
    for year in (2022,2023,2024):
        cache_season(tmp_path,year)
    artifact = fit.fit(tmp_path,calibration_only=True)
    for year in (2025,2026,2027):
        folder = tmp_path/str(year)
        folder.mkdir()
        (folder/f"{year}unreadable.json").write_text("not training data")
    assert digest(fit.fit(tmp_path,calibration_only=True)) == digest(artifact)
    validation = fit.validate_on_2024(tmp_path,artifact)
    assert set(validation["guards"]) == {"pooled_log_loss","brier","log_loss"}
    for name in ("validation_brier_tolerance","validation_log_loss_tolerance"):
        assert artifact["policy"][name] == fit.POLICY[name]


def test_calibration_only_successful_freeze_evaluates_identity_fixture(tmp_path):
    for year in (2022,2023,2024,2025):
        cache_season(tmp_path,year)
    artifact = fit.fit(tmp_path,calibration_only=True)
    artifact["calibration"] = {"intercept":0.,"slope":1.,"reference":"asof_base_logit"}
    validation = fit.validate_on_2024(tmp_path,artifact)
    assert validation["passed"]
    out = tmp_path/"fits"
    write_artifact(out,artifact)
    write_artifact(out,validation)
    frozen = {"schema":"flag-rp-frozen-v1","artifact_sha256":digest(artifact),"validation_sha256":digest(validation),
              "validation_passed":True,"evaluation_seasons":[2025,2026],"serving_eligible":False}
    result = evaluate(tmp_path,2025,write_artifact(out,frozen))
    assert result["comparisons"]["raw_fitted_minus_default"]["pooled"]["brier"]["mean_diff"] == 0
    assert not result["serving_eligible"]
