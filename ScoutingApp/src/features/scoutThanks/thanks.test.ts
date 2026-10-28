import { describe, expect, it } from 'vitest';
import { scoutThanks } from './thanks';

const row = (scout_profile: string, entry_count: number, best_qual_streak = 0) => ({
  scout_profile, entry_count, best_qual_streak, matches_covered: entry_count, teams_covered: entry_count, last_entry_at: null,
});

describe('scoutThanks', () => {
  const board = [row('Ben', 9, 4), row('Ava', 14, 9), row('Cy', 9), row('Dee', 2)];

  it("tells a scout their share, and thanks them once they're done", () => {
    expect(scoutThanks(board, 'ava', false)).toEqual({ headline: 'Your scouting here', detail: '14 reports, 9 matches in a row. The most on your team.' });
    expect(scoutThanks(board, 'Ava', true)?.headline).toBe('Thank you for scouting!');
  });

  it('shares a place on a tie, and says nothing about a place past third', () => {
    expect(scoutThanks(board, 'Cy', false)?.detail).toBe('9 reports. 2nd most on your team.');
    expect(scoutThanks(board, 'Ben', false)?.detail).toBe('9 reports, 4 matches in a row. 2nd most on your team.');
    expect(scoutThanks([...board, row('Eve', 1)], 'Eve', false)?.detail).toBe('1 report.');
  });

  it('has nothing to say before a first report, or to a team of one about rank', () => {
    expect(scoutThanks(board, 'Zed', false)).toBeNull();
    expect(scoutThanks([row('Ava', 0)], 'Ava', false)).toBeNull();
    expect(scoutThanks([row('Ava', 3)], 'Ava', true)?.detail).toBe('3 reports.');
  });
});
