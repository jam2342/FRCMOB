import { describe, expect, it } from 'vitest';
import { buildContextSummary } from './useContextStrip';

describe('buildContextSummary', () => {
  it('names only what is selected', () => {
    expect(buildContextSummary('2026 ARC', '', 'Team 254')).toBe('2026 ARC · Team 254');
    expect(buildContextSummary('2026 ARC', 'QM12', 'Team 254')).toBe('2026 ARC · QM12 · Team 254');
  });

  it('says so when nothing is selected', () => {
    expect(buildContextSummary('', '', '')).toBe('Nothing selected yet');
  });
});
