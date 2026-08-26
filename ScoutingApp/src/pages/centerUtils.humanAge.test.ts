import { describe, expect, it } from 'vitest';
import { humanAge } from './centerUtils';

describe('humanAge', () => {
  it('never shows a decimal place on a day count', () => {
    // The bug this replaces: "Stale (151.7d old)".
    for (const days of [0.4, 1.2, 3.6, 9.9, 45.5, 151.7, 400.2]) {
      expect(humanAge(days)).not.toMatch(/\d\.\d/);
    }
  });

  it('picks the unit a person would say', () => {
    expect(humanAge(0.5)).toBe('today');
    expect(humanAge(1.4)).toBe('yesterday');
    expect(humanAge(9)).toBe('9 days ago');
    expect(humanAge(21)).toBe('3 weeks ago');
    expect(humanAge(151.7)).toBe('5 months ago');
  });

  it('does not say "12 months ago"', () => {
    expect(humanAge(400)).toBe('over a year ago');
    expect(humanAge(900)).toBe('2 years ago');
  });
});
