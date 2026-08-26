/* Which ink is readable on a given fill.

   Needed wherever a colour comes out of an array by index rather than out of a
   stylesheet — an overlay label on a per-robot track colour, say. A stylesheet
   can pair a fill with its ink by hand; a runtime lookup cannot, and the usual
   guess is white, which is wrong on exactly the colours people reach for. On
   the ten track colours in VideoReplayer, white measured 1.92:1 on the yellow
   and 1.98:1 on the lime — far under the 4.5:1 AA floor for small text.

   Prefer pairing fill and ink in CSS where the set is enumerable (see the
   scout identity classes). This is for when it genuinely is not. */

const DARK_INK = '#0f172a';
const LIGHT_INK = '#ffffff';

function channel(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex: string): number | null {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) return null;
  const int = parseInt(match[1], 16);
  const r = channel((int >> 16) & 0xff);
  const g = channel((int >> 8) & 0xff);
  const b = channel(int & 0xff);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number | null {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  if (la === null || lb === null) return null;
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** The higher-contrast of near-black and white against `fill`. */
export function readableInk(fill: string): string {
  const luminance = relativeLuminance(fill);
  // An unparseable fill (a gradient, a var(), a named colour) gets white, which
  // is what the call sites did before this existed.
  if (luminance === null) return LIGHT_INK;
  const onDark = (luminance + 0.05) / (relativeLuminance(DARK_INK)! + 0.05);
  const onLight = (relativeLuminance(LIGHT_INK)! + 0.05) / (luminance + 0.05);
  return onDark >= onLight ? DARK_INK : LIGHT_INK;
}
