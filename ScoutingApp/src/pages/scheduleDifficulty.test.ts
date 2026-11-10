import { describe, expect, it } from 'vitest';
import { opponentRankLabel, scheduleDifficulty } from './scheduleDifficulty';

const event = { opponentRatingAvg: 90.6, eventRatingMean: 77.8, eventRatingSd: 16.9 };

describe('schedule difficulty', () => {
  it('ranks opponents against the whole season, so Einstein reads near the top', () => {
    // 2026cmptx SF 12-1: 254, 6800 and 8613, expected 716 points, 99.7th percentile.
    const einstein = scheduleDifficulty({ ...event, opponentStrengthPct: 99.7 });
    expect(einstein.score).toBeCloseTo(9.97, 2);
    expect(einstein).toMatchObject({ tier: 'very-hard', basis: 'season' });
  });

  it('spreads an ordinary event across the scale', () => {
    expect(scheduleDifficulty({ ...event, opponentStrengthPct: 78.5 }).score).toBeCloseTo(8.07, 2);
    expect(scheduleDifficulty({ ...event, opponentStrengthPct: 50 }).tier).toBe('medium');
    expect(scheduleDifficulty({ ...event, opponentStrengthPct: 0.1 })).toMatchObject({ tier: 'favorable' });
  });

  it('falls back to the event-relative rating score without season predictions', () => {
    const fallback = scheduleDifficulty({ ...event, opponentStrengthPct: null });
    expect(fallback.basis).toBe('event');
    // The old number for the same match.
    expect(fallback.score).toBeCloseTo(6.33, 2);
  });

  it('labels the rank the way people say it', () => {
    expect(opponentRankLabel(99.7)).toBe('Top 1% of alliances');
    expect(opponentRankLabel(97.2)).toBe('Top 3% of alliances');
    expect(opponentRankLabel(50)).toBe('Top 50% of alliances');
    expect(opponentRankLabel(12.4)).toBe('Bottom 13% of alliances');
    expect(opponentRankLabel(null)).toBeNull();
  });
});
