import { describe, expect, it } from 'vitest';

import {
  type Mat3,
  type Point,
  calibrateFromTaps,
  carryPose,
  fieldReferenceCorners,
  invert3,
  mat3mul,
  projectPoint,
  reprojectionRmse,
} from './homography';

// A known perspective homography (image px -> field m) for generating exact taps.
const H_TRUTH: Mat3 = [
  [0.008, 0.001, 0.5],
  [0.0005, 0.007, 0.3],
  [1e-5, 2e-5, 1.0],
];

const IMG_CORNERS: Point[] = [
  { x: 100, y: 100 },
  { x: 1800, y: 120 },
  { x: 1750, y: 1000 },
  { x: 120, y: 980 },
];

function field(p: Point): Point {
  return projectPoint(H_TRUTH, p.x, p.y);
}

describe('on-device homography', () => {
  it('recovers a known perspective map from 4 taps', () => {
    const fieldPts = IMG_CORNERS.map(field);
    const cal = calibrateFromTaps(IMG_CORNERS, fieldPts);
    expect(cal.rmseM).toBeLessThan(1e-6);
    // a held-out point projects to the same place under the recovered map
    const truth = projectPoint(H_TRUTH, 1000, 700);
    const got = projectPoint(cal.homography, 1000, 700);
    expect(got.x).toBeCloseTo(truth.x, 4);
    expect(got.y).toBeCloseTo(truth.y, 4);
  });

  it('inverse maps field coords back to the original pixels', () => {
    const fieldPts = IMG_CORNERS.map(field);
    const cal = calibrateFromTaps(IMG_CORNERS, fieldPts);
    for (let i = 0; i < IMG_CORNERS.length; i++) {
      const back = projectPoint(cal.inverse, fieldPts[i].x, fieldPts[i].y);
      expect(back.x).toBeCloseTo(IMG_CORNERS[i].x, 3);
      expect(back.y).toBeCloseTo(IMG_CORNERS[i].y, 3);
    }
  });

  it('requires exactly 4 taps', () => {
    expect(() => calibrateFromTaps(IMG_CORNERS.slice(0, 3), [])).toThrow();
    expect(() =>
      calibrateFromTaps(IMG_CORNERS.slice(0, 3), [
        { x: 0, y: 0 },
        { x: 1, y: 0 },
        { x: 0, y: 1 },
      ]),
    ).toThrow();
  });

  it('throws on collinear taps (degenerate)', () => {
    const collinear: Point[] = [
      { x: 0, y: 0 },
      { x: 10, y: 10 },
      { x: 20, y: 20 },
      { x: 30, y: 30 },
    ];
    const fieldPts = fieldReferenceCorners(16.541, 8.0693);
    expect(() => calibrateFromTaps(collinear, fieldPts)).toThrow();
  });

  it('maps real field corners to a sane field rectangle', () => {
    const corners = fieldReferenceCorners(16.541, 8.0693);
    const cal = calibrateFromTaps(IMG_CORNERS, corners);
    // center of the image should land roughly mid-field
    const mid = projectPoint(cal.homography, 950, 540);
    expect(mid.x).toBeGreaterThan(0);
    expect(mid.x).toBeLessThan(16.541);
    expect(mid.y).toBeGreaterThan(0);
    expect(mid.y).toBeLessThan(8.0693);
  });

  it('invert3 round-trips', () => {
    const inv = invert3(H_TRUTH);
    const p = projectPoint(H_TRUTH, 640, 480);
    const back = projectPoint(inv, p.x, p.y);
    expect(back.x).toBeCloseTo(640, 3);
    expect(back.y).toBeCloseTo(480, 3);
  });

  it('reprojectionRmse is ~0 for an exact fit', () => {
    const fieldPts = IMG_CORNERS.map(field);
    const cal = calibrateFromTaps(IMG_CORNERS, fieldPts);
    expect(reprojectionRmse(cal.homography, IMG_CORNERS, fieldPts)).toBeLessThan(1e-6);
  });

  it('carryPose keeps a static field point fixed under camera motion', () => {
    const base: Mat3 = [
      [0.01, 0, 0],
      [0, 0.01, 0],
      [0, 0, 1],
    ]; // px/100 -> field
    const F = projectPoint(base, 500, 300);
    const m1: Mat3 = [
      [1, 0, 50],
      [0, 1, 20],
      [0, 0, 1],
    ];
    const p1: Point = { x: 550, y: 320 }; // m1 @ (500,300)
    const h1 = carryPose(base, m1);
    expect(projectPoint(h1, p1.x, p1.y).x).toBeCloseTo(F.x, 6);
    expect(projectPoint(h1, p1.x, p1.y).y).toBeCloseTo(F.y, 6);
  });

  it('mat3mul by identity is a no-op', () => {
    const I: Mat3 = [
      [1, 0, 0],
      [0, 1, 0],
      [0, 0, 1],
    ];
    const out = mat3mul(H_TRUTH, I);
    expect(out[0][0]).toBeCloseTo(H_TRUTH[0][0], 9);
    expect(out[2][2]).toBeCloseTo(H_TRUTH[2][2], 9);
  });
});
