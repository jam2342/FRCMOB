// On-device field calibration math — the in-browser mirror of the backend's
// on_device.py (estimate_homography / calibrate_from_taps / project_point).
//
// A scout taps the 4 field corners once; this turns those taps + their known field
// coordinates into an image->field homography (and its inverse) so detections can be
// projected to field metres entirely offline. The field is planar, so 4 point
// correspondences fully determine the homography via an exact linear solve — no SVD
// needed, which is why this ports cleanly to plain TypeScript.

export type Point = { x: number; y: number };
export type Mat3 = number[][]; // 3x3, row-major

export type Calibration = {
  homography: Mat3; // image px -> field metres
  inverse: Mat3; // field metres -> image px (for drawing field overlays back on the frame)
  rmseM: number; // reprojection error over the taps, in field metres
};

// Solve the 8x8 linear system A·h = b by Gaussian elimination with partial pivoting.
function solveLinear(a: number[][], b: number[]): number[] {
  const n = b.length;
  // augmented matrix
  const m = a.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let pivot = col;
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(m[r][col]) > Math.abs(m[pivot][col])) pivot = r;
    }
    if (Math.abs(m[pivot][col]) < 1e-12) throw new Error('degenerate calibration (collinear taps?)');
    [m[col], m[pivot]] = [m[pivot], m[col]];
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = m[r][col] / m[col][col];
      for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c];
    }
  }
  return m.map((row, i) => row[n] / row[i]);
}

// Exact homography mapping src -> dst from 4 correspondences.
function homographyFrom4(src: Point[], dst: Point[]): Mat3 {
  const a: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i++) {
    const { x, y } = src[i];
    const { x: u, y: v } = dst[i];
    a.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
    b.push(u);
    a.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
    b.push(v);
  }
  const h = solveLinear(a, b); // h11..h32 (h33 fixed to 1)
  return [
    [h[0], h[1], h[2]],
    [h[3], h[4], h[5]],
    [h[6], h[7], 1],
  ];
}

export function projectPoint(h: Mat3, x: number, y: number): Point {
  const denom = h[2][0] * x + h[2][1] * y + h[2][2];
  if (Math.abs(denom) < 1e-12) throw new Error('point projects to infinity');
  return {
    x: (h[0][0] * x + h[0][1] * y + h[0][2]) / denom,
    y: (h[1][0] * x + h[1][1] * y + h[1][2]) / denom,
  };
}

export function invert3(m: Mat3): Mat3 {
  const [a, b, c] = m[0];
  const [d, e, f] = m[1];
  const [g, h, i] = m[2];
  const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (Math.abs(det) < 1e-12) throw new Error('singular homography');
  const inv = [
    [e * i - f * h, c * h - b * i, b * f - c * e],
    [f * g - d * i, a * i - c * g, c * d - a * f],
    [d * h - e * g, b * g - a * h, a * e - b * d],
  ];
  return inv.map((row) => row.map((v) => v / det));
}

export function reprojectionRmse(h: Mat3, src: Point[], dst: Point[]): number {
  if (src.length === 0) return Infinity;
  let sum = 0;
  for (let i = 0; i < src.length; i++) {
    const p = projectPoint(h, src[i].x, src[i].y);
    sum += (p.x - dst[i].x) ** 2 + (p.y - dst[i].y) ** 2;
  }
  return Math.sqrt(sum / src.length);
}

export function mat3mul(a: Mat3, b: Mat3): Mat3 {
  const out: Mat3 = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      out[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j];
    }
  }
  return out;
}

// Advance an image->field homography by one frame of camera motion (mirrors
// on_device.carry_pose). interframeMotion maps previous-frame px -> current-frame px
// (from optical flow); H_t = H_{t-1} · M_t^{-1} keeps a static field point fixed, so
// a one-time calibration rides through camera motion without re-detecting any tags.
export function carryPose(imageToField: Mat3, interframeMotion: Mat3): Mat3 {
  return mat3mul(imageToField, invert3(interframeMotion));
}

// The four field-floor corners (metres) the 4-tap UI prompts for, in order:
// (0,0) -> (L,0) -> (L,W) -> (0,W).
export function fieldReferenceCorners(lengthM: number, widthM: number): Point[] {
  return [
    { x: 0, y: 0 },
    { x: lengthM, y: 0 },
    { x: lengthM, y: widthM },
    { x: 0, y: widthM },
  ];
}

// One-time manual calibration from exactly 4 taps + their known field coords.
export function calibrateFromTaps(imagePoints: Point[], fieldPoints: Point[]): Calibration {
  if (imagePoints.length !== fieldPoints.length) {
    throw new Error('imagePoints and fieldPoints must be the same length');
  }
  if (imagePoints.length !== 4) {
    throw new Error(`need exactly 4 taps, got ${imagePoints.length}`);
  }
  const homography = homographyFrom4(imagePoints, fieldPoints);
  return {
    homography,
    inverse: invert3(homography),
    rmseM: reprojectionRmse(homography, imagePoints, fieldPoints),
  };
}
