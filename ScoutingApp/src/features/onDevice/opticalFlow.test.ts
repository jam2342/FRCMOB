import { describe, expect, it, vi } from 'vitest';

import { projectPoint, type Mat3 } from './homography';
import { createCvPoseResolver, estimateInterframeMotion, StabilizedPose } from './opticalFlow';

const IDENTITY: Mat3 = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];
// camera motion mapping prev-frame px -> curr-frame px (a pan by (tx, ty))
const translate = (tx: number, ty: number): Mat3 => [
  [1, 0, tx],
  [0, 1, ty],
  [0, 0, 1],
];

describe('StabilizedPose', () => {
  it('holds the pose and counts lost frames when flow fails', () => {
    const stab = new StabilizedPose(IDENTITY);
    stab.update(null);
    expect(stab.lostFrames).toBe(1);
    stab.update(null);
    expect(stab.lostFrames).toBe(2);
    // pose unchanged through the misses
    expect(stab.homography).toEqual(IDENTITY);
  });

  it('resets the lost counter once flow recovers', () => {
    const stab = new StabilizedPose(IDENTITY);
    stab.update(null);
    stab.update(translate(5, 0));
    expect(stab.lostFrames).toBe(0);
  });

  it('keeps a static field point fixed as the camera pans (carryPose invariant)', () => {
    // base pose: image px == field coords. A field point sits at image (40, 30).
    const stab = new StabilizedPose(IDENTITY);
    const fieldPointImagePrev = { x: 40, y: 30 };
    const before = projectPoint(stab.homography, fieldPointImagePrev.x, fieldPointImagePrev.y);

    // the camera pans by (12, -7): the same physical point now appears 12 right, 7 up.
    const motion = translate(12, -7);
    stab.update(motion);
    const imageNow = { x: fieldPointImagePrev.x + 12, y: fieldPointImagePrev.y - 7 };
    const after = projectPoint(stab.homography, imageNow.x, imageNow.y);

    // carried pose maps the moved pixel back to the same field coordinate
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });

  it('composes motion across multiple frames', () => {
    const stab = new StabilizedPose(IDENTITY);
    stab.update(translate(10, 0));
    stab.update(translate(0, 10));
    // a point that drifted +10x then +10y maps back to its original field coord
    const after = projectPoint(stab.homography, 50 + 10, 50 + 10);
    expect(after.x).toBeCloseTo(50, 6);
    expect(after.y).toBeCloseTo(50, 6);
  });
});

describe('estimateInterframeMotion', () => {
  it('uses the CV_8U flow status bytes even when a float view is present', () => {
    const disposable = () => ({
      rows: 0,
      cols: 0,
      data: new Uint8Array(),
      data32F: new Float32Array(),
      data64F: new Float64Array(),
      delete: vi.fn(),
      empty: () => false,
    });
    const points = new Float32Array(
      Array.from({ length: 12 }, (_, index) => [index, index + 0.5]).flat(),
    );
    const prevPts = { ...disposable(), rows: 12, data32F: points };
    const nextPts = { ...disposable(), rows: 12, data32F: points };
    const status = {
      ...disposable(),
      rows: 12,
      data: new Uint8Array(12).fill(1),
      // OpenCV exposes this view too, but status is CV_8U and must not use it.
      data32F: new Float32Array(3),
    };
    const mats = [prevPts, nextPts, status, disposable(), disposable()];
    const arrays: number[][] = [];
    const homography = {
      ...disposable(),
      data64F: new Float64Array([1, 0, 0, 0, 1, 0, 0, 0, 1]),
    };
    const cv = {
      Mat: function Mat() {
        return mats.shift() ?? disposable();
      },
      Size: function Size() {},
      TermCriteria: function TermCriteria() {},
      matFromImageData: vi.fn(),
      matFromArray: vi.fn((_rows: number, _cols: number, _type: number, values: number[]) => {
        arrays.push(values);
        return disposable();
      }),
      cvtColor: vi.fn(),
      goodFeaturesToTrack: vi.fn(),
      calcOpticalFlowPyrLK: vi.fn(),
      findHomography: vi.fn(() => homography),
      COLOR_RGBA2GRAY: 1,
      CV_32FC2: 2,
      RANSAC: 3,
      TERM_CRITERIA_EPS: 4,
      TERM_CRITERIA_COUNT: 8,
    } as unknown as Parameters<typeof estimateInterframeMotion>[0];

    const motion = estimateInterframeMotion(cv, disposable(), disposable());

    expect(motion).toEqual(IDENTITY);
    expect(arrays).toHaveLength(2);
    expect(arrays[0]).toHaveLength(24);
    expect(arrays[1]).toHaveLength(24);
  });
});

describe('createCvPoseResolver Mat lifecycle', () => {
  it('deletes allocated Mats when grayscale conversion fails', () => {
    const rgbaDelete = vi.fn();
    const grayDelete = vi.fn();
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    Object.defineProperty(canvas, 'getContext', {
      configurable: true,
      value: vi.fn(() => ({
        getImageData: vi.fn(() => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 })),
      })),
    });

    const cv = {
      Mat: function Mat() {
        return {
          rows: 0,
          cols: 0,
          data: new Uint8Array(),
          data32F: new Float32Array(),
          data64F: new Float64Array(),
          delete: grayDelete,
          empty: () => false,
        };
      },
      Size: function Size() {},
      TermCriteria: function TermCriteria() {},
      matFromImageData: vi.fn(() => ({
        rows: 0,
        cols: 0,
        data: new Uint8Array(),
        data32F: new Float32Array(),
        data64F: new Float64Array(),
        delete: rgbaDelete,
        empty: () => false,
      })),
      matFromArray: vi.fn(),
      cvtColor: vi.fn(() => {
        throw new Error('conversion failed');
      }),
      goodFeaturesToTrack: vi.fn(),
      calcOpticalFlowPyrLK: vi.fn(),
      findHomography: vi.fn(),
      COLOR_RGBA2GRAY: 1,
      CV_32FC2: 2,
      RANSAC: 3,
      TERM_CRITERIA_EPS: 4,
      TERM_CRITERIA_COUNT: 8,
    } as unknown as Parameters<typeof createCvPoseResolver>[0];

    const resolver = createCvPoseResolver(cv, IDENTITY);

    expect(() => resolver.resolve(canvas)).toThrow('conversion failed');
    expect(grayDelete).toHaveBeenCalledTimes(1);
    expect(rgbaDelete).toHaveBeenCalledTimes(1);
  });
});
