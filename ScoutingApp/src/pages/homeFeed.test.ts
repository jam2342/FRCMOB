import { describe, expect, it } from 'vitest';
import type { EventScheduleItem, EventSearchItem } from '../api';
import {
  pickDefaultMatchKey,
  nearestEventDayToken,
  nearestLoadedMatchDayMs,
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

describe('pickDefaultMatchKey', () => {
  const row = (match_key: string, scheduled_time: number | null, done = false) =>
    ({
      match_key, display_name: match_key, comp_level: 'qm', set_number: 1, match_number: 1,
      scheduled_time, has_time: scheduled_time !== null, is_completed: done, red: [], blue: [],
    }) as EventScheduleItem;
  const now = Date.UTC(2026, 3, 30, 18, 0, 0);
  const minutes = (m: number) => Math.floor(now / 1000) + m * 60;

  it('prefers the live match, then the next one, then the latest result', () => {
    expect(pickDefaultMatchKey([row('qm1', minutes(-300), true), row('qm40', minutes(-1)), row('qm41', minutes(6))], now)).toBe('qm40');
    expect(pickDefaultMatchKey([row('qm1', minutes(-300), true), row('qm41', minutes(6)), row('qm42', minutes(12))], now)).toBe('qm41');
    expect(pickDefaultMatchKey([row('qm1', minutes(-300), true), row('qm2', minutes(-290), true)], now)).toBe('qm2');
    expect(pickDefaultMatchKey([], now)).toBeNull();
  });
});

describe('nearestEventDayToken', () => {
  const events = [
    { event_key: '2026arc', start_date: '2026-04-29', end_date: '2026-05-02' },
    { event_key: '2026off', start_date: '2026-10-10', end_date: '2026-10-11' },
  ];

  it('points at the closest event day in either direction', () => {
    expect(nearestEventDayToken(events, '2026-09-28')).toBe('2026-10-10');
    expect(nearestEventDayToken(events, '2026-05-10')).toBe('2026-05-02');
  });

  it('prefers the future on a tie and ignores events without dates', () => {
    const tied = [
      { event_key: 'a', start_date: '2026-05-01', end_date: '2026-05-01' },
      { event_key: 'b', start_date: '2026-05-05', end_date: '2026-05-05' },
      { event_key: 'c', start_date: null, end_date: null },
    ];
    expect(nearestEventDayToken(tied, '2026-05-03')).toBe('2026-05-05');
    expect(nearestEventDayToken([], '2026-05-03')).toBeNull();
  });

  it('skips events already known to have no matches', () => {
    expect(nearestEventDayToken(events, '2026-09-28', new Set(['2026off']))).toBe('2026-05-02');
  });
});

describe('nearestLoadedMatchDayMs', () => {
  const day = (y: number, m: number, d: number, h = 12) => new Date(y, m - 1, d, h).getTime();

  it('finds the closest other day with a loaded match', () => {
    const matches = [
      { scheduled_time: day(2026, 4, 30) / 1000 },
      { scheduled_time: day(2026, 5, 2, 9) / 1000, actual_time: day(2026, 5, 2, 15) / 1000 },
    ];
    expect(nearestLoadedMatchDayMs(matches, day(2026, 9, 28))).toBe(new Date(2026, 4, 2).getTime());
    expect(nearestLoadedMatchDayMs(matches, day(2026, 4, 30))).toBe(new Date(2026, 4, 2).getTime());
  });

  it('returns null when nothing has a time', () => {
    expect(nearestLoadedMatchDayMs([{ scheduled_time: null }], day(2026, 9, 28))).toBeNull();
  });
});
