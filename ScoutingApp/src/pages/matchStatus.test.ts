import { describe, expect, it } from 'vitest';
import type { EventScheduleItem } from '../api';
import { liveTimerLabel, matchStartTime } from './centerUtils';
import { inferMatchCompleted, matchHasScores } from './matchStatus';

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
