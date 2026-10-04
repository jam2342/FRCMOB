import { describe, expect, it } from 'vitest';

import { cropPlan, fieldRoi } from './fieldCrop';
import { calibrateFromTaps, fieldReferenceCorners } from './homography';

const L = 16.541;
const W = 8.0693;
// The four taps from the Einstein 13 stands video (1280x720).
const TAPS = [
  { x: 78, y: 430 },
  { x: 1202, y: 430 },
  { x: 1262, y: 632 },
  { x: 18, y: 632 },
];

describe('fieldRoi', () => {
  it('boxes the calibrated field with room above for robot height', () => {
    const { homography } = calibrateFromTaps(TAPS, fieldReferenceCorners(L, W));
    const roi = fieldRoi(homography, 1280, 720, L, W)!;
    // Field floor spans y 430-632; 50% of its height above, 15% below, clamped to the frame.
    expect(roi.y).toBeGreaterThanOrEqual(320);
    expect(roi.y).toBeLessThanOrEqual(335);
    expect(roi.y + roi.h).toBeGreaterThanOrEqual(655);
    expect(roi.y + roi.h).toBeLessThanOrEqual(665);
    expect(roi.x).toBe(0);
    expect(roi.w).toBe(1280);
  });

  it('refuses a calibration that puts the field off-screen', () => {
    const offscreen = [[1, 0, -5000], [0, 1, -5000], [0, 0, 1]];
    expect(fieldRoi(offscreen, 1280, 720, L, W)).toBeNull();
  });
});

describe('cropPlan', () => {
  it('keeps a 720p field strip at native resolution', () => {
    const plan = cropPlan({ x: 0, y: 330, w: 1280, h: 330 });
    expect(plan.scale).toBe(1);
    expect([plan.inputW, plan.inputH]).toEqual([1280, 352]);
  });

  it('scales a 1080p strip down to the same budget, never up', () => {
    const plan = cropPlan({ x: 0, y: 500, w: 1920, h: 500 });
    expect(plan.scale).toBeLessThan(1);
    expect(plan.inputW * plan.inputH).toBeLessThanOrEqual(1280 * 352 * 1.1);
    expect(plan.inputW % 32).toBe(0);
    expect(plan.inputH % 32).toBe(0);
  });
});
