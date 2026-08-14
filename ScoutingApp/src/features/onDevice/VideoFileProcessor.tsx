import { useCallback, useEffect, useRef, useState } from 'react';

import { createDetector, detectRobots, type Detector } from './detector';
import { type Mat3 } from './homography';
import { type CapturedFrame } from './MatchRecorder';
import { type RawDetection } from './simpleTracker';

// Offline desktop path: upload a match clip and run the same on-device pipeline over
// sampled frames — no camera or second screen needed. Seeks the video at a target rate,
// runs the in-browser detector on each frame, and emits the same CapturedFrame the live
// recorder does, so identify → sync is identical. Static calibration per frame (a clip is
// one fixed camera view); the optical-flow carry is for the shaky handheld camera path.

const MODEL_URL = String(
  import.meta.env.VITE_ONDEVICE_MODEL_URL || '/models/frc_robot_detector_v2.onnx',
);
const MAX_FRAMES = 900; // safety cap (~3 min at 5 fps)

const throwIfAborted = (signal?: AbortSignal) => {
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
};

type Props = {
  // image-px -> field-metre homography for a sampled frame (static base calibration)
  resolvePose: (frameCanvas: HTMLCanvasElement, timeSec: number) => Mat3 | null;
  onFrame: (frame: CapturedFrame) => void;
  onComplete: () => void;
  targetFps?: number;
  confThreshold?: number;
};

type VideoWithRvfc = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number;
};

// Sample frames from a clip and run `onSample(timeSec)` on each, ~every intervalSec of
// media time. Plays the video (playback forces decode + present — a detached/paused video
// draws blank frames in some browsers/headless) and grabs each presented frame via
// requestVideoFrameCallback, pausing during detection so a slow model back-pressures the
// sampler. Falls back to seek-based sampling where rVFC is unavailable.
async function sampleFrames(
  video: VideoWithRvfc,
  intervalSec: number,
  maxFrames: number,
  onSample: (timeSec: number) => Promise<void>,
  signal?: AbortSignal,
): Promise<void> {
  throwIfAborted(signal);
  if (typeof video.requestVideoFrameCallback === 'function') {
    // Play continuously and sample presented frames. We do NOT pause during detection
    // (pause/resume races hang the pipeline); instead a busy-flag drops frames that
    // arrive mid-inference, and each kept frame is drawn synchronously in onSample
    // before any await, so it's captured before playback advances.
    let last = -Infinity;
    let count = 0;
    let busy = false;
    await video.play().catch(() => {});
    await new Promise<void>((resolve, reject) => {
      let finished = false;
      let lastFrameAt = Date.now();
      let watchdog: ReturnType<typeof setInterval> | null = null;
      const cleanup = () => {
        if (watchdog) {
          clearInterval(watchdog);
          watchdog = null;
        }
        video.removeEventListener('ended', finish);
        video.removeEventListener('error', fail);
        signal?.removeEventListener('abort', abort);
      };
      const finish = () => {
        if (finished) return;
        finished = true;
        cleanup();
        try {
          video.pause();
        } catch {
          // cleanup must continue even if the media element is already torn down
        }
        resolve();
      };
      const abort = () => finish();
      const fail = () => {
        if (finished) return;
        finished = true;
        cleanup();
        reject(new Error('video decode error'));
      };
      // Safety: if no frame is presented for a while (playback stalled / no 'ended'
      // in headless), stop rather than hang forever.
      watchdog = setInterval(() => {
        if (!busy && Date.now() - lastFrameAt > 4000) finish();
      }, 1000);
      video.addEventListener('ended', finish);
      video.addEventListener('error', fail);
      signal?.addEventListener('abort', abort, { once: true });
      const step = async (_now: number, meta: { mediaTime: number }) => {
        if (finished) return;
        if (signal?.aborted) return finish();
        lastFrameAt = Date.now();
        const t = meta.mediaTime;
        if (!busy && t - last >= intervalSec && count < maxFrames) {
          busy = true;
          last = t;
          try {
            await onSample(t); // draws synchronously, then awaits detection
          } catch {
            /* skip this frame */
          }
          if (signal?.aborted) return finish();
          count += 1;
          busy = false;
          if (count >= maxFrames) return finish();
        }
        if (finished) return;
        if (video.ended) finish();
        else video.requestVideoFrameCallback!(step);
      };
      if (signal?.aborted) return finish();
      video.requestVideoFrameCallback!(step);
    });
    throwIfAborted(signal);
    return;
  }

  // Fallback: seek-based (no rVFC). Resolve on 'seeked' + a rAF tick for paint.
  const duration = video.duration;
  for (let t = 0, n = 0; t < duration && n < maxFrames; t += intervalSec, n += 1) {
    throwIfAborted(signal);
    await new Promise<void>((resolve) => {
      let raf1: number | null = null;
      let raf2: number | null = null;
      const cleanup = () => {
        video.removeEventListener('seeked', onSeeked);
        signal?.removeEventListener('abort', onAbort);
        if (raf1 !== null) cancelAnimationFrame(raf1);
        if (raf2 !== null) cancelAnimationFrame(raf2);
      };
      const onAbort = () => {
        cleanup();
        resolve();
      };
      const onSeeked = () => {
        video.removeEventListener('seeked', onSeeked);
        raf1 = requestAnimationFrame(() => {
          raf2 = requestAnimationFrame(() => {
            cleanup();
            resolve();
          });
        });
      };
      video.addEventListener('seeked', onSeeked);
      signal?.addEventListener('abort', onAbort, { once: true });
      if (signal?.aborted) return onAbort();
      video.currentTime = t;
    });
    throwIfAborted(signal);
    await onSample(t);
  }
}

export function VideoFileProcessor({
  resolvePose,
  onFrame,
  onComplete,
  targetFps = 5,
  confThreshold = 0.35,
}: Props) {
  const detectorRef = useRef<Detector | null>(null);
  const mountedRef = useRef(true);
  const activeAbortRef = useRef<AbortController | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'processing' | 'done' | 'error'>('idle');
  const [error, setError] = useState('');
  const [progress, setProgress] = useState({ frames: 0, detections: 0, pct: 0 });

  const process = useCallback(
    async (file: File) => {
      activeAbortRef.current?.abort();
      const abortController = new AbortController();
      activeAbortRef.current = abortController;
      const isCurrentJob = () =>
        mountedRef.current && activeAbortRef.current === abortController && !abortController.signal.aborted;

      setError('');
      setStatus('loading');
      setProgress({ frames: 0, detections: 0, pct: 0 });
      let detector = detectorRef.current;
      if (!detector) {
        try {
          detector = await createDetector(MODEL_URL);
          detectorRef.current = detector;
        } catch (err) {
          if (isCurrentJob()) {
            setStatus('error');
            setError(
              `Detector unavailable: ${err instanceof Error ? err.message : 'load failed'}. Set VITE_ONDEVICE_MODEL_URL or place the model at ${MODEL_URL}.`,
            );
          }
          return;
        }
      }
      if (!isCurrentJob()) return;

      const video = document.createElement('video') as VideoWithRvfc;
      video.muted = true;
      video.playsInline = true;
      // Off-screen but attached: a fully-detached video may not decode/paint in some
      // browsers, leaving drawImage blank.
      video.style.cssText = 'position:fixed;left:-9999px;width:1px;height:1px;opacity:0';
      const objectUrl = URL.createObjectURL(file);
      video.src = objectUrl;
      document.body.appendChild(video);
      try {
        await new Promise<void>((resolve, reject) => {
          const cleanup = () => {
            video.removeEventListener('loadeddata', onLoaded);
            video.removeEventListener('error', onError);
            abortController.signal.removeEventListener('abort', onAbort);
          };
          const onLoaded = () => {
            cleanup();
            resolve();
          };
          const onError = () => {
            cleanup();
            reject(new Error('could not read that video file'));
          };
          const onAbort = () => {
            cleanup();
            reject(new DOMException('Aborted', 'AbortError'));
          };
          video.addEventListener('loadeddata', onLoaded);
          video.addEventListener('error', onError);
          abortController.signal.addEventListener('abort', onAbort, { once: true });
          if (abortController.signal.aborted) onAbort();
        });
        throwIfAborted(abortController.signal);
        const w = video.videoWidth;
        const h = video.videoHeight;
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        if (!w || !h || duration <= 0) throw new Error('video has no decodable frames');

        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        if (!ctx) throw new Error('2d canvas context unavailable');

        if (isCurrentJob()) setStatus('processing');
        let frames = 0;
        let detections = 0;
        await sampleFrames(video, 1 / targetFps, MAX_FRAMES, async (t) => {
          throwIfAborted(abortController.signal);
          ctx.drawImage(video, 0, 0, w, h);
          const homography = resolvePose(canvas, t);
          if (!homography) return;
          const boxes = await detectRobots(detector, canvas, w, h, { confThreshold });
          if (!isCurrentJob()) return;
          const dets: RawDetection[] = boxes.map((b) => ({
            bbox: [b.x1, b.y1, b.x2, b.y2],
            confidence: b.score,
          }));
          onFrame({ timeSec: t, detections: dets, homography });
          frames += 1;
          detections += dets.length;
          setProgress({ frames, detections, pct: Math.min(100, Math.round((t / duration) * 100)) });
        }, abortController.signal);

        if (isCurrentJob()) {
          setProgress((p) => ({ ...p, pct: 100 }));
          setStatus('done');
          onComplete();
        }
      } catch (err) {
        if (!abortController.signal.aborted && isCurrentJob()) {
          setStatus('error');
          setError(err instanceof Error ? err.message : 'video processing failed');
        }
      } finally {
        try {
          video.pause();
        } catch {
          // cleanup must continue even if the media element is already torn down
        }
        URL.revokeObjectURL(objectUrl);
        video.remove();
        if (activeAbortRef.current === abortController) activeAbortRef.current = null;
      }
    },
    [confThreshold, onComplete, onFrame, resolvePose, targetFps],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      activeAbortRef.current?.abort();
    };
  }, []);

  return (
    <div className="video-file-processor">
      <label className="field-calibration__file">
        <input
          type="file"
          accept="video/*"
          disabled={status === 'loading' || status === 'processing'}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void process(file);
          }}
        />
      </label>
      {status === 'loading' ? <p className="muted">Loading detector model…</p> : null}
      {status === 'processing' ? (
        <p className="muted">
          Processing… {progress.pct}% · {progress.frames} frames · {progress.detections} detections
        </p>
      ) : null}
      {status === 'done' ? (
        <p className="muted">
          Done — {progress.frames} frames · {progress.detections} detections. Continue to identify.
        </p>
      ) : null}
      {error ? <p className="field-calibration__error">{error}</p> : null}
    </div>
  );
}
