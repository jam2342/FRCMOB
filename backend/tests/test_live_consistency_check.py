from __future__ import annotations

from scripts import live_consistency_check as lc

NOW = 1_792_360_000


def _tba(key, red=(1, 2, 3), blue=(4, 5, 6), score=None, t=NOW - 600):
    return {
        "key": key,
        "alliances": {
            "red": {"team_keys": [f"frc{n}" for n in red], "score": score[0] if score else -1},
            "blue": {"team_keys": [f"frc{n}" for n in blue], "score": score[1] if score else -1},
        },
        "actual_time": t if score else None,
        "predicted_time": t,
    }


def _prod(key, red=(1, 2, 3), blue=(4, 5, 6), score=None, comp="qm"):
    return {
        "match_key": key, "comp_level": comp, "is_completed": bool(score),
        "red_score": score[0] if score else None, "blue_score": score[1] if score else None,
        "red": [{"team_key": f"frc{n}"} for n in red], "blue": [{"team_key": f"frc{n}"} for n in blue],
    }


def _kinds(found):
    return sorted((f["kind"], f["match"]) for f in found)


def test_agreeing_event_has_no_findings():
    tba = [_tba("2026x_qm1", score=(10, 20)), _tba("2026x_qm2")]
    schedule = {"matches": [_prod("2026x_qm1", score=(10, 20)), _prod("2026x_qm2")]}
    predictions = {"matches": [{"match_key": "2026x_qm2", "prediction": {"available": True}}]}
    assert lc.compare_event("2026x", tba, schedule, predictions, {"Qualification": 1, "Playoff": 0}) == []


def test_finals_missing_from_our_schedule():
    # 2026ncfay1 on 2026-10-18: TBA listed the finals, our schedule had no rows for them.
    tba = [_tba("2026x_sf1m1", score=(5, 6)), _tba("2026x_f1m1", score=(9, 3))]
    schedule = {"matches": [_prod("2026x_sf1m1", score=(5, 6), comp="sf")]}
    assert ("missing_match", "2026x_f1m1") in _kinds(lc.compare_event("2026x", tba, schedule, None, None))


def test_results_lagging_wrong_or_with_other_teams():
    tba = [_tba("2026x_qm1", score=(10, 20)), _tba("2026x_qm2", score=(30, 40)), _tba("2026x_qm3", score=(1, 2))]
    schedule = {"matches": [
        _prod("2026x_qm1"),                              # we have no result yet
        _prod("2026x_qm2", score=(30, 41)),              # different score
        _prod("2026x_qm3", score=(1, 2), red=(1, 2, 9)),  # different robots
    ]}
    assert _kinds(lc.compare_event("2026x", tba, schedule, None, None)) == [
        ("lineup_mismatch", "2026x_qm3"), ("result_lag", "2026x_qm1"), ("score_mismatch", "2026x_qm2"),
    ]


def test_unplayed_match_without_prediction():
    tba = [_tba("2026x_qm2")]
    predictions = {"matches": [{"match_key": "2026x_qm2", "prediction": {"available": False}}]}
    assert _kinds(lc.compare_event("2026x", tba, None, predictions, None)) == [("missing_prediction", "2026x_qm2")]


def test_no_prediction_is_expected_before_tba_knows_the_alliances():
    tba = [_tba("2026x_f1m1", red=(), blue=()), _tba("2026x_f1m2", red=(0, 0, 0), blue=(4, 5, 6)), _tba("2026x_f1m3")]
    assert _kinds(lc.compare_event("2026x", tba, None, {"matches": []}, None)) == [("missing_prediction", "2026x_f1m3")]


def test_frc_events_ahead_counts_per_level():
    tba = [_tba("2026x_sf1m1")]
    schedule = {"matches": [_prod("2026x_sf1m1", comp="sf")]}
    found = lc.compare_event("2026x", tba, schedule, None, {"Qualification": 0, "Playoff": 1})
    assert _kinds(found) == [("frc_ahead", "Playoff")]


def test_problems_report_only_after_their_grace_and_resolve_once():
    state = {}
    obs = [{"event": "2026x", "kind": "result_lag", "match": "2026x_qm1", "detail": "d"}]
    assert lc.advance(state, obs, NOW) == ([], [])
    reported, _ = lc.advance(state, obs, NOW + lc.GRACE_SEC["result_lag"])
    assert [r["match"] for r in reported] == ["2026x_qm1"]
    assert lc.advance(state, obs, NOW + 500) == ([], [])  # reported once
    _, resolved = lc.advance(state, [], NOW + 600)
    assert [r["match"] for r in resolved] == ["2026x_qm1"] and state["open"] == {}


def test_short_blips_are_never_reported():
    state = {}
    obs = [{"event": "2026x", "kind": "missing_match", "match": "2026x_f1m1", "detail": "d"}]
    lc.advance(state, obs, NOW)
    assert lc.advance(state, [], NOW + 60) == ([], [])


def test_in_play_window():
    assert lc.event_in_play([_tba("2026x_qm1", t=NOW - 3600)], NOW)
    assert not lc.event_in_play([_tba("2026x_qm1", t=NOW - 5 * 3600)], NOW)
    assert not lc.event_in_play([_tba("2026x_qm1", t=NOW + 3 * 3600)], NOW)
