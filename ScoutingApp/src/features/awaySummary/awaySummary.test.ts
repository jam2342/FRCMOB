import { describe, expect, it } from 'vitest';
import type { EventScheduleItem } from '../../api';
import { awayDuration, summarizeAway } from './awaySummary';

const NOW = 1_800_000_000_000;
const slot = (n: number) => ({ team_key: `frc${n}`, team_number: n, nickname: null, station: null });
const match = (n: number, startMinAgo: number, red: number[], blue: number[], score?: [number, number]): EventScheduleItem => ({
  match_key: `2026test_qm${n}`, display_name: `Qual ${n}`, comp_level: 'qm', set_number: 1, match_number: n,
  scheduled_time: (NOW - startMinAgo * 60_000) / 1000, has_time: true,
  red: red.map(slot), blue: blue.map(slot),
  red_score: score ? score[0] : null, blue_score: score ? score[1] : null, is_completed: Boolean(score),
});

describe('summarizeAway', () => {
  const schedule = [
    match(10, 120, [1, 2, 3], [4, 5, 6], [100, 90]),
    match(11, 35, [1678, 2, 3], [4, 5, 6], [131, 126]),
    match(12, 25, [7, 8, 9], [1, 2, 3], [88, 97]),
    match(13, -18, [1678, 8, 9], [1, 2, 3]),
  ];

  it('counts results since you left, says how your team did and when it plays next', () => {
    const summary = summarizeAway(schedule, NOW - 42 * 60_000, NOW, 'frc1678');
    expect(summary?.headline).toBe('While you were away (42 min)');
    expect(summary?.newMatchKeys).toEqual(['2026test_qm11', '2026test_qm12']);
    expect(summary?.details).toEqual(['2 results', 'you won Q11 131–126', 'next: Q13 in 18 min']);
  });

  it('stays quiet after a short break or when nothing finished', () => {
    expect(summarizeAway(schedule, NOW - 5 * 60_000, NOW, '')).toBeNull();
    expect(summarizeAway(schedule, NOW - 15 * 60_000, NOW, '')).toBeNull();
    expect(summarizeAway(schedule, null, NOW, '')).toBeNull();
  });

  it('reads long breaks plainly', () => {
    expect(awayDuration(42 * 60_000)).toBe('42 min');
    expect(awayDuration(130 * 60_000)).toBe('2 h 10 min');
    expect(summarizeAway(schedule, NOW - 20 * 3600_000, NOW, '')?.headline).toBe('Since your last visit');
  });
});
