import { describe, expect, it } from 'vitest';

import { assignTrackIds, type RawFrame } from './simpleTracker';
import type { Bbox } from './trackProduction';
import type { Mat3 } from './homography';

// a 20x20 box centred so its floor-contact (bottom-centre) sits at (cx, cy)
const boxAt = (cx: number, cy: number): Bbox => [cx - 10, cy - 20, cx + 10, cy];
const METRES_PER_PIXEL: Mat3 = [[0.01, 0, 0], [0, 0.01, 0], [0, 0, 1]];

describe('simpleTracker', () => {
  it('keeps one id for a robot drifting slowly across frames', () => {
    const frames: RawFrame[] = [
      { timeSec: 0.0, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(100, 100) }] },
      { timeSec: 0.4, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(110, 105) }] },
      { timeSec: 0.8, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(122, 112) }] },
    ];
    const tracked = assignTrackIds(frames);
    const ids = tracked.map((f) => f.detections[0].trackId);
    expect(new Set(ids).size).toBe(1);
  });

  it('separates two robots and keeps their ids stable', () => {
    const frames: RawFrame[] = [
      { timeSec: 0.0, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(100, 100) }, { bbox: boxAt(500, 400) }] },
      { timeSec: 0.4, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(108, 104) }, { bbox: boxAt(492, 405) }] },
    ];
    const tracked = assignTrackIds(frames);
    // each frame has the two robots; ids consistent frame-to-frame, two distinct tracks
    const f0 = tracked[0].detections.map((d) => d.trackId);
    const f1 = tracked[1].detections.map((d) => d.trackId);
    expect(new Set([...f0, ...f1]).size).toBe(2);
    expect(f0).toEqual(f1); // nearest-contact keeps the same robot in the same slot
  });

  it('starts a new id after a long gap (track retired)', () => {
    const frames: RawFrame[] = [
      { timeSec: 0.0, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(100, 100) }] },
      { timeSec: 5.0, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(100, 100) }] }, // > maxGap later
    ];
    const tracked = assignTrackIds(frames, { maxGapSec: 1.0 });
    expect(tracked[0].detections[0].trackId).not.toBe(tracked[1].detections[0].trackId);
  });

  it('does not merge two robots closer than each other across a big jump', () => {
    // a teleport beyond maxDistPx must spawn a fresh id, not snap to the far robot
    const frames: RawFrame[] = [
      { timeSec: 0.0, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(100, 100) }] },
      { timeSec: 0.4, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(600, 600) }] },
    ];
    const tracked = assignTrackIds(frames);
    expect(tracked[0].detections[0].trackId).not.toBe(tracked[1].detections[0].trackId);
  });

  it('keeps one id for a stationary robot through a camera pan over 150 pixels', () => {
    const afterPan: Mat3 = [[0.01, 0, -2], [0, 0.01, 0], [0, 0, 1]];
    const frames: RawFrame[] = [
      { timeSec: 0, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(100, 200) }] },
      { timeSec: 0.4, homography: afterPan, detections: [{ bbox: boxAt(300, 200) }] },
    ];

    const tracked = assignTrackIds(frames);

    expect(tracked[0].detections[0].trackId).toBe(tracked[1].detections[0].trackId);
  });

  it('does not transfer an id to an opposing bumper after an occlusion', () => {
    const frames: RawFrame[] = [
      { timeSec: 0, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(100, 200), colour: { red: 0.2, blue: 0 } }] },
      { timeSec: 0.4, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(102, 200), colour: { red: 0, blue: 0.2 } }] },
    ];
    const tracked = assignTrackIds(frames);
    expect(tracked[0].detections[0].trackId).not.toBe(tracked[1].detections[0].trackId);
  });

  it('drops contacts projected outside the field', () => {
    const tracked = assignTrackIds([{ timeSec: 0, homography: METRES_PER_PIXEL, detections: [{ bbox: boxAt(1800, 100) }] }]);
    expect(tracked[0].detections).toHaveLength(0);
  });
});
