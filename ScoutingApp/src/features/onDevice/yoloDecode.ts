// Pure YOLO post-processing — no onnxruntime/canvas imports, so it unit-tests cleanly.
// Decodes the FRC detector's raw ONNX output (1 x 5 x 8400, single class) into boxes
// and runs non-max suppression. Mirrors what the backend ultralytics pipeline does
// internally; here we do it by hand because onnxruntime-web returns the raw tensor.

export type Box = { x1: number; y1: number; x2: number; y2: number; score: number };

// Letterbox geometry for fitting a (w x h) frame into a square `size` input while
// preserving aspect ratio — returns the scale + padding to undo it afterwards.
export type Letterbox = { scale: number; padX: number; padY: number };

export function letterboxParams(w: number, h: number, size: number): Letterbox {
  const scale = Math.min(size / w, size / h);
  return { scale, padX: (size - w * scale) / 2, padY: (size - h * scale) / 2 };
}

// Map a box from letterboxed input-pixel space back to original-frame pixels.
export function undoLetterbox(box: Box, lb: Letterbox): Box {
  return {
    x1: (box.x1 - lb.padX) / lb.scale,
    y1: (box.y1 - lb.padY) / lb.scale,
    x2: (box.x2 - lb.padX) / lb.scale,
    y2: (box.y2 - lb.padY) / lb.scale,
    score: box.score,
  };
}

// Decode raw output (row-major [1, 5, numAnchors]: rows cx,cy,w,h,score) to xyxy boxes
// in input-pixel space, keeping anchors above confThreshold. data[c*numAnchors + i].
export function decodeDetections(
  data: ArrayLike<number>,
  numAnchors: number,
  confThreshold: number,
): Box[] {
  const boxes: Box[] = [];
  const cx = 0;
  const cy = numAnchors;
  const w = 2 * numAnchors;
  const h = 3 * numAnchors;
  const sc = 4 * numAnchors;
  for (let i = 0; i < numAnchors; i++) {
    const score = data[sc + i];
    if (score < confThreshold) continue;
    const bw = data[w + i];
    const bh = data[h + i];
    const x = data[cx + i];
    const y = data[cy + i];
    boxes.push({ x1: x - bw / 2, y1: y - bh / 2, x2: x + bw / 2, y2: y + bh / 2, score });
  }
  return boxes;
}

export function iou(a: Box, b: Box): number {
  const ix1 = Math.max(a.x1, b.x1);
  const iy1 = Math.max(a.y1, b.y1);
  const ix2 = Math.min(a.x2, b.x2);
  const iy2 = Math.min(a.y2, b.y2);
  const iw = Math.max(0, ix2 - ix1);
  const ih = Math.max(0, iy2 - iy1);
  const inter = iw * ih;
  const areaA = Math.max(0, a.x2 - a.x1) * Math.max(0, a.y2 - a.y1);
  const areaB = Math.max(0, b.x2 - b.x1) * Math.max(0, b.y2 - b.y1);
  const union = areaA + areaB - inter;
  return union <= 0 ? 0 : inter / union;
}

// Greedy non-max suppression: keep the highest-scoring box, drop overlaps above
// iouThreshold, repeat. The raw output has many anchors firing on each robot.
export function nms(boxes: Box[], iouThreshold: number): Box[] {
  const sorted = [...boxes].sort((a, b) => b.score - a.score);
  const kept: Box[] = [];
  for (const box of sorted) {
    if (kept.every((k) => iou(box, k) <= iouThreshold)) kept.push(box);
  }
  return kept;
}

// Full decode: raw tensor -> NMS'd boxes mapped back to original frame coordinates.
export function postprocess(
  data: ArrayLike<number>,
  numAnchors: number,
  lb: Letterbox,
  opts: { confThreshold?: number; iouThreshold?: number } = {},
): Box[] {
  const conf = opts.confThreshold ?? 0.25;
  const iouT = opts.iouThreshold ?? 0.45;
  return nms(decodeDetections(data, numAnchors, conf), iouT).map((b) => undoLetterbox(b, lb));
}
