import { describe, expect, it } from 'vitest';

import { thumbRect } from './robotThumb';

describe('thumbRect', () => {
  it('pads the box and clamps it to the frame', () => {
    expect(thumbRect([100, 100, 200, 150], 1280, 720)).toEqual({ x: 80, y: 90, w: 140, h: 70 });
    expect(thumbRect([0, 690, 60, 720], 1280, 720)).toEqual({ x: 0, y: 684, w: 72, h: 36 });
  });

  it('skips boxes too small to show anything', () => {
    expect(thumbRect([10, 10, 20, 40], 1280, 720)).toBeNull();
  });
});
