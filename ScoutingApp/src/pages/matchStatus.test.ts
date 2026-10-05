import { describe, expect, it } from 'vitest';
import type { EventScheduleItem } from '../api';
import { liveTimerLabel, matchStartTime } from './centerUtils';
import { eventHasMatchesInPlay, inferMatchCompleted, matchHasScores } from './matchStatus';

function match(overrides: Partial<EventScheduleItem>): EventScheduleItem {
  return { match_key: '2026x_qm1', scheduled_time: 1000, red: [], blue: [], ...overrides } as EventScheduleItem;
}

describe('match status', () => {
  const longAfter = 1_000_000_000;

  it("treats TBA's -1 scores as no result, even once the scheduled time has passed", () => {
    const late = match({ red_score: -1, blue_score: -1 });
    expect(matchHasScores(late)).toBe(false);
    expect(inferMatchCompleted(late, longAfter)).toBe(false);
  });

  it('marks a scored match whose time has passed as completed', () => {
    const played = match({ red_score: 120, blue_score: 98 });
    expect(matchHasScores(played)).toBe(true);
    expect(inferMatchCompleted(played, longAfter)).toBe(true);
  });

  it('trusts an explicit winner and handles no match', () => {
    expect(inferMatchCompleted(match({ winner_alliance: 'blue' }), 0)).toBe(true);
    expect(inferMatchCompleted(null, longAfter)).toBe(false);
  });
});

describe('matchStartTime', () => {
  it('prefers the actual start, then the predicted one, then the schedule', () => {
    expect(matchStartTime({ scheduled_time: 100, predicted_time: 400, actual_time: 450 })).toBe(450);
    expect(matchStartTime({ scheduled_time: 100, predicted_time: 400, actual_time: null })).toBe(400);
    expect(matchStartTime({ scheduled_time: 100 })).toBe(100);
    expect(matchStartTime(null)).toBeNull();
  });

  it('keeps a late-running match upcoming instead of live', () => {
    const now = 1_000_000;
    const lateMatch = { scheduled_time: now / 1000 - 60, predicted_time: now / 1000 + 900 };
    expect(liveTimerLabel(matchStartTime(lateMatch), now).state).toBe('upcoming');
  });
});

describe('eventHasMatchesInPlay', () => {
  const now = 2_000_000 * 1000;
  const at = (offsetSec: number, extra: Partial<EventScheduleItem> = {}) =>
    match({ scheduled_time: 2_000_000 + offsetSec, ...extra });

  it('is true when an unplayed match is due soon or running late', () => {
    expect(eventHasMatchesInPlay([at(-600, { is_completed: true }), at(420)], now)).toBe(true);
    expect(eventHasMatchesInPlay([at(-2 * 3600)], now)).toBe(true);
  });

  it('is false for a finished event, one days away, or no schedule', () => {
    expect(eventHasMatchesInPlay([at(-600, { winner_alliance: 'red' })], now)).toBe(false);
    expect(eventHasMatchesInPlay([at(3 * 86400)], now)).toBe(false);
    expect(eventHasMatchesInPlay([at(-2 * 86400)], now)).toBe(false);
    expect(eventHasMatchesInPlay([], now)).toBe(false);
    expect(eventHasMatchesInPlay(null, now)).toBe(false);
  });
});
