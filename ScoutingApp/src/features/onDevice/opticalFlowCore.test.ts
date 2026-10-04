import { describe, expect, it } from 'vitest';

import { projectPoint, type Mat3 } from './homography';
import { registerCalibrationFrame, StabilizedPose } from './opticalFlow';
import {
  calcOpticalFlowPyrLK,
  estimateInterframeMotionCore,
  findHomographyRansac,
  goodFeaturesToTrack,
  grayscaleFromRgba,
  homographyFromPoints,
  type GrayImage,
} from './opticalFlowCore';

// These mirror backend/tests/test_on_device_cv.py, which the OpenCV-backed Python
// implementation already passes. Same scene, same motions, same tolerances — so this is a
// like-for-like check against a known-good reference, not a fresh set of expectations
// invented to match whatever the new code happens to do.

// Deterministic PRNG so a failure is always reproducible.
function makeRng(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    s >>>= 0;
    return s / 0x100000000;
  };
}

// A dark frame scattered with bright blobs -> plenty of trackable corners.
function texturedImage(w = 480, h = 360, seed = 1): GrayImage {
  const rng = makeRng(seed);
  const data = new Float32Array(w * h).fill(30);
  for (let i = 0; i < 180; i += 1) {
    const cx = 12 + Math.floor(rng() * (w - 24));
    const cy = 12 + Math.floor(rng() * (h - 24));
    const r = 2 + Math.floor(rng() * 3);
    const value = 150 + Math.floor(rng() * 105);
    for (let y = cy - r; y <= cy + r; y += 1) {
      for (let x = cx - r; x <= cx + r; x += 1) {
        if (x < 0 || y < 0 || x >= w || y >= h) continue;
        if ((x - cx) ** 2 + (y - cy) ** 2 <= r * r) data[y * w + x] = value;
      }
    }
  }
  return { data, width: w, height: h };
}

// np.roll equivalent: content at (x,y) moves to (x+dx, y+dy). The wrapped edge strip is a
// minority that RANSAC rejects, exactly as in the Python test.
function shift(img: GrayImage, dx: number, dy: number): GrayImage {
  const { width: w, height: h } = img;
  const data = new Float32Array(w * h);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const sx = ((x - dx) % w + w) % w;
      const sy = ((y - dy) % h + h) % h;
      data[y * w + x] = img.data[sy * w + sx];
    }
  }
  return { data, width: w, height: h };
}

describe('grayscaleFromRgba', () => {
  it('applies Rec.601 luma like cv.COLOR_RGBA2GRAY', () => {
    const rgba = new Uint8ClampedArray([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255]);
    const gray = grayscaleFromRgba(rgba, 4, 1);
    expect(gray.data[0]).toBeCloseTo(76.245, 2);
    expect(gray.data[1]).toBeCloseTo(149.685, 2);
    expect(gray.data[2]).toBeCloseTo(29.07, 2);
    expect(gray.data[3]).toBeCloseTo(255, 2);
  });
});

describe('goodFeaturesToTrack', () => {
  it('finds corners on a textured frame and none on a blank one', () => {
    expect(goodFeaturesToTrack(texturedImage()).length).toBeGreaterThan(50);
    const blank: GrayImage = { data: new Float32Array(200 * 200), width: 200, height: 200 };
    expect(goodFeaturesToTrack(blank)).toHaveLength(0);
  });

  it('honours maxCorners and keeps corners minDistance apart', () => {
    const corners = goodFeaturesToTrack(texturedImage(), { maxCorners: 25, minDistance: 10 });
    expect(corners.length).toBeLessThanOrEqual(25);
    for (let i = 0; i < corners.length; i += 1) {
      for (let j = i + 1; j < corners.length; j += 1) {
        const d = Math.hypot(corners[i].x - corners[j].x, corners[i].y - corners[j].y);
        expect(d).toBeGreaterThanOrEqual(10);
      }
    }
  });
});

describe('calcOpticalFlowPyrLK', () => {
  it('tracks a known translation to sub-pixel accuracy', () => {
    const prev = texturedImage();
    const curr = shift(prev, 6, -4);
    const corners = goodFeaturesToTrack(prev, { maxCorners: 60 });
    const { points, status } = calcOpticalFlowPyrLK(prev, curr, corners);

    const errors: number[] = [];
    for (let i = 0; i < corners.length; i += 1) {
      if (!status[i]) continue;
      // Skip the wrapped edge strip, where the "true" motion does not apply.
      if (corners[i].x + 6 >= prev.width - 2 || corners[i].y - 4 < 2) continue;
      errors.push(Math.hypot(points[i].x - (corners[i].x + 6), points[i].y - (corners[i].y - 4)));
    }
    expect(errors.length).toBeGreaterThan(20);
    errors.sort((a, b) => a - b);
    expect(errors[Math.floor(errors.length / 2)]).toBeLessThan(0.5);
  });

  it('recovers a displacement larger than the window via the pyramid', () => {
    // 24 px exceeds the 21 px window, so a single-level solve could not find it.
    const prev = texturedImage();
    const curr = shift(prev, 24, 0);
    const corners = goodFeaturesToTrack(prev, { maxCorners: 40 });
    const { points, status } = calcOpticalFlowPyrLK(prev, curr, corners);
    const tracked = corners
      .map((c, i) => ({ c, p: points[i], ok: status[i] }))
      .filter((r) => r.ok && r.c.x + 24 < prev.width - 2);
    const dxs = tracked.map((r) => r.p.x - r.c.x).sort((a, b) => a - b);
    expect(dxs.length).toBeGreaterThan(10);
    expect(dxs[Math.floor(dxs.length / 2)]).toBeCloseTo(24, 0);
  });
});

describe('homographyFromPoints', () => {
  it('recovers an exact transform from 4 correspondences', () => {
    const h: Mat3 = [
      [1.2, 0.1, 30],
      [-0.05, 1.1, -12],
      [0.0002, 0.0001, 1],
    ];
    const src = [
      { x: 10, y: 20 },
      { x: 300, y: 40 },
      { x: 280, y: 250 },
      { x: 30, y: 220 },
    ];
    const dst = src.map((p) => projectPoint(h, p.x, p.y));
    const got = homographyFromPoints(src, dst);
    expect(got).not.toBeNull();
    for (const p of src) {
      const a = projectPoint(got as Mat3, p.x, p.y);
      const b = projectPoint(h, p.x, p.y);
      expect(a.x).toBeCloseTo(b.x, 4);
      expect(a.y).toBeCloseTo(b.y, 4);
    }
  });

  it('returns null rather than throwing on too few or degenerate points', () => {
    expect(homographyFromPoints([{ x: 0, y: 0 }], [{ x: 0, y: 0 }])).toBeNull();
    const collinear = [
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 2, y: 0 },
      { x: 3, y: 0 },
    ];
    expect(homographyFromPoints(collinear, collinear)).toBeNull();
  });
});

describe('findHomographyRansac', () => {
  it('ignores movers that a plain least-squares fit would follow', () => {
    // 40 points shifted by the camera, 15 "robots" moving independently. This is the
    // whole reason RANSAC is here rather than a direct fit.
    const rng = makeRng(7);
    const src: Array<{ x: number; y: number }> = [];
    const dst: Array<{ x: number; y: number }> = [];
    for (let i = 0; i < 40; i += 1) {
      const p = { x: rng() * 400, y: rng() * 300 };
      src.push(p);
      dst.push({ x: p.x + 9, y: p.y - 5 });
    }
    for (let i = 0; i < 15; i += 1) {
      const p = { x: rng() * 400, y: rng() * 300 };
      src.push(p);
      dst.push({ x: p.x - 40 + rng() * 20, y: p.y + 35 });
    }
    const h = findHomographyRansac(src, dst);
    expect(h).not.toBeNull();
    const moved = projectPoint(h as Mat3, 200, 150);
    expect(moved.x).toBeCloseTo(209, 0);
    expect(moved.y).toBeCloseTo(145, 0);
  });

  it('is deterministic across runs', () => {
    const rng = makeRng(3);
    const src = Array.from({ length: 30 }, () => ({ x: rng() * 400, y: rng() * 300 }));
    const dst = src.map((p) => ({ x: p.x + 4, y: p.y + 2 }));
    expect(findHomographyRansac(src, dst)).toEqual(findHomographyRansac(src, dst));
  });

  it('returns null below four correspondences', () => {
    expect(findHomographyRansac([{ x: 1, y: 1 }], [{ x: 2, y: 2 }])).toBeNull();
  });
});

// ── The three ported backend cases ──────────────────────────────────────────────

describe('estimateInterframeMotionCore (ported from test_on_device_cv.py)', () => {
  it('recovers a known motion', () => {
    const prev = texturedImage();
    const dx = 12;
    const dy = -7;
    const curr = shift(prev, dx, dy);
    const est = estimateInterframeMotionCore(prev, curr);
    expect(est).not.toBeNull();
    const got = projectPoint(est as Mat3, 240, 180);
    expect(got.x).toBeCloseTo(240 + dx, 0);
    expect(got.y).toBeCloseTo(180 + dy, 0);
    expect(Math.abs(got.x - (240 + dx))).toBeLessThan(1.5);
    expect(Math.abs(got.y - (180 + dy))).toBeLessThan(1.5);
  });

  it('returns null without features', () => {
    const blank: GrayImage = { data: new Float32Array(200 * 200), width: 200, height: 200 };
    expect(estimateInterframeMotionCore(blank, blank)).toBeNull();
  });

  it('holds a static field point under a sustained pan', () => {
    // base calibration: pixels/50 -> field metres
    const base: Mat3 = [
      [0.02, 0, 0],
      [0, 0.02, 0],
      [0, 0, 1],
    ];
    const f0 = projectPoint(base, 240, 180);
    const pose = new StabilizedPose(base);
    let prev = texturedImage();
    const dx = 8;
    const dy = 3;
    const drift: number[] = [];
    for (let k = 1; k <= 5; k += 1) {
      const curr = shift(prev, dx, dy);
      pose.update(estimateInterframeMotionCore(prev, curr));
      const fk = projectPoint(pose.homography, 240 + k * dx, 180 + k * dy);
      drift.push(Math.abs(fk.x - f0.x) + Math.abs(fk.y - f0.y));
      prev = curr;
    }
    expect(pose.lostFrames).toBe(0);
    expect(Math.max(...drift)).toBeLessThan(0.25);
  });

  it('registers the first recording frame to a panned calibration frame', () => {
    const base: Mat3 = [[0.02, 0, 0], [0, 0.02, 0], [0, 0, 1]];
    const reference = texturedImage();
    const current = shift(reference, 18, -6);

    const registered = registerCalibrationFrame(base, reference, current);
    const before = projectPoint(base, 240, 180);
    const after = projectPoint(registered, 258, 174);

    expect(after.x).toBeCloseTo(before.x, 1);
    expect(after.y).toBeCloseTo(before.y, 1);
  });

  it('refuses a recording frame with different calibration dimensions', () => {
    const base: Mat3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    expect(() => registerCalibrationFrame(base, texturedImage(480, 360), texturedImage(640, 480)))
      .toThrow(/Please re-calibrate for this camera size/);
  });

  it('refuses when the first recording frame cannot register to calibration', () => {
    const base: Mat3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    const blank: GrayImage = { data: new Float32Array(320 * 240), width: 320, height: 240 };
    expect(() => registerCalibrationFrame(base, blank, blank))
      .toThrow(/Could not register.*Please re-calibrate/);
  });
});
