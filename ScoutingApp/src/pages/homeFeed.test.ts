import { describe, expect, it } from 'vitest';
import type { EventScheduleItem, EventSearchItem } from '../api';
import {
  buildHomeFilterCounts,
  filterHomeWindowMatches,
  pickMobileHomeAutoEventKey,
  resolveHomeMatchState,
  selectHomeDayMatches,
  selectHomeWindowMatches,
} from './homeFeed';

function makeMatch(
  key: string,
  scheduledTime: number | null,
  overrides: Partial<EventScheduleItem> = {},
): EventScheduleItem {
  return {
    match_key: key,
    display_name: key.toUpperCase(),
    comp_level: 'qm',
    set_number: 1,
    match_number: 1,
    scheduled_time: scheduledTime,
    has_time: scheduledTime !== null,
    red: [],
    blue: [],
    ...overrides,
  };
}

function makeEvent(
  eventKey: string,
  overrides: Partial<EventSearchItem> = {},
): EventSearchItem {
  return {
    event_key: eventKey,
    name: eventKey.toUpperCase(),
    year: 2026,
    ...overrides,
  };
}

describe('homeFeed', () => {
  it('treats scored matches as ended even if timer state is not ended yet', () => {
    const nowMs = Date.UTC(2026, 1, 21, 18, 0, 0);
    const match = makeMatch('2026week0_qm1', Math.floor(nowMs / 1000) + 180, {
      red_score: 80,
      blue_score: 70,
    });
    expect(resolveHomeMatchState(match, nowMs)).toBe('ended');
  });

  it('selects rolling today window before hard local-date fallback', () => {
    const nowMs = Date.UTC(2026, 1, 21, 3, 0, 0);
    const nowSec = Math.floor(nowMs / 1000);
    const matches = [
      makeMatch('old_qm1', nowSec - (20 * 3600)),
      makeMatch('rolling_qm2', nowSec - (6 * 3600)),
      makeMatch('rolling_qm3', nowSec + (2 * 3600)),
      makeMatch('future_qm4', nowSec + (30 * 3600)),
    ];
    const selected = selectHomeWindowMatches(matches, nowMs);
    expect(selected.map((row) => row.match_key)).toEqual(['rolling_qm2', 'rolling_qm3']);
  });

  it('filters live/upcoming/final and computes counts consistently', () => {
    const nowMs = Date.UTC(2026, 1, 21, 18, 0, 0);
    const nowSec = Math.floor(nowMs / 1000);

    const live = makeMatch('m_live', nowSec - 10);
    const upcoming = makeMatch('m_upcoming', nowSec + 120);
    const completed = makeMatch('m_final', nowSec + 240, { is_completed: true, red_score: 10, blue_score: 5 });

    const scheduleByEvent = {
      '2026week0': {
        matches: [live, upcoming, completed],
      },
    };
    const counts = buildHomeFilterCounts(['2026week0'], scheduleByEvent, nowMs);
    expect(counts).toEqual({
      all: 3,
      live: 1,
      upcoming: 1,
      completed: 1,
    });

    const window = selectHomeWindowMatches(scheduleByEvent['2026week0'].matches || [], nowMs);
    expect(filterHomeWindowMatches(window, 'live', nowMs).map((row) => row.match_key)).toEqual(['m_live']);
    expect(filterHomeWindowMatches(window, 'upcoming', nowMs).map((row) => row.match_key)).toEqual(['m_upcoming']);
    expect(filterHomeWindowMatches(window, 'completed', nowMs).map((row) => row.match_key)).toEqual(['m_final']);
  });

  it('supports exact selected-day filtering when a day override is provided', () => {
    const day = new Date(2026, 2, 8);
    day.setHours(0, 0, 0, 0);
    const dayMs = day.getTime();
    const nowMs = dayMs + (13 * 60 * 60 * 1000);
    const dayMidSec = Math.floor((dayMs + (12 * 60 * 60 * 1000)) / 1000);
    const prevDaySec = Math.floor((dayMs - (4 * 60 * 60 * 1000)) / 1000);

    const scheduleByEvent = {
      week0: {
        matches: [
          makeMatch('prev_day', prevDaySec),
          makeMatch('selected_day', dayMidSec),
        ],
      },
    };

    const selectedDayMatches = selectHomeDayMatches(scheduleByEvent.week0.matches || [], dayMs);
    expect(selectedDayMatches.map((row) => row.match_key)).toEqual(['selected_day']);

    const counts = buildHomeFilterCounts(['week0'], scheduleByEvent, nowMs, { dayMs });
    expect(counts.all).toBe(1);
  });

  it('prefers events with live matches for mobile auto-open', () => {
    const nowMs = Date.UTC(2026, 2, 20, 18, 0, 0);
    const nowSec = Math.floor(nowMs / 1000);
    const events = [
      makeEvent('2026dateonly', {
        start_date: '2026-03-20',
        end_date: '2026-03-22',
      }),
      makeEvent('2026live', {
        start_date: '2026-03-20',
        end_date: '2026-03-22',
      }),
    ];

    const selected = pickMobileHomeAutoEventKey(
      events,
      {
        '2026live': {
          matches: [
            makeMatch('2026live_qm1', nowSec - 15),
            makeMatch('2026live_qm2', nowSec + 600),
          ],
        },
      },
      nowMs,
    );

    expect(selected).toBe('2026live');
  });

  it('falls back to date-active events when schedules are not loaded yet', () => {
    const nowMs = Date.UTC(2026, 2, 20, 18, 0, 0);
    const events = [
      makeEvent('2026cur', {
        start_date: '2026-03-20',
        end_date: '2026-03-22',
      }),
      makeEvent('2026later', {
        start_date: '2026-03-25',
        end_date: '2026-03-27',
      }),
    ];

    expect(pickMobileHomeAutoEventKey(events, {}, nowMs)).toBe('2026cur');
  });
});
