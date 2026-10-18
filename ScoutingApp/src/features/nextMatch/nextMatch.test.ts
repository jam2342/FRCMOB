import { describe, expect, it } from 'vitest';
import type { EventScheduleItem } from '../../api';
import { countdownLabel, findNextMatch, shortMatchLabel } from './nextMatch';

const NOW = 1_800_000_000_000;
const slot = (n: number, station: string) => ({ team_key: `frc${n}`, team_number: n, nickname: null, station });
const match = (n: number, startOffsetMin: number, red: number[], blue: number[], extra: Partial<EventScheduleItem> = {}): EventScheduleItem => ({
  match_key: `2026test_qm${n}`, display_name: `Qual ${n}`, comp_level: 'qm', set_number: 1, match_number: n,
  scheduled_time: (NOW + startOffsetMin * 60_000) / 1000, has_time: true,
  red: red.map((t, i) => slot(t, `r${i + 1}`)), blue: blue.map((t, i) => slot(t, `b${i + 1}`)), ...extra,
});

describe('findNextMatch', () => {
  it('picks the soonest unplayed match the team is in, within the hour', () => {
    const next = findNextMatch([
      match(1, -30, [1678, 2, 3], [4, 5, 6], { is_completed: true }),
      match(5, 40, [7, 8, 9], [10, 1678, 11]),
      match(3, 25, [12, 1678, 13], [14, 15, 16]),
      match(8, 90, [1678, 17, 18], [19, 20, 21]),
    ], 'frc1678', NOW);
    expect(next?.match.match_key).toBe('2026test_qm3');
    expect(next?.stationLabel).toBe('Red 2');
    expect(next?.partners).toEqual([12, 13]);
    expect(next?.opponents).toEqual([14, 15, 16]);
    expect(next?.state).toBe('later');
  });

  it('says soon inside ten minutes and on the field once started, then lets go', () => {
    expect(findNextMatch([match(1, 6, [1], [1678])], 'frc1678', NOW)?.state).toBe('soon');
    expect(findNextMatch([match(1, -2, [1], [1678])], 'frc1678', NOW)?.state).toBe('now');
    expect(findNextMatch([match(1, -6, [1], [1678])], 'frc1678', NOW)).toBeNull();
  });

  it('uses the predicted time when the field runs late, and needs a team', () => {
    const late = match(1, 70, [1678], [2], { predicted_time: (NOW + 30 * 60_000) / 1000 });
    expect(findNextMatch([late], 'frc1678', NOW)?.state).toBe('later');
    expect(findNextMatch([late], '', NOW)).toBeNull();
  });
});

describe('labels', () => {
  it('counts down in minutes far out and in m:ss close in', () => {
    expect(countdownLabel(NOW + 24 * 60_000, NOW)).toBe('in 24 min');
    expect(countdownLabel(NOW + 6 * 60_000 + 42_000, NOW)).toBe('6:42');
    expect(countdownLabel(NOW - 1000, NOW)).toBe('On the field');
  });

  it('shortens qualification names only', () => {
    expect(shortMatchLabel(match(41, 0, [], []))).toBe('Q41');
    expect(shortMatchLabel({ ...match(1, 0, [], []), comp_level: 'sf', display_name: 'SF 3-1' })).toBe('SF 3-1');
  });
});
