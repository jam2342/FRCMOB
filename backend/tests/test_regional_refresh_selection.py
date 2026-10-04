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
