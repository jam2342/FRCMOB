import { describe, expect, it } from 'vitest';

import { type Mat3 } from './homography';
import { type Bbox, type Frame, assemblePointsByTeam, produceTrackPoints } from './trackProduction';

const I: Mat3 = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];
const RED_SCORE: [number, number] = [11.902, 4.021];
const RED_DEPOT: [number, number] = [14.839, 7.168];
const BLUE_SCORE: [number, number] = [4.611, 4.021];
const NEUTRAL: [number, number] = [8.271, 4.035];

// bbox whose floor-contact (bottom-centre) is exactly field_xy under identity homography.
function bboxAt([x, y]: [number, number]): Bbox {
  return [x - 0.5, y - 1, x + 0.5, y];
}

describe('track production', () => {
  it('projects, zone-tags, and fills speeds', () => {
    const frames: Frame[] = [
      { timeSec: 0, homography: I, detections: [{ trackId: 1, bbox: bboxAt(RED_SCORE) }] },
      { timeSec: 1, homography: I, detections: [{ trackId: 1, bbox: bboxAt(RED_DEPOT) }] },
    ];
    const out = produceTrackPoints(frames);
    expect(out[1]).toHaveLength(2);
    expect(out[1][0].zoneKey).toBe('red_alliance_scoring_zone');
    expect(out[1][1].zoneKey).toBe('red_loading_depot_zone');
    expect(out[1][0].speedMps).toBeNull();
    const expected = Math.hypot(RED_DEPOT[0] - RED_SCORE[0], RED_DEPOT[1] - RED_SCORE[1]);
    expect(out[1][1].speedMps).toBeCloseTo(expected, 4);
  });

  it('pose fallback carries last good homography within staleness, drops beyond', () => {
    const frames: Frame[] = [
      { timeSec: 0, homography: I, detections: [{ trackId: 1, bbox: bboxAt(RED_SCORE) }] },
      { timeSec: 0.3, homography: null, detections: [{ trackId: 1, bbox: bboxAt(RED_SCORE) }] },
      { timeSec: 2, homography: null, detections: [{ trackId: 1, bbox: bboxAt(RED_SCORE) }] },
    ];
    const out = produceTrackPoints(frames, { maxPoseStalenessSec: 0.5 });
    expect(out[1].map((p) => p.timeSec)).toEqual([0, 0.3]);
  });

  it('drops low-confidence detections', () => {
    const frames: Frame[] = [
      {
        timeSec: 0,
        homography: I,
        detections: [
          { trackId: 1, bbox: bboxAt(RED_SCORE), confidence: 0.9 },
          { trackId: 2, bbox: bboxAt(NEUTRAL), confidence: 0.1 },
        ],
      },
    ];
    const out = produceTrackPoints(frames, { minDetectionConfidence: 0.5 });
    expect(out[1]).toBeDefined();
    expect(out[2]).toBeUndefined();
  });

  it('rejects a one-frame velocity spike', () => {
    const seq = [RED_SCORE, RED_SCORE, BLUE_SCORE, RED_SCORE, RED_SCORE];
    const frames: Frame[] = seq.map((p, i) => ({
      timeSec: i * 0.1,
      homography: I,
      detections: [{ trackId: 1, bbox: bboxAt(p) }],
    }));
    const out = produceTrackPoints(frames, { smoothWindow: 1 });
    expect(out[1]).toHaveLength(4);
    expect(out[1].some((p) => p.zoneKey === 'blue_alliance_scoring_zone')).toBe(false);
  });

  it('assemble merges fragments by team and drops unresolved tracks', () => {
    const frames: Frame[] = [
      {
        timeSec: 0,
        homography: I,
        detections: [
          { trackId: 1, bbox: bboxAt(RED_SCORE) },
          { trackId: 9, bbox: bboxAt(NEUTRAL) },
        ],
      },
      { timeSec: 1, homography: I, detections: [{ trackId: 2, bbox: bboxAt(RED_SCORE) }] },
    ];
    const tracks = produceTrackPoints(frames);
    const byTeam = assemblePointsByTeam(tracks, { 1: 'frc1111', 2: 'frc1111', 9: null });
    expect(Object.keys(byTeam)).toEqual(['frc1111']);
    expect(byTeam['frc1111']).toHaveLength(2);
  });
});
