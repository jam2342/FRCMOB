// Pure WCAG contrast math. Deliberately free of any browser API so it can be
// unit-tested — an earlier ad-hoc version of this shipped two wrong answers
// (a ratio of 9.6e8, and 1.66:1 for dark text on a white page) because it
// treated `rgba(0,0,0,0)` as opaque black and never composited translucent
// layers. The browser side only reports colour strings; every calculation
// happens here.

// Parse a CSS colour string into {r,g,b,a} with r/g/b in 0..255 and a in 0..1.
// Returns null for anything not expressible as rgb()/rgba() so callers can
// report it rather than silently guessing.
export function parseCssColor(input) {
  if (typeof input !== 'string') return null;
  const raw = input.trim().toLowerCase();
  if (raw === 'transparent') return { r: 0, g: 0, b: 0, a: 0 };

  // `color-mix()` — used heavily in this codebase for button fills — computes to
  // `color(srgb r g b / a)` with 0..1 channels. Without this branch the guard was
  // blind to 62% of the page.
  const srgb = raw.match(/^color\(\s*srgb\s+([^)]*)\)$/);
  if (srgb) {
    const bits = srgb[1].replace(/\//g, ' ').split(/\s+/).filter(Boolean);
    if (bits.length < 3) return null;
    const unit = (token, scale) => {
      const value = Number.parseFloat(token);
      if (!Number.isFinite(value)) return null;
      return token.endsWith('%') ? (value / 100) * scale : value * scale;
    };
    const r = unit(bits[0], 255);
    const g = unit(bits[1], 255);
    const b = unit(bits[2], 255);
    const a = bits[3] === undefined ? 1 : unit(bits[3], 1);
    if (r === null || g === null || b === null || a === null) return null;
    const clip = (v, hi) => Math.min(hi, Math.max(0, v));
    return { r: clip(r, 255), g: clip(g, 255), b: clip(b, 255), a: clip(a, 1) };
  }

  const match = raw.match(/^rgba?\(([^)]*)\)$/);
  if (!match) return null;

  // Accept both legacy `rgb(r, g, b, a)` and modern `rgb(r g b / a)`.
  const parts = match[1].replace(/\//g, ' ').replace(/,/g, ' ').split(/\s+/).filter(Boolean);
  if (parts.length < 3) return null;

  const channel = (token) => {
    const value = Number.parseFloat(token);
    if (!Number.isFinite(value)) return null;
    return token.endsWith('%') ? (value / 100) * 255 : value;
  };
  const alpha = (token) => {
    if (token === undefined) return 1;
    const value = Number.parseFloat(token);
    if (!Number.isFinite(value)) return null;
    return token.endsWith('%') ? value / 100 : value;
  };

  const r = channel(parts[0]);
  const g = channel(parts[1]);
  const b = channel(parts[2]);
  const a = alpha(parts[3]);
  if (r === null || g === null || b === null || a === null) return null;

  const clamp = (v, hi) => Math.min(hi, Math.max(0, v));
  return { r: clamp(r, 255), g: clamp(g, 255), b: clamp(b, 255), a: clamp(a, 1) };
}

// Standard source-over compositing of `fg` onto `bg`.
export function compositeOver(fg, bg) {
  const a = fg.a + bg.a * (1 - fg.a);
  if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
  const mix = (f, b) => (f * fg.a + b * bg.a * (1 - fg.a)) / a;
  return { r: mix(fg.r, bg.r), g: mix(fg.g, bg.g), b: mix(fg.b, bg.b), a };
}

// `layers` is innermost-first (the element, then its ancestors). Paint order is
// the reverse: the outermost layer sits on the base and inner layers stack on
// top. `base` is whatever the page composites onto when nothing is opaque.
export function resolveBackground(layers, base = { r: 255, g: 255, b: 255, a: 1 }) {
  let result = base;
  for (let i = layers.length - 1; i >= 0; i -= 1) {
    const layer = layers[i];
    if (!layer || layer.a === 0) continue;
    result = compositeOver(layer, result);
  }
  return result;
}

export function relativeLuminance({ r, g, b }) {
  const channel = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

// Contrast of `fg` against `bg`. A translucent foreground is composited onto the
// background first — text at 50% alpha is not the same colour it declares.
export function contrastRatio(fg, bg) {
  const solidFg = fg.a < 1 ? compositeOver(fg, bg) : fg;
  const l1 = relativeLuminance(solidFg);
  const l2 = relativeLuminance(bg);
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
  return (hi + 0.05) / (lo + 0.05);
}

// WCAG "large text": >= 24px, or >= 18.66px when bold.
export function isLargeText(fontSizePx, fontWeight) {
  const size = Number.parseFloat(fontSizePx);
  const weight = Number.parseFloat(fontWeight);
  if (!Number.isFinite(size)) return false;
  if (size >= 24) return true;
  return size >= 18.66 && Number.isFinite(weight) && weight >= 700;
}

export function requiredRatio(fontSizePx, fontWeight) {
  return isLargeText(fontSizePx, fontWeight) ? 3 : 4.5;
}

// One text sample -> a verdict. `sample` comes straight off the page as strings.
export function evaluateSample(sample, base) {
  const color = parseCssColor(sample.color);
  if (!color) return { ok: true, skipped: 'unparseable-color', color: sample.color };

  const layers = sample.backgroundLayers.map(parseCssColor);
  const badIndex = layers.findIndex((l) => l === null);
  if (badIndex !== -1) {
    // Name the offending layer. A skip nobody can identify is a blind spot,
    // and the whole point of the guard is that nothing goes unmeasured.
    return { ok: true, skipped: 'unparseable-background', color: sample.backgroundLayers[badIndex] };
  }

  const background = resolveBackground(layers, base);
  const ratio = contrastRatio(color, background);
  const required = requiredRatio(sample.fontSize, sample.fontWeight);
  return { ok: ratio >= required, ratio, required, background };
}
