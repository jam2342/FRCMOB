import { describe, expect, it } from 'vitest';

import {
  compositeOver,
  contrastRatio,
  evaluateSample,
  isLargeText,
  parseCssColor,
  relativeLuminance,
  requiredRatio,
  resolveBackground,
} from './contrast.mjs';

const WHITE = { r: 255, g: 255, b: 255, a: 1 };
const BLACK = { r: 0, g: 0, b: 0, a: 1 };

describe('parseCssColor', () => {
  it('parses legacy rgb and rgba', () => {
    expect(parseCssColor('rgb(255, 0, 0)')).toEqual({ r: 255, g: 0, b: 0, a: 1 });
    expect(parseCssColor('rgba(0, 128, 255, 0.5)')).toEqual({ r: 0, g: 128, b: 255, a: 0.5 });
  });

  it('parses modern space-separated syntax with a slash alpha', () => {
    expect(parseCssColor('rgb(10 20 30 / 0.25)')).toEqual({ r: 10, g: 20, b: 30, a: 0.25 });
    expect(parseCssColor('rgb(10 20 30 / 50%)')).toEqual({ r: 10, g: 20, b: 30, a: 0.5 });
  });

  it('treats fully transparent as alpha 0, not as black', () => {
    // This is the exact bug that produced a 1.66:1 reading on a white page.
    expect(parseCssColor('rgba(0, 0, 0, 0)')).toEqual({ r: 0, g: 0, b: 0, a: 0 });
    expect(parseCssColor('transparent')).toEqual({ r: 0, g: 0, b: 0, a: 0 });
  });

  it('parses color(srgb ...) — what color-mix() computes to', () => {
    expect(parseCssColor('color(srgb 1 0 0)')).toEqual({ r: 255, g: 0, b: 0, a: 1 });
    expect(parseCssColor('color(srgb 0 0.5 1 / 0.4)')).toEqual({ r: 0, g: 127.5, b: 255, a: 0.4 });
  });

  it('returns null for colours it cannot express rather than guessing', () => {
    expect(parseCssColor('color(display-p3 1 0 0)')).toBeNull();
    expect(parseCssColor('#ffffff')).toBeNull();
    expect(parseCssColor(undefined)).toBeNull();
  });
});

describe('compositeOver', () => {
  it('an opaque foreground replaces the background', () => {
    expect(compositeOver(BLACK, WHITE)).toEqual({ r: 0, g: 0, b: 0, a: 1 });
  });

  it('a fully transparent foreground leaves the background untouched', () => {
    expect(compositeOver({ r: 0, g: 0, b: 0, a: 0 }, WHITE)).toEqual({ r: 255, g: 255, b: 255, a: 1 });
  });

  it('50% black over white is mid grey', () => {
    const out = compositeOver({ r: 0, g: 0, b: 0, a: 0.5 }, WHITE);
    expect(out.r).toBeCloseTo(127.5, 5);
    expect(out.a).toBe(1);
  });
});

describe('resolveBackground', () => {
  it('falls back to the base when every layer is transparent', () => {
    const layers = [
      { r: 0, g: 0, b: 0, a: 0 },
      { r: 0, g: 0, b: 0, a: 0 },
    ];
    expect(resolveBackground(layers, WHITE)).toEqual(WHITE);
  });

  it('stops at the first opaque ancestor', () => {
    // innermost transparent, then an opaque red ancestor
    const layers = [
      { r: 0, g: 0, b: 0, a: 0 },
      { r: 255, g: 0, b: 0, a: 1 },
    ];
    expect(resolveBackground(layers, WHITE)).toEqual({ r: 255, g: 0, b: 0, a: 1 });
  });

  it('composites translucent layers in paint order, not first-wins', () => {
    // A 50% black scrim inside an opaque white card => mid grey.
    const layers = [
      { r: 0, g: 0, b: 0, a: 0.5 },
      WHITE,
    ];
    const out = resolveBackground(layers, WHITE);
    expect(out.r).toBeCloseTo(127.5, 5);
  });
});

describe('contrastRatio', () => {
  it('black on white is the 21:1 maximum', () => {
    expect(contrastRatio(BLACK, WHITE)).toBeCloseTo(21, 5);
  });

  it('a colour against itself is 1:1', () => {
    expect(contrastRatio(WHITE, WHITE)).toBeCloseTo(1, 5);
  });

  it('is symmetric', () => {
    expect(contrastRatio(WHITE, BLACK)).toBeCloseTo(contrastRatio(BLACK, WHITE), 10);
  });

  it('composites translucent text before measuring', () => {
    const ghost = { r: 0, g: 0, b: 0, a: 0.1 };
    // Barely-there black text on white is nearly invisible, not 21:1.
    expect(contrastRatio(ghost, WHITE)).toBeLessThan(2);
  });

  it('matches the real bug: near-white card title on a white card', () => {
    // #eef7ff on #ffffff — what /home actually shipped in light theme.
    const title = { r: 238, g: 247, b: 255, a: 1 };
    expect(contrastRatio(title, WHITE)).toBeLessThan(1.2);
  });

  it('matches the fix: --color-text light on a white card', () => {
    // #243546 on #ffffff
    const fixed = { r: 36, g: 53, b: 70, a: 1 };
    expect(contrastRatio(fixed, WHITE)).toBeGreaterThan(12);
  });
});

describe('relativeLuminance', () => {
  it('anchors at the spec endpoints', () => {
    expect(relativeLuminance(WHITE)).toBeCloseTo(1, 5);
    expect(relativeLuminance(BLACK)).toBeCloseTo(0, 5);
  });
});

describe('large-text thresholds', () => {
  it('treats >=24px as large', () => {
    expect(isLargeText('24px', '400')).toBe(true);
    expect(isLargeText('23px', '400')).toBe(false);
  });

  it('treats >=18.66px as large only when bold', () => {
    expect(isLargeText('19px', '700')).toBe(true);
    expect(isLargeText('19px', '400')).toBe(false);
  });

  it('maps to the right required ratio', () => {
    expect(requiredRatio('14px', '400')).toBe(4.5);
    expect(requiredRatio('30px', '400')).toBe(3);
  });
});

describe('evaluateSample', () => {
  it('fails invisible text', () => {
    const verdict = evaluateSample(
      { color: 'rgb(238, 247, 255)', backgroundLayers: ['rgb(255, 255, 255)'], fontSize: '14px', fontWeight: '700' },
      WHITE,
    );
    expect(verdict.ok).toBe(false);
    expect(verdict.ratio).toBeLessThan(1.2);
  });

  it('passes readable text', () => {
    const verdict = evaluateSample(
      { color: 'rgb(36, 53, 70)', backgroundLayers: ['rgb(255, 255, 255)'], fontSize: '14px', fontWeight: '400' },
      WHITE,
    );
    expect(verdict.ok).toBe(true);
  });

  it('skips rather than guesses when a colour is unparseable', () => {
    const verdict = evaluateSample(
      { color: 'color(display-p3 1 1 1)', backgroundLayers: [], fontSize: '14px', fontWeight: '400' },
      WHITE,
    );
    expect(verdict.ok).toBe(true);
    expect(verdict.skipped).toBe('unparseable-color');
  });
});
