import { describe, expect, it } from 'vitest';

import {
  type Box,
  decodeDetections,
  iou,
  letterboxParams,
  nms,
  postprocess,
  undoLetterbox,
} from './yoloDecode';

// Build a raw [1,5,N] row-major buffer from a list of (cx,cy,w,h,score) anchors.
function rawTensor(anchors: number[][], numAnchors: number): Float32Array {
  const data = new Float32Array(5 * numAnchors);
  anchors.forEach((a, i) => {
    for (let c = 0; c < 5; c++) data[c * numAnchors + i] = a[c];
  });
  return data;
}

describe('yolo decode', () => {
  it('decodes only anchors above the confidence threshold, as xyxy', () => {
    const data = rawTensor(
      [
        [100, 100, 40, 20, 0.9], // cx,cy,w,h -> (80,90)-(120,110)
        [300, 300, 10, 10, 0.1], // below threshold
      ],
      2,
    );
    const boxes = decodeDetections(data, 2, 0.25);
    expect(boxes).toHaveLength(1);
    expect(boxes[0]).toMatchObject({ x1: 80, y1: 90, x2: 120, y2: 110 });
    expect(boxes[0].score).toBeCloseTo(0.9, 5); // float32 round-trip
  });

  it('iou is 1 for identical boxes and 0 for disjoint', () => {
    const a: Box = { x1: 0, y1: 0, x2: 10, y2: 10, score: 1 };
    expect(iou(a, a)).toBeCloseTo(1, 6);
    expect(iou(a, { x1: 100, y1: 100, x2: 110, y2: 110, score: 1 })).toBe(0);
  });

  it('nms keeps the top box and drops overlaps, keeps distinct ones', () => {
    const boxes: Box[] = [
      { x1: 0, y1: 0, x2: 10, y2: 10, score: 0.9 },
      { x1: 1, y1: 1, x2: 11, y2: 11, score: 0.8 }, // heavy overlap with the first
      { x1: 50, y1: 50, x2: 60, y2: 60, score: 0.7 }, // distinct
    ];
    const kept = nms(boxes, 0.45);
    expect(kept).toHaveLength(2);
    expect(kept[0].score).toBe(0.9);
    expect(kept[1].score).toBe(0.7);
  });

  it('undoLetterbox inverts the letterbox mapping', () => {
    const lb = letterboxParams(1920, 1080, 640); // wide frame -> vertical padding
    const box: Box = { x1: 100, y1: 100, x2: 200, y2: 200, score: 1 };
    const back = undoLetterbox(box, lb);
    // forward again should return the input box
    const fwd = {
      x1: back.x1 * lb.scale + lb.padX,
      y1: back.y1 * lb.scale + lb.padY,
      x2: back.x2 * lb.scale + lb.padX,
      y2: back.y2 * lb.scale + lb.padY,
    };
    expect(fwd.x1).toBeCloseTo(100, 4);
    expect(fwd.y1).toBeCloseTo(100, 4);
  });

  it('letterbox for a 1920x1080 frame fits within 640 with vertical pad', () => {
    const lb = letterboxParams(1920, 1080, 640);
    expect(lb.scale).toBeCloseTo(640 / 1920, 6);
    expect(lb.padX).toBe(0);
    expect(lb.padY).toBeGreaterThan(0);
  });

  it('postprocess: decode + nms + back to frame coords', () => {
    const N = 3;
    // two overlapping detections of one robot + one distinct, in 640 space
    const data = rawTensor(
      [
        [320, 320, 60, 60, 0.95],
        [322, 318, 58, 62, 0.6], // overlaps the first
        [100, 500, 40, 40, 0.8], // distinct
      ],
      N,
    );
    const lb = letterboxParams(1280, 1280, 640); // scale 0.5, no pad
    const boxes = postprocess(data, N, lb, { confThreshold: 0.25, iouThreshold: 0.45 });
    expect(boxes).toHaveLength(2);
    // first box center ~ (320,320)/0.5 = (640,640) in frame coords
    const top = boxes[0];
    expect((top.x1 + top.x2) / 2).toBeCloseTo(640, 1);
    expect((top.y1 + top.y2) / 2).toBeCloseTo(640, 1);
  });
});
