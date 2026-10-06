// A small photo of each detected robot, so the identify step can show what a path looks
// like instead of "Track 47 · neutral zone". Scouts tell robots apart by eye far more
// reliably than by where a path wandered, and the six robots in a match rarely look alike.

const THUMB_W = 112;
const THUMB_H = 84;
const PAD = 0.2; // share of the box added around it, so bumpers and mechanisms stay in frame
const MIN_BOX_PX = 16; // smaller boxes are too few pixels to be worth a photo

let scratch: HTMLCanvasElement | null = null;

// The crop rectangle for a box, padded and clamped to the frame (null when too small).
export function thumbRect(
  bbox: [number, number, number, number],
  frameW: number,
  frameH: number,
): { x: number; y: number; w: number; h: number } | null {
  const bw = bbox[2] - bbox[0];
  const bh = bbox[3] - bbox[1];
  if (bw < MIN_BOX_PX || bh < MIN_BOX_PX) return null;
  const x = Math.max(0, Math.floor(bbox[0] - bw * PAD));
  const y = Math.max(0, Math.floor(bbox[1] - bh * PAD));
  const w = Math.min(frameW, Math.ceil(bbox[2] + bw * PAD)) - x;
  const h = Math.min(frameH, Math.ceil(bbox[3] + bh * PAD)) - y;
  return w > 0 && h > 0 ? { x, y, w, h } : null;
}

// JPEG of one box from `frame` (the canvas still holding the sampled frame). Resolves to
// null when the box is too small or the browser can't encode.
function captureRobotThumb(
  frame: HTMLCanvasElement,
  bbox: [number, number, number, number],
): Promise<Blob | null> {
  const rect = thumbRect(bbox, frame.width, frame.height);
  if (!rect) return Promise.resolve(null);
  scratch ??= document.createElement('canvas');
  const scale = Math.min(THUMB_W / rect.w, THUMB_H / rect.h);
  scratch.width = Math.max(1, Math.round(rect.w * scale));
  scratch.height = Math.max(1, Math.round(rect.h * scale));
  const ctx = scratch.getContext('2d');
  if (!ctx) return Promise.resolve(null);
  ctx.drawImage(frame, rect.x, rect.y, rect.w, rect.h, 0, 0, scratch.width, scratch.height);
  const canvas = scratch;
  return new Promise((resolve) => {
    try {
      canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.75);
    } catch {
      resolve(null);
    }
  });
}

// Photos for a frame's boxes, one at a time: the scratch canvas is shared.
export async function captureRobotThumbs(
  frame: HTMLCanvasElement,
  bboxes: [number, number, number, number][],
): Promise<(Blob | null)[]> {
  const out: (Blob | null)[] = [];
  for (const bbox of bboxes) out.push(await captureRobotThumb(frame, bbox));
  return out;
}
