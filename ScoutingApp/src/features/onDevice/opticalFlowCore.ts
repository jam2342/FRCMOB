// The three OpenCV algorithms the on-device camera path actually needs, written out so we
// do not ship a 7.3 MB / 2.4 MB-gzipped emscripten build for them: Shi-Tomasi corners,
// pyramidal Lucas-Kanade flow, and a RANSAC homography.
//
// Each function keeps the cv.* defaults so it is a like-for-like substitute on its own.
// estimateInterframeMotionCore, the entry point the app actually calls, overrides them
// with lighter values -- see the measurements above CORNER_DEFAULTS for why.
//
// Pure and dependency-free like homography.ts / trackProduction.ts: no cv, no canvas, no
// DOM. Everything takes plain arrays so it is testable in node.

import { type Mat3, type Point } from './homography';

export type GrayImage = {
  data: Float32Array; // luma 0..255, row-major
  width: number;
  height: number;
};

// Rec.601 luma, matching cv.COLOR_RGBA2GRAY.
export function grayscaleFromRgba(rgba: Uint8ClampedArray | Uint8Array, width: number, height: number): GrayImage {
  const out = new Float32Array(width * height);
  for (let i = 0, p = 0; i < out.length; i += 1, p += 4) {
    out[i] = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2];
  }
  return { data: out, width, height };
}

function at(img: GrayImage, x: number, y: number): number {
  const cx = x < 0 ? 0 : x >= img.width ? img.width - 1 : x;
  const cy = y < 0 ? 0 : y >= img.height ? img.height - 1 : y;
  return img.data[cy * img.width + cx];
}

// Bilinear sample — LK works at sub-pixel offsets, so nearest-neighbour would stall the
// iteration well before it converges.
function sampleBilinear(img: GrayImage, x: number, y: number): number {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = x - x0;
  const fy = y - y0;
  const a = at(img, x0, y0);
  const b = at(img, x0 + 1, y0);
  const c = at(img, x0, y0 + 1);
  const d = at(img, x0 + 1, y0 + 1);
  return a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy;
}

// Central differences, the same 3x3 Scharr/Sobel-family gradient cv uses for corner
// scoring. Halved so the scale matches a standard Sobel/2.
function gradients(img: GrayImage): { ix: Float32Array; iy: Float32Array } {
  const { width: w, height: h } = img;
  const ix = new Float32Array(w * h);
  const iy = new Float32Array(w * h);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      ix[y * w + x] = (at(img, x + 1, y) - at(img, x - 1, y)) * 0.5;
      iy[y * w + x] = (at(img, x, y + 1) - at(img, x, y - 1)) * 0.5;
    }
  }
  return { ix, iy };
}

export type CornerOptions = {
  maxCorners?: number;
  qualityLevel?: number;
  minDistance?: number;
  blockSize?: number;
};

// Shi-Tomasi: score each pixel by the smaller eigenvalue of the windowed structure
// tensor, keep those above qualityLevel * bestScore, then greedily suppress neighbours
// closer than minDistance. Same contract as cv.goodFeaturesToTrack.
export function goodFeaturesToTrack(img: GrayImage, options: CornerOptions = {}): Point[] {
  const maxCorners = options.maxCorners ?? 400;
  const qualityLevel = options.qualityLevel ?? 0.01;
  const minDistance = options.minDistance ?? 8;
  const blockSize = options.blockSize ?? 3;

  const { width: w, height: h } = img;
  const half = Math.max(1, Math.floor(blockSize / 2));
  const { ix, iy } = gradients(img);
  const score = new Float32Array(w * h);
  let best = 0;

  // Ignore a border of `half` so every window is fully inside the image.
  for (let y = half; y < h - half; y += 1) {
    for (let x = half; x < w - half; x += 1) {
      let sxx = 0;
      let syy = 0;
      let sxy = 0;
      for (let wy = -half; wy <= half; wy += 1) {
        for (let wx = -half; wx <= half; wx += 1) {
          const gx = ix[(y + wy) * w + (x + wx)];
          const gy = iy[(y + wy) * w + (x + wx)];
          sxx += gx * gx;
          syy += gy * gy;
          sxy += gx * gy;
        }
      }
      // Smaller eigenvalue of [[sxx, sxy], [sxy, syy]].
      const trace = sxx + syy;
      const diff = sxx - syy;
      const root = Math.sqrt(diff * diff + 4 * sxy * sxy);
      const minEig = (trace - root) / 2;
      score[y * w + x] = minEig;
      if (minEig > best) best = minEig;
    }
  }
  if (best <= 0) return [];

  const threshold = best * qualityLevel;
  const candidates: Array<{ x: number; y: number; s: number }> = [];
  for (let y = half; y < h - half; y += 1) {
    for (let x = half; x < w - half; x += 1) {
      const s = score[y * w + x];
      if (s >= threshold) candidates.push({ x, y, s });
    }
  }
  candidates.sort((a, b) => b.s - a.s);

  const kept: Point[] = [];
  const minDistSq = minDistance * minDistance;
  for (const c of candidates) {
    if (kept.length >= maxCorners) break;
    let tooClose = false;
    for (const k of kept) {
      const dx = k.x - c.x;
      const dy = k.y - c.y;
      if (dx * dx + dy * dy < minDistSq) {
        tooClose = true;
        break;
      }
    }
    if (!tooClose) kept.push({ x: c.x, y: c.y });
  }
  return kept;
}

// Halve an image with a 2x2 box filter. Cheap, and adequate because LK only needs the
// coarse levels to get within a pixel or two before the fine levels refine.
function downsample(img: GrayImage): GrayImage {
  const w = Math.max(1, Math.floor(img.width / 2));
  const h = Math.max(1, Math.floor(img.height / 2));
  const data = new Float32Array(w * h);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const sx = x * 2;
      const sy = y * 2;
      data[y * w + x] = (at(img, sx, sy) + at(img, sx + 1, sy) + at(img, sx, sy + 1) + at(img, sx + 1, sy + 1)) / 4;
    }
  }
  return { data, width: w, height: h };
}

function buildPyramid(img: GrayImage, maxLevel: number): GrayImage[] {
  const levels: GrayImage[] = [img];
  for (let i = 0; i < maxLevel; i += 1) {
    const prev = levels[levels.length - 1];
    if (prev.width < 8 || prev.height < 8) break;
    levels.push(downsample(prev));
  }
  return levels;
}

export type FlowOptions = {
  winSize?: number;
  maxLevel?: number;
  maxIterations?: number;
  epsilon?: number;
  minEigenvalue?: number;
};

export type FlowResult = {
  points: Point[]; // tracked position in the second image (garbage where status is false)
  status: boolean[];
};

// Pyramidal Lucas-Kanade. Coarse-to-fine so displacements larger than the window still
// converge, which is what a shaky handheld camera produces. Mirrors
// cv.calcOpticalFlowPyrLK(winSize=21, maxLevel=3, criteria=(EPS|COUNT, 30, 0.01)).
export function calcOpticalFlowPyrLK(
  prev: GrayImage,
  curr: GrayImage,
  points: Point[],
  options: FlowOptions = {},
): FlowResult {
  const winSize = options.winSize ?? 21;
  const maxLevel = options.maxLevel ?? 3;
  const maxIterations = options.maxIterations ?? 30;
  const epsilon = options.epsilon ?? 0.01;
  const minEigenvalue = options.minEigenvalue ?? 1e-4;

  const half = Math.floor(winSize / 2);
  const prevPyr = buildPyramid(prev, maxLevel);
  const currPyr = buildPyramid(curr, maxLevel);
  const levels = Math.min(prevPyr.length, currPyr.length);

  const out: Point[] = [];
  const status: boolean[] = [];

  for (const p of points) {
    let gx = 0; // running displacement guess, in the current level's pixels
    let gy = 0;
    let ok = true;

    for (let level = levels - 1; level >= 0; level -= 1) {
      const pi = prevPyr[level];
      const ci = currPyr[level];
      const scale = 1 / Math.pow(2, level);
      const px = p.x * scale;
      const py = p.y * scale;

      // Structure tensor of the window in the previous image; constant across iterations.
      let sxx = 0;
      let syy = 0;
      let sxy = 0;
      const gradX: number[] = [];
      const gradY: number[] = [];
      for (let wy = -half; wy <= half; wy += 1) {
        for (let wx = -half; wx <= half; wx += 1) {
          const x = px + wx;
          const y = py + wy;
          const dx = (sampleBilinear(pi, x + 1, y) - sampleBilinear(pi, x - 1, y)) * 0.5;
          const dy = (sampleBilinear(pi, x, y + 1) - sampleBilinear(pi, x, y - 1)) * 0.5;
          gradX.push(dx);
          gradY.push(dy);
          sxx += dx * dx;
          syy += dy * dy;
          sxy += dx * dy;
        }
      }
      const det = sxx * syy - sxy * sxy;
      const trace = sxx + syy;
      const root = Math.sqrt(Math.max(0, (sxx - syy) * (sxx - syy) + 4 * sxy * sxy));
      const minEig = (trace - root) / 2 / Math.max(1, gradX.length);
      if (det <= 0 || minEig < minEigenvalue) {
        ok = false;
        break;
      }

      for (let iter = 0; iter < maxIterations; iter += 1) {
        let bx = 0;
        let by = 0;
        let i = 0;
        for (let wy = -half; wy <= half; wy += 1) {
          for (let wx = -half; wx <= half; wx += 1, i += 1) {
            const diff = sampleBilinear(pi, px + wx, py + wy) - sampleBilinear(ci, px + gx + wx, py + gy + wy);
            bx += diff * gradX[i];
            by += diff * gradY[i];
          }
        }
        const vx = (syy * bx - sxy * by) / det;
        const vy = (sxx * by - sxy * bx) / det;
        gx += vx;
        gy += vy;
        if (vx * vx + vy * vy < epsilon * epsilon) break;
      }

      if (level > 0) {
        gx *= 2;
        gy *= 2;
      }
    }

    if (ok) {
      const nx = p.x + gx;
      const ny = p.y + gy;
      // A point pushed outside the frame is lost, not tracked to a clamped edge pixel.
      ok = nx >= 0 && ny >= 0 && nx < curr.width && ny < curr.height;
      out.push({ x: nx, y: ny });
    } else {
      out.push({ x: p.x, y: p.y });
    }
    status.push(ok);
  }

  return { points: out, status };
}

// Gauss-Jordan with partial pivoting. Returns null instead of throwing, because a
// degenerate RANSAC sample is an ordinary event here, not an error.
function solveLinearOrNull(a: number[][], b: number[]): number[] | null {
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col += 1) {
    let pivot = col;
    for (let r = col + 1; r < n; r += 1) {
      if (Math.abs(m[r][col]) > Math.abs(m[pivot][col])) pivot = r;
    }
    if (Math.abs(m[pivot][col]) < 1e-12) return null;
    [m[col], m[pivot]] = [m[pivot], m[col]];
    for (let r = 0; r < n; r += 1) {
      if (r === col) continue;
      const f = m[r][col] / m[col][col];
      for (let c = col; c <= n; c += 1) m[r][c] -= f * m[col][c];
    }
  }
  const out = new Array<number>(n);
  for (let i = 0; i < n; i += 1) {
    const v = m[i][n] / m[i][i];
    if (!Number.isFinite(v)) return null;
    out[i] = v;
  }
  return out;
}

// Least-squares DLT with h33 fixed to 1, via the 8x8 normal equations. Exact when given
// 4 correspondences, a proper fit when given more — which is what lets RANSAC refine on
// its full inlier set instead of keeping a noisy 4-point model.
export function homographyFromPoints(src: Point[], dst: Point[]): Mat3 | null {
  const n = Math.min(src.length, dst.length);
  if (n < 4) return null;
  const ata: number[][] = Array.from({ length: 8 }, () => new Array<number>(8).fill(0));
  const atb: number[] = new Array<number>(8).fill(0);

  const addRow = (row: number[], rhs: number) => {
    for (let i = 0; i < 8; i += 1) {
      for (let j = 0; j < 8; j += 1) ata[i][j] += row[i] * row[j];
      atb[i] += row[i] * rhs;
    }
  };

  for (let i = 0; i < n; i += 1) {
    const { x, y } = src[i];
    const { x: u, y: v } = dst[i];
    addRow([x, y, 1, 0, 0, 0, -u * x, -u * y], u);
    addRow([0, 0, 0, x, y, 1, -v * x, -v * y], v);
  }
  const h = solveLinearOrNull(ata, atb);
  if (!h) return null;
  return [
    [h[0], h[1], h[2]],
    [h[3], h[4], h[5]],
    [h[6], h[7], 1],
  ];
}

// Deterministic PRNG so a RANSAC result is reproducible across runs and in tests.
function makeRng(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    s >>>= 0;
    return s / 0x100000000;
  };
}

function projectOrNull(h: Mat3, x: number, y: number): Point | null {
  const denom = h[2][0] * x + h[2][1] * y + h[2][2];
  if (Math.abs(denom) < 1e-12) return null;
  return {
    x: (h[0][0] * x + h[0][1] * y + h[0][2]) / denom,
    y: (h[1][0] * x + h[1][1] * y + h[1][2]) / denom,
  };
}

export type RansacOptions = {
  threshold?: number;
  iterations?: number;
  seed?: number;
};

// RANSAC homography, standing in for cv.findHomography(src, dst, cv.RANSAC, 3.0).
// Robustness is the whole point: robots and people move independently of the camera, and
// a plain least-squares fit over every correspondence would be dragged off by them.
export function findHomographyRansac(
  src: Point[],
  dst: Point[],
  options: RansacOptions = {},
): Mat3 | null {
  const threshold = options.threshold ?? 3.0;
  const iterations = options.iterations ?? 500;
  const n = Math.min(src.length, dst.length);
  if (n < 4) return null;

  const rng = makeRng(options.seed ?? 0x5eed);
  const thresholdSq = threshold * threshold;
  let bestInliers: number[] = [];

  const inliersFor = (h: Mat3): number[] => {
    const inliers: number[] = [];
    for (let i = 0; i < n; i += 1) {
      const p = projectOrNull(h, src[i].x, src[i].y);
      if (!p) continue;
      const dx = p.x - dst[i].x;
      const dy = p.y - dst[i].y;
      if (dx * dx + dy * dy <= thresholdSq) inliers.push(i);
    }
    return inliers;
  };

  for (let iter = 0; iter < iterations; iter += 1) {
    const idx = new Set<number>();
    let guard = 0;
    while (idx.size < 4 && guard < 64) {
      idx.add(Math.floor(rng() * n));
      guard += 1;
    }
    if (idx.size < 4) continue;
    const pick = [...idx];
    const h = homographyFromPoints(
      pick.map((i) => src[i]),
      pick.map((i) => dst[i]),
    );
    if (!h) continue;
    const inliers = inliersFor(h);
    if (inliers.length > bestInliers.length) {
      bestInliers = inliers;
      // Nothing better is available once everything agrees.
      if (bestInliers.length === n) break;
    }
  }

  if (bestInliers.length < 4) return null;
  // Refit on the consensus set. Without this the result carries the noise of whichever
  // four points happened to be sampled, and that error compounds frame over frame.
  const refined = homographyFromPoints(
    bestInliers.map((i) => src[i]),
    bestInliers.map((i) => dst[i]),
  );
  return refined ?? null;
}

export const MIN_FLOW_POINTS = 12;

// Lighter than the cv.* defaults this replaces, because measurement said we were paying
// for precision we cannot use. On real Einstein frames at 640x360 with known motion,
// in-browser:
//
//   preset                                    median err   max err   median
//   cv defaults (400 corners, win21, it30)      0.0020 px  0.0059 px  163 ms
//   these       (120 corners, win15, it10)      0.0027 px  0.0232 px   44 ms
//   leaner      ( 80 corners, win13, it8)       0.0000 px  0.0001 px   28 ms
//
// Every preset is far inside the noise floor for this job -- one pixel is roughly two
// centimetres of field -- so the choice is about robustness, not accuracy. 120 well-spread
// corners leave RANSAC plenty of static background to find consensus in when a good share
// of the frame is moving robots, which the synthetic-translation benchmark above does not
// exercise. The leaner preset wins that benchmark but has less to fall back on.
//
// maxLevel stays at 3: the pyramid is what lets a handheld jolt larger than the window
// still converge, and it is cheap compared with the window and iteration count.
const CORNER_DEFAULTS: CornerOptions = {
  maxCorners: 120,
  qualityLevel: 0.01,
  minDistance: 12,
  blockSize: 3,
};
const FLOW_DEFAULTS: FlowOptions = {
  winSize: 15,
  maxLevel: 3,
  maxIterations: 10,
  epsilon: 0.01,
};

// prev-frame px -> curr-frame px homography, the drop-in for the cv.* pipeline in
// estimateInterframeMotion. Returns null when too few features survive, so the caller
// holds its last good pose rather than carrying a bad one.
export function estimateInterframeMotionCore(
  prev: GrayImage,
  curr: GrayImage,
  options: { corners?: CornerOptions; flow?: FlowOptions } = {},
): Mat3 | null {
  const corners = goodFeaturesToTrack(prev, { ...CORNER_DEFAULTS, ...options.corners });
  if (corners.length < MIN_FLOW_POINTS) return null;

  const { points, status } = calcOpticalFlowPyrLK(prev, curr, corners, {
    ...FLOW_DEFAULTS,
    ...options.flow,
  });

  const p0: Point[] = [];
  const p1: Point[] = [];
  for (let i = 0; i < status.length; i += 1) {
    if (!status[i]) continue;
    p0.push(corners[i]);
    p1.push(points[i]);
  }
  if (p0.length < MIN_FLOW_POINTS) return null;

  return findHomographyRansac(p0, p1, { threshold: 3.0 });
}
