// Where to point the detector. A stands camera sees the field as a thin strip -- about
// 1280x230 of a 1280x720 frame on the Einstein footage -- yet the full frame used to be
// squashed into an 896x896 square that was mostly padding, crowd and ceiling. Cropping
// to the calibrated field and spending a fixed pixel budget on it measured 2.1x faster
// *and* found twice the robots per frame (3.2 vs 1.6 over five frames), because the
// robots end up larger in the model's input instead of smaller.
import { invert3, projectPoint, type Mat3 } from './homography';

export type PixelRect = { x: number; y: number; w: number; h: number };

export type CropPlan = {
  source: PixelRect; // region of the frame to read
  inputW: number; // model input size (multiples of 32, as YOLO needs)
  inputH: number;
  scale: number; // input px per source px
};

// 1280x352: the field strip of a 720p stands video at native resolution.
const CROP_PIXEL_BUDGET = 1280 * 352;
const STRIDE = 32;

// Robots stand up from the floor, so a robot on the far edge extends above the far line;
// the near line sits low and is often behind the crowd's heads.
const MARGIN_ABOVE = 0.5;
const MARGIN_BELOW = 0.15;
const MARGIN_SIDES = 0.05;

// Bounding box of the field floor in the frame, padded for robot height. `imageToField`
// is the calibration (image px -> field metres). Null when the projection is unusable,
// so the caller falls back to the full frame.
export function fieldRoi(
  imageToField: Mat3,
  frameW: number,
  frameH: number,
  fieldLengthM: number,
  fieldWidthM: number,
): PixelRect | null {
  let fieldToImage: Mat3;
  try {
    fieldToImage = invert3(imageToField);
  } catch {
    return null;
  }
  const corners = [
    [0, 0],
    [fieldLengthM, 0],
    [fieldLengthM, fieldWidthM],
    [0, fieldWidthM],
  ].map(([x, y]) => projectPoint(fieldToImage, x, y));
  if (corners.some((p) => !Number.isFinite(p.x) || !Number.isFinite(p.y))) return null;
  const xs = corners.map((p) => p.x);
  const ys = corners.map((p) => p.y);
  let x1 = Math.min(...xs);
  let x2 = Math.max(...xs);
  let y1 = Math.min(...ys);
  let y2 = Math.max(...ys);
  const w = x2 - x1;
  const h = y2 - y1;
  if (!(w > 0 && h > 0)) return null;
  x1 -= w * MARGIN_SIDES;
  x2 += w * MARGIN_SIDES;
  y1 -= h * MARGIN_ABOVE;
  y2 += h * MARGIN_BELOW;
  const rect = {
    x: Math.max(0, Math.floor(x1)),
    y: Math.max(0, Math.floor(y1)),
    w: 0,
    h: 0,
  };
  rect.w = Math.min(frameW, Math.ceil(x2)) - rect.x;
  rect.h = Math.min(frameH, Math.ceil(y2)) - rect.y;
  // A calibration that puts the field off-screen is not worth trusting.
  if (rect.w < frameW * 0.2 || rect.h < 16) return null;
  return rect;
}

// Size the model input for a region: native resolution when it fits the budget, scaled
// down evenly when it doesn't -- never up.
export function cropPlan(source: PixelRect, pixelBudget: number = CROP_PIXEL_BUDGET): CropPlan {
  const scale = Math.min(1, Math.sqrt(pixelBudget / (source.w * source.h)));
  const inputW = Math.max(STRIDE, Math.ceil((source.w * scale) / STRIDE) * STRIDE);
  const inputH = Math.max(STRIDE, Math.ceil((source.h * scale) / STRIDE) * STRIDE);
  return { source, inputW, inputH, scale };
}
