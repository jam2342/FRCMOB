import { describe, expect, it } from 'vitest';
import { buildContextSummary, formatContextMatch } from './useContextStrip';

describe('buildContextSummary', () => {
  it('names only what is selected', () => {
    expect(buildContextSummary('2026 ARC', '', 'Team 254')).toBe('2026 ARC · Team 254');
    expect(buildContextSummary('2026 ARC', 'QM 12', 'Team 254')).toBe('2026 ARC · QM 12 · Team 254');
  });

  it('says so when nothing is selected', () => {
    expect(buildContextSummary('', '', '')).toBe('Nothing selected yet');
  });
});

describe('formatContextMatch', () => {
  it('writes the match the way the schedule does, not as a key', () => {
    expect(formatContextMatch('2026arc_qm1', '2026arc')).toBe('QM 1');
    expect(formatContextMatch('2026arc_sf3m1', '2026arc')).toBe('SF 3-1');
    expect(formatContextMatch('', '2026arc')).toBe('No match');
  });

  it("names the other event when the match isn't from the strip's event", () => {
    expect(formatContextMatch('2026casac_qm1', '2026arc')).toBe('QM 1 · CASAC');
  });
});
