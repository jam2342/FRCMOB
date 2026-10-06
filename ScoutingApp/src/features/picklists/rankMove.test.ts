import { describe, expect, it } from 'vitest';
import { slotIndexForRank } from './rankMove';

const tiers = (...t: string[]) => t.map((tier) => ({ tier }));

describe('slotIndexForRank', () => {
  it('finds the row that currently holds a rank', () => {
    expect(slotIndexForRank(tiers('first', 'first', 'second'), 2)).toBe(1);
  });

  it('skips do-not-pick rows when counting ranks', () => {
    expect(slotIndexForRank(tiers('first', 'dnp', 'second', 'second'), 2)).toBe(2);
    expect(slotIndexForRank(tiers('dnp', 'first'), 1)).toBe(1);
  });

  it('sends a rank past the end to the bottom', () => {
    expect(slotIndexForRank(tiers('first', 'second'), 9)).toBe(1);
    expect(slotIndexForRank([], 1)).toBe(0);
  });
});
