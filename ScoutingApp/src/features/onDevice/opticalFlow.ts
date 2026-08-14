// In-browser optical-flow camera stabilization — the JS/OpenCV.js mirror of the backend
// on_device_cv.py. Real-footage testing showed the field's AprilTags are unreadable from
// the stands, so the pose is fixed ONCE by the 4-tap calibration and then carried
// frame-to-frame from optical flow of the static background (carryPose = H · M⁻¹),
// holding the last good pose when flow drops out. The pure carry core (StabilizedPose)
// is unit-tested; the OpenCV.js flow estimate runs only in a real browser (WASM).

import { carryPose, type Mat3 } from './homography';

// ── pure carry core (no OpenCV; unit-tested) ────────────────────────────
// Mirror of on_device_cv.StabilizedPose minus the cv2 flow estimate. Feed it the
// per-frame motion (prev-px → curr-px homography) or null when flow failed; it composes
// the calibrated pose forward and holds the last good pose on a miss.
export class StabilizedPose {
  homography: Mat3;
  lostFrames = 0; // consecutive frames flow failed (UI can prompt a re-tap)

  constructor(base: Mat3) {
    this.homography = base;
  }

  // Call once per frame *after the first*. `motion` null = flow failed this frame.
  update(motion: Mat3 | null): Mat3 {
    if (motion) {
      try {
        this.homography = carryPose(this.homography, motion);
        this.lostFrames = 0;
      } catch {
        this.lostFrames += 1; // singular motion → hold pose
      }
    } else {
      this.lostFrames += 1; // too few features → hold pose
    }
    return this.homography;
  }
}

// ── OpenCV.js loader (lazy; heavy WASM only loaded when stabilization is on) ──
// Minimal structural type for just the cv surface we touch, so we don't couple to
// mirada's full type tree.
type CvMat = {
  rows: number;
  cols: number;
  data: Uint8Array;
  data32F: Float32Array;
  data64F: Float64Array;
  delete: () => void;
  empty: () => boolean;
};
type OpenCvLike = {
  Mat: { new (): CvMat };
  Size: { new (w: number, h: number): unknown };
  TermCriteria: { new (type: number, maxCount: number, epsilon: number): unknown };
  matFromImageData: (data: ImageData) => CvMat;
  matFromArray: (rows: number, cols: number, type: number, arr: number[]) => CvMat;
  cvtColor: (src: CvMat, dst: CvMat, code: number) => void;
  goodFeaturesToTrack: (
    img: CvMat,
    corners: CvMat,
    maxCorners: number,
    quality: number,
    minDist: number,
    mask: CvMat,
    blockSize: number,
  ) => void;
  calcOpticalFlowPyrLK: (
    prev: CvMat,
    next: CvMat,
    prevPts: CvMat,
    nextPts: CvMat,
    status: CvMat,
    err: CvMat,
    winSize: unknown,
    maxLevel: number,
    criteria: unknown,
  ) => void;
  findHomography: (src: CvMat, dst: CvMat, method: number, ransacThresh: number) => CvMat;
  COLOR_RGBA2GRAY: number;
  CV_32FC2: number;
  RANSAC: number;
  TERM_CRITERIA_EPS: number;
  TERM_CRITERIA_COUNT: number;
  onRuntimeInitialized?: () => void;
};

let _cvReady: Promise<OpenCvLike> | null = null;

// OpenCV.js is a UMD/emscripten bundle: importing it as an ESM module doesn't initialize
// the runtime (it depends on classic-script globals → "Module is not defined" under Vite).
// Load it the documented way — inject a <script> for the bundled asset (Vite's ?url emits
// + fingerprints it, so it's served same-origin and works offline via the service worker)
// and resolve once emscripten signals the runtime is ready.
export function loadOpenCv(): Promise<OpenCvLike> {
  if (_cvReady) return _cvReady;
  _cvReady = (async () => {
    const w = globalThis as unknown as { cv?: OpenCvLike };
    if (w.cv && typeof w.cv.Mat === 'function') return w.cv;
    const { default: url } = await import('@techstark/opencv-js/dist/opencv.js?url');
    await new Promise<void>((resolve, reject) => {
      const script = document.createElement('script');
      // Bound the whole load (network + parse) so a slow/failed fetch of the ~8 MB blob
      // degrades to the static-calibration pose promptly instead of hanging the capture.
      const timeout = setTimeout(() => reject(new Error('OpenCV.js load timed out')), 45_000);
      script.src = url;
      script.async = true;
      script.onload = () => {
        clearTimeout(timeout);
        resolve();
      };
      script.onerror = () => {
        clearTimeout(timeout);
        reject(new Error('failed to load OpenCV.js'));
      };
      document.head.appendChild(script);
    });
    const cv = w.cv;
    if (!cv) throw new Error('OpenCV.js loaded but window.cv is missing');
    if (typeof cv.Mat === 'function') return cv; // runtime already up
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('OpenCV.js init timed out')), 30_000);
      cv.onRuntimeInitialized = () => {
        clearTimeout(timeout);
        resolve();
      };
    });
    return cv;
  })().catch((err) => {
    _cvReady = null; // don't poison future attempts — allow a retry on the next capture
    throw err;
  });
  return _cvReady;
}

const MIN_FLOW_POINTS = 12;

function cvHomographyToMat3(h: CvMat): Mat3 {
  const d = h.data64F;
  return [
    [d[0], d[1], d[2]],
    [d[3], d[4], d[5]],
    [d[6], d[7], d[8]],
  ];
}

// prev-frame px → curr-frame px homography from Lucas-Kanade flow of good corners, with
// RANSAC rejecting moving robots/people. Returns null when too few features track (caller
// holds the last pose). Mirrors on_device_cv.estimate_interframe_homography.
export function estimateInterframeMotion(cv: OpenCvLike, prevGray: CvMat, currGray: CvMat): Mat3 | null {
  const prevPts = new cv.Mat();
  const nextPts = new cv.Mat();
  const status = new cv.Mat();
  const err = new cv.Mat();
  const mask = new cv.Mat();
  const toDelete: CvMat[] = [prevPts, nextPts, status, err, mask];
  try {
    cv.goodFeaturesToTrack(prevGray, prevPts, 400, 0.01, 8, mask, 3);
    if (prevPts.rows < MIN_FLOW_POINTS) return null;
    const winSize = new cv.Size(21, 21);
    const criteria = new cv.TermCriteria(cv.TERM_CRITERIA_EPS | cv.TERM_CRITERIA_COUNT, 30, 0.01);
    cv.calcOpticalFlowPyrLK(prevGray, currGray, prevPts, nextPts, status, err, winSize, 3, criteria);

    const p0: number[] = [];
    const p1: number[] = [];
    for (let i = 0; i < status.rows; i++) {
      if (status.data[i]) {
        p0.push(prevPts.data32F[i * 2], prevPts.data32F[i * 2 + 1]);
        p1.push(nextPts.data32F[i * 2], nextPts.data32F[i * 2 + 1]);
      }
    }
    if (p0.length / 2 < MIN_FLOW_POINTS) return null;

    const src = cv.matFromArray(p0.length / 2, 1, cv.CV_32FC2, p0);
    const dst = cv.matFromArray(p1.length / 2, 1, cv.CV_32FC2, p1);
    toDelete.push(src, dst);
    const h = cv.findHomography(src, dst, cv.RANSAC, 3.0);
    toDelete.push(h);
    if (h.empty()) return null;
    return cvHomographyToMat3(h);
  } finally {
    for (const m of toDelete) {
      try {
        m.delete();
      } catch {
        // already freed
      }
    }
  }
}

export type CvPoseResolver = {
  resolve: (frameCanvas: HTMLCanvasElement) => Mat3 | null; // current carried image→field pose
  lostFrames: () => number;
  dispose: () => void;
};

// Build a per-frame pose resolver for MatchRecorder backed by OpenCV.js optical flow.
// The first frame returns the base pose; each later frame carries it by the measured
// inter-frame motion (or holds on flow loss). Manages the prev-grayscale Mat lifecycle.
export function createCvPoseResolver(cv: OpenCvLike, baseHomography: Mat3): CvPoseResolver {
  const stab = new StabilizedPose(baseHomography);
  let prevGray: CvMat | null = null;

  const toGray = (canvas: HTMLCanvasElement): CvMat | null => {
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const rgba = cv.matFromImageData(ctx.getImageData(0, 0, canvas.width, canvas.height));
    const gray = new cv.Mat();
    try {
      cv.cvtColor(rgba, gray, cv.COLOR_RGBA2GRAY);
      return gray;
    } catch (err) {
      gray.delete();
      throw err;
    } finally {
      rgba.delete();
    }
  };

  return {
    resolve(canvas) {
      const gray = toGray(canvas);
      if (!gray) return stab.homography;
      const previous = prevGray;
      if (previous) {
        let motion: Mat3 | null = null;
        try {
          motion = estimateInterframeMotion(cv, previous, gray);
        } catch {
          motion = null;
        } finally {
          previous.delete();
        }
        stab.update(motion); // null → holds pose, bumps lostFrames
      }
      prevGray = gray;
      return stab.homography;
    },
    lostFrames: () => stab.lostFrames,
    dispose() {
      prevGray?.delete();
      prevGray = null;
    },
  };
}
