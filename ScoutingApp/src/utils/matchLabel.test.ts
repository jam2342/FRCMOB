import { describe, expect, it } from 'vitest';
import { matchLabel, matchLabelFromKey } from './matchLabel';

describe('match labels', () => {
  it('writes every level the way the schedule does', () => {
    expect(matchLabelFromKey('2026arc_qm12')).toBe('QM 12');
    expect(matchLabelFromKey('2026arc_sf3m1')).toBe('SF 3-1');
    expect(matchLabelFromKey('2026cmptx_f1m2')).toBe('F 1-2');
    expect(matchLabel('qf', 2, 1)).toBe('QF 2-1');
  });

  it('has no label for something that is not a match key', () => {
    expect(matchLabelFromKey('2026arc')).toBeNull();
    expect(matchLabelFromKey('')).toBeNull();
  });
});
