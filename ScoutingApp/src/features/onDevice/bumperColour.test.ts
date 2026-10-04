import { describe, expect, it } from 'vitest';

import { bumperBand, bumperColourOfPixels } from './bumperColour';

function pixels(...rgb: [number, number, number][]): number[] {
  return rgb.flatMap(([r, g, b]) => [r, g, b, 255]);
}

describe('bumperColourOfPixels', () => {
  it('counts saturated red and blue, not grey or dark pixels', () => {
    const c = bumperColourOfPixels(pixels([200, 20, 20], [20, 40, 200], [120, 120, 120], [30, 0, 0]));
    expect(c.red).toBeCloseTo(0.25);
    expect(c.blue).toBeCloseTo(0.25);
  });

  it('reads a wrapped-around red hue as red', () => {
    expect(bumperColourOfPixels(pixels([210, 20, 60])).red).toBe(1);
  });

  it('is zero for no pixels', () => {
    expect(bumperColourOfPixels([])).toEqual({ red: 0, blue: 0 });
  });
});

describe('bumperBand', () => {
  it('reads the bottom of the box, clamped to the frame', () => {
    expect(bumperBand([10, 100, 50, 200], 1280, 720)).toEqual({ x: 10, y: 155, w: 40, h: 45 });
    expect(bumperBand([-5, 700, 30, 740], 1280, 720)).toEqual({ x: 0, y: 711, w: 30, h: 9 });
    expect(bumperBand([10, 10, 10, 20], 1280, 720)).toBeNull();
  });
});
