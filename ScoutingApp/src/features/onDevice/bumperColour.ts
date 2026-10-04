// Bumper colour per detection: the share of saturated red and blue pixels in the bottom
// of the box, where the bumpers sit. It only has to tell alliances apart -- enough to
// stop the stitcher joining a red robot's path onto a blue one's. Thresholds follow
// OpenCV's HSV scale (H 0-180, S and V 0-255) so the benchmark tooling can match them.

const BAND_SHARE = 0.45; // bottom share of the box that is read

export type BumperColour = { red: number; blue: number };

// OpenCV-style HSV for one 8-bit RGB pixel.
function hsv(r: number, g: number, b: number): [number, number, number] {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const v = max;
  const s = max === 0 ? 0 : ((max - min) / max) * 255;
  let h = 0;
  if (max !== min) {
    if (max === r) h = (60 * (g - b)) / (max - min);
    else if (max === g) h = 120 + (60 * (b - r)) / (max - min);
    else h = 240 + (60 * (r - g)) / (max - min);
    if (h < 0) h += 360;
  }
  return [h / 2, s, v];
}

// `rgba` is the band's pixels as read from a canvas (getImageData().data).
export function bumperColourOfPixels(rgba: ArrayLike<number>): BumperColour {
  const n = Math.floor(rgba.length / 4);
  if (n === 0) return { red: 0, blue: 0 };
  let red = 0;
  let blue = 0;
  for (let i = 0; i < n; i++) {
    const [h, s, v] = hsv(rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2]);
    if ((h <= 8 || h >= 170) && s >= 110 && v >= 70) red += 1;
    else if (h >= 100 && h <= 128 && s >= 110 && v >= 60) blue += 1;
  }
  return { red: red / n, blue: blue / n };
}

// The bumper band of a box, clamped to the frame, in whole pixels (null when empty).
export function bumperBand(
  bbox: [number, number, number, number],
  frameW: number,
  frameH: number,
): { x: number; y: number; w: number; h: number } | null {
  const x1 = Math.max(0, Math.round(bbox[0]));
  const x2 = Math.min(frameW, Math.round(bbox[2]));
  const y2 = Math.min(frameH, Math.round(bbox[3]));
  const top = Math.max(0, Math.round(bbox[1]));
  const y1 = Math.max(0, Math.floor(y2 - BAND_SHARE * (y2 - top)));
  if (x2 <= x1 || y2 <= y1) return null;
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

export function readBumperColour(
  ctx: CanvasRenderingContext2D,
  bbox: [number, number, number, number],
  frameW: number,
  frameH: number,
): BumperColour {
  const band = bumperBand(bbox, frameW, frameH);
  if (!band) return { red: 0, blue: 0 };
  return bumperColourOfPixels(ctx.getImageData(band.x, band.y, band.w, band.h).data);
}
