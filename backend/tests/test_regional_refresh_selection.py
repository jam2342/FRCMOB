from __future__ import annotations

from datetime import date
from unittest import mock

from app.core.config import settings
from app.services.events import regional_automation as ra

TODAY = date(2026, 9, 29)


def _events(*specs):
    return [{"key": key, "end_date": end} for key, end in specs]


def _select(events, refreshed_at, *, cap=2, settle=3):
    with (
        mock.patch.object(ra, "_event_refreshed_at", return_value=refreshed_at),
        mock.patch.object(ra.redis, "from_url"),
        mock.patch.object(settings, "automation_regional_max_rechecks_per_tick", cap),
        mock.patch.object(settings, "automation_regional_settle_days", settle),
    ):
        return ra._events_due_for_refresh(events, TODAY)


def test_new_and_recently_ended_events_always_refresh():
    events = _events(("2026new", "2026-04-01"), ("2026recent", "2026-09-27"))
    selected, skipped = _select(events, {"2026recent": 1.0})
    assert [e["key"] for e in selected] == ["2026new", "2026recent"]
    assert skipped == 0


def test_settled_events_get_a_capped_recheck_longest_unchecked_first():
    events = _events(("2026a", "2026-03-01"), ("2026b", "2026-03-08"), ("2026c", "2026-03-15"))
    selected, skipped = _select(events, {"2026a": 300.0, "2026b": 100.0, "2026c": 200.0}, cap=2)
    assert [e["key"] for e in selected] == ["2026b", "2026c"]
    assert skipped == 1


def test_missing_refresh_state_falls_back_to_everything():
    events = _events(("2026a", "2026-03-01"))
    with (
        mock.patch.object(ra, "_event_refreshed_at", side_effect=ra.redis.RedisError("down")),
        mock.patch.object(ra.redis, "from_url"),
    ):
        selected, skipped = ra._events_due_for_refresh(events, TODAY)
    assert selected == events and skipped == 0


def test_tick_ingests_all_then_builds_season_synergy_once_then_each_event():
    events = [
        {"key": "2026aaa", "end_date": "2026-03-01", "start_date": "2026-02-27"},
        {"key": "2026bbb", "end_date": "2026-03-08", "start_date": "2026-03-06"},
    ]
    order: list[str] = []
    tba = mock.MagicMock()
    tba.events.return_value = events

    def refresh(_db, *, event_key, run_post_compute, **_kwargs):
        order.append(f"ingest:{event_key}")
        assert run_post_compute is False
        return {"event_key": event_key, "status": "processed" if event_key == "2026aaa" else "ingest_failed"}

    with (
        mock.patch.object(ra, "TBAClient", return_value=tba),
        mock.patch.object(ra, "refresh_event", side_effect=refresh),
        mock.patch.object(ra, "precompute_season_synergy", side_effect=lambda _db, season, **_k: order.append(f"season:{season}") or {"ok": True}),
        mock.patch.object(ra, "post_compute_event", side_effect=lambda _db, *, event_key, season_ready, **_k: order.append(f"rebuild:{event_key}:{season_ready}") or {}),
        mock.patch.object(ra, "_mark_event_refreshed", side_effect=lambda key: order.append(f"mark:{key}")),
        mock.patch.object(ra, "train_shadow_models_after_refresh", return_value={"triggered": False}),
    ):
        ra.run_regional_post_event_breakdowns(
            season=2026, db=mock.MagicMock(), include_all_events=True, refresh_all=True,
            synergy_model_version="v", quality_threshold=0.7,
        )
    assert order == ["ingest:2026aaa", "ingest:2026bbb", "season:2026", "rebuild:2026aaa:True", "mark:2026aaa"]
