import { describe, expect, it } from 'vitest';
import { contrastRatio, readableInk } from './readableInk';

/* The ten track colours VideoReplayer cycles through. Every label drawn on one
   of these used to be white, which is the case this util exists to stop. */
const TRACK_COLORS = [
  '#ef4444', '#3b82f6', '#22c55e', '#eab308', '#a855f7',
  '#f97316', '#06b6d4', '#ec4899', '#84cc16', '#6366f1',
];

describe('readableInk', () => {
  it('beats white on every track colour it is asked about', () => {
    for (const fill of TRACK_COLORS) {
      const chosen = contrastRatio(fill, readableInk(fill));
      const white = contrastRatio(fill, '#ffffff');
      expect(chosen).not.toBeNull();
      expect(chosen!).toBeGreaterThanOrEqual(white!);
    }
  });

  it('picks dark ink on the light fills white was failing on', () => {
    // 1.92:1 and 1.98:1 against white respectively.
    expect(readableInk('#eab308')).toBe('#0f172a');
    expect(readableInk('#84cc16')).toBe('#0f172a');
  });

  it('picks white on a genuinely dark fill', () => {
    expect(readableInk('#1a2332')).toBe('#ffffff');
  });

  it('falls back to white rather than throwing on a colour it cannot parse', () => {
    expect(readableInk('var(--scout-color-1)')).toBe('#ffffff');
    expect(readableInk('rebeccapurple')).toBe('#ffffff');
    expect(contrastRatio('nonsense', '#fff')).toBeNull();
  });

  it('accepts a hex with or without the hash', () => {
    expect(readableInk('eab308')).toBe(readableInk('#eab308'));
  });
});
