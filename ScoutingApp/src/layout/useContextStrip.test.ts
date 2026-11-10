import { describe, expect, it } from 'vitest';
import { formatContextMatch } from './useContextStrip';

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
