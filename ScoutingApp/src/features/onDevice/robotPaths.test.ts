import { describe, expect, it } from 'vitest';

import { buildRobotPaths } from './robotPaths';
import { type RawFrame } from './simpleTracker';

// Identity homography: image pixels are field metres, so boxes can sit anywhere on a
// 16 x 8 "field" and the tracker's speed limits read directly.
const H = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];

describe('buildRobotPaths', () => {
  it('keeps the photo from the biggest, most confident box on each path', () => {
    const small = new Blob(['small']);
    const big = new Blob(['big']);
    const frames: RawFrame[] = [0, 0.33, 0.66, 1].map((t, i) => ({
      timeSec: t,
      homography: H,
      detections: [
        {
          bbox: i === 2 ? [2, 1, 4, 3] : [2.5, 2, 3.5, 3],
          confidence: 0.9,
          thumb: i === 2 ? big : small,
        },
      ],
    }));
    const [path] = buildRobotPaths(frames, { stitch: false });
    expect(path.thumb).toBe(big);
  });

  it('has no photo when none was captured', () => {
    const frames: RawFrame[] = [0, 0.33, 0.66].map((t) => ({
      timeSec: t,
      homography: H,
      detections: [{ bbox: [2.5, 2, 3.5, 3], confidence: 0.9 }],
    }));
    expect(buildRobotPaths(frames, { stitch: false })[0].thumb).toBeNull();
  });
});
