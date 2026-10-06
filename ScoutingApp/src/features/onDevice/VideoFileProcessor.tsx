import { useCallback, useEffect, useRef, useState } from 'react';

import { summarizeInferenceTelemetry, type InferenceTelemetry } from './benchmark';
import {
  createDeviceDetector,
  detectRobots,
  ON_DEVICE_MODEL_URL,
  type Detector,
} from './detector';
import { SEASON } from '../../config/season';
import { readBumperColour } from './bumperColour';
import { captureRobotThumbs } from './robotThumb';
import { sampleFramesWithWebCodecs, WebCodecsUnsupported } from './webcodecsFrames';
import { fieldRoi } from './fieldCrop';
import { type Mat3 } from './homography';
import { type CapturedFrame } from './MatchRecorder';
import {
  advanceSampleTarget,
  matchWindow,
  safePlaybackRate,
  SPEEDUP_WARMUP_SAMPLES,
  stoppedEarly,
} from './samplingRate';
import { type RawDetection } from './simpleTracker';

// Offline desktop path: upload a match clip and run the same on-device pipeline over
// sampled frames — no camera or second screen needed. Seeks the video at a target rate,
// runs the in-browser detector on each frame, and emits the same CapturedFrame the live
// recorder does, so identify → sync is identical. Static calibration per frame (a clip is
// one fixed camera view); the optical-flow carry is for the shaky handheld camera path.

const MAX_FRAMES = 900; // safety cap (~3 min at 5 fps)
// One frame's detection normally takes well under a second. A frame that never settles
// (seen in WebKit: a promise that never resolved, the page idle at 0% CPU) is skipped
// rather than parking the whole breakdown -- the stall watchdog below only runs between
// frames, so it could not catch this.
const SAMPLE_TIMEOUT_MS = 20_000;
async function withTimeout(work: Promise<void>, ms: number): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  try {
    await Promise.race([
      work,
      new Promise<void>((_, reject) => {
        timer = setTimeout(() => reject(new Error('sample timed out')), ms);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// WebKit can drop a video's blob source partway through a long breakdown (seen every
// ~1.5-3 minutes under detection load); playback then stalls and the frame loop ends as
// if the match were over. Reopen the same file and carry on from the last frame, giving
// up only after this many reopens in a row that made no progress.
const MAX_STALLED_REOPENS = 3;

const throwIfAborted = (signal?: AbortSignal) => {
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
};

type Props = {
  // image-px -> field-metre homography for a sampled frame (static base calibration)
  resolvePose: (frameCanvas: HTMLCanvasElement, timeSec: number) => Mat3 | null;
  onFrame: (frame: CapturedFrame) => void;
  // coveredUntilSec: match time the breakdown reached when the rest of the video could not
  // be read (null when it covered the whole match).
  onComplete: (result: { coveredUntilSec: number | null }) => void;
  onTelemetry?: (telemetry: InferenceTelemetry) => void;
  targetFps?: number;
  confThreshold?: number;
  // Video timestamp where the match starts; only the match itself is processed.
  matchStartSec?: number;
  // Lets the parent lock the capture-source switch while frames are being captured.
  onRecordingChange?: (busy: boolean) => void;
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
  onSpeedup?: (rate: number) => void,
  window: { startSec: number; endSec: number } = {
    startSec: 0,
    endSec: Infinity,
  },
): Promise<void> {
  throwIfAborted(signal);
  if (typeof video.requestVideoFrameCallback === 'function') {
    if (window.startSec > 0) {
      await new Promise<void>((resolve) => {
        const done = () => {
          video.removeEventListener('seeked', done);
          resolve();
        };
        video.addEventListener('seeked', done);
        setTimeout(done, 5000); // a seek that never reports still lets sampling start
        video.currentTime = window.startSec;
      });
    }
    // Play continuously and sample presented frames. We do NOT pause during detection
    // (pause/resume races hang the pipeline); instead a busy-flag drops frames that
    // arrive mid-inference, and each kept frame is drawn synchronously in onSample
    // before any await, so it's captured before playback advances.
    // Sample against a fixed grid rather than "interval since the frame we last took".
    // A presented frame almost never lands exactly on a boundary, so anchoring the next
    // target to the frame actually sampled folds that overshoot in every time and the
    // error compounds -- over a full match that silently cost 11 of 495 samples, and the
    // gaps it opened cost whole tracks.
    let nextTarget = -Infinity;
    let count = 0;
    let busy = false;
    const sampleCostsMs: number[] = [];
    let speedupDecided = false;
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
        if (t > window.endSec) return finish();
        if (!busy && t >= window.startSec && t >= nextTarget && count < maxFrames) {
          busy = true;
          nextTarget = advanceSampleTarget(nextTarget, t, intervalSec);
          const sampleStartedAt = performance.now();
          try {
            // draws synchronously, then awaits detection
            await withTimeout(onSample(t), SAMPLE_TIMEOUT_MS);
          } catch {
            /* skip this frame */
          }
          lastFrameAt = Date.now();
          sampleCostsMs.push(performance.now() - sampleStartedAt);
          if (signal?.aborted) return finish();
          count += 1;
          busy = false;
          if (!speedupDecided && sampleCostsMs.length >= SPEEDUP_WARMUP_SAMPLES) {
            speedupDecided = true;
            const rate = safePlaybackRate(intervalSec, sampleCostsMs);
            if (rate > 1) {
              video.playbackRate = rate;
              onSpeedup?.(rate);
            }
          }
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
  const end = Math.min(video.duration, window.endSec);
  for (let t = window.startSec, n = 0; t < end && n < maxFrames; t += intervalSec, n += 1) {
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
    await withTimeout(onSample(t), SAMPLE_TIMEOUT_MS).catch(() => {});
  }
}

async function reopenSource(
  video: HTMLVideoElement,
  file: File,
  signal: AbortSignal,
): Promise<string> {
  const url = URL.createObjectURL(file);
  video.src = url;
  await new Promise<void>((resolve, reject) => {
    const done = (err?: Error) => {
      clearTimeout(timer);
      video.removeEventListener('loadeddata', onLoaded);
      video.removeEventListener('error', onError);
      signal.removeEventListener('abort', onAbort);
      if (err) reject(err);
      else resolve();
    };
    const onLoaded = () => done();
    const onError = () => done(new Error('could not reopen the video'));
    const onAbort = () => done(new DOMException('Aborted', 'AbortError'));
    const timer = setTimeout(() => done(new Error('reopening the video timed out')), 10_000);
    video.addEventListener('loadeddata', onLoaded);
    video.addEventListener('error', onError);
    signal.addEventListener('abort', onAbort, { once: true });
  });
  return url;
}


export function VideoFileProcessor({
  resolvePose,
  onFrame,
  onComplete,
  onTelemetry,
  targetFps = 3, // 2 fps measurably lost positions against a dense 10 fps run of the same match
  // Unset: the loaded model's own cutoff (modelArtifact.ts), tuned per model.
  confThreshold,
  matchStartSec = 0,
  onRecordingChange,
}: Props) {
  const detectorRef = useRef<Detector | null>(null);
  const mountedRef = useRef(true);
  const activeAbortRef = useRef<AbortController | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'processing' | 'done' | 'error'>('idle');
  const [error, setError] = useState('');
  const busyForParent = status === 'loading' || status === 'processing';
  const onRecordingChangeRef = useRef(onRecordingChange);
  onRecordingChangeRef.current = onRecordingChange;
  useEffect(() => {
    onRecordingChangeRef.current?.(busyForParent);
  }, [busyForParent]);
  useEffect(() => () => onRecordingChangeRef.current?.(false), []);
  const [fileName, setFileName] = useState('');
  const [progress, setProgress] = useState({
    frames: 0,
    detections: 0,
    pct: 0,
  });
  const [speedup, setSpeedup] = useState(1);

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
      setSpeedup(1);
      let detector = detectorRef.current;
      if (!detector) {
        try {
          detector = await createDeviceDetector();
          detectorRef.current = detector;
        } catch (err) {
          if (isCurrentJob()) {
            setStatus('error');
            setError(
              `Detector unavailable: ${err instanceof Error ? err.message : 'load failed'}. Set VITE_ONDEVICE_MODEL_URL or place the model at ${ON_DEVICE_MODEL_URL}.`,
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
      let objectUrl = URL.createObjectURL(file);
      video.preload = 'auto';
      document.body.appendChild(video);
      try {
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => {
            cleanup();
            reject(new Error('Video did not load. Choose the file again.'));
          }, 15_000);
          const cleanup = () => {
            clearTimeout(timer);
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
          abortController.signal.addEventListener('abort', onAbort, {
            once: true,
          });
          if (abortController.signal.aborted) onAbort();
          else {
            // Install listeners before starting decode; WKWebView needs an explicit load.
            video.src = objectUrl;
            video.load();
          }
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
        const inferenceSamples: number[] = [];
        const window = matchWindow(matchStartSec, duration);
        const windowSec = Math.max(1e-6, window.endSec - window.startSec);
        const intervalSec = 1 / targetFps;
        let lastSampleSec = -Infinity;
        let resumeFromSec = window.startSec;
        const processSample = async (t: number, source: CanvasImageSource) => {
          throwIfAborted(abortController.signal);
          lastSampleSec = t;
          ctx.drawImage(source, 0, 0, w, h);
          const homography = resolvePose(canvas, t);
          if (!homography) return;
          const inferenceStarted = performance.now();
          const roi = fieldRoi(homography, w, h, SEASON.fieldLengthM, SEASON.fieldWidthM);
          const boxes = await detectRobots(detector, canvas, w, h, {
            confThreshold: confThreshold ?? detector.confThreshold,
            roi,
          });
          inferenceSamples.push(performance.now() - inferenceStarted);
          onTelemetry?.(
            summarizeInferenceTelemetry(
              inferenceSamples,
              detector.modelVersion,
              detector.executionProvider,
            ),
          );
          if (!isCurrentJob()) return;
          const dets: RawDetection[] = boxes.map((b) => {
            const bbox: [number, number, number, number] = [b.x1, b.y1, b.x2, b.y2];
            // the canvas still holds this frame: it is redrawn only for the next sample
            return { bbox, confidence: b.score, colour: readBumperColour(ctx, bbox, w, h) };
          });
          const thumbs = await captureRobotThumbs(canvas, dets.map((d) => d.bbox));
          thumbs.forEach((thumb, i) => {
            if (thumb) dets[i].thumb = thumb;
          });
          if (!isCurrentJob()) return;
          onFrame({ timeSec: t, detections: dets, homography });
          frames += 1;
          detections += dets.length;
          const pct = Math.round(((t - window.startSec) / windowSec) * 100);
          setProgress({
            frames,
            detections,
            pct: Math.max(0, Math.min(100, pct)),
          });
        };

        // Decode the file directly when the device can; it isn't held to playback speed.
        // Unsupported (or failing partway), the playback loop below takes over from the
        // last sampled frame.
        let decodedDirectly = false;
        try {
          await sampleFramesWithWebCodecs(file, {
            startSec: window.startSec,
            endSec: window.endSec,
            intervalSec,
            signal: abortController.signal,
            onFrame: (frame, t) => processSample(t, frame),
          });
          decodedDirectly = true;
        } catch (err) {
          throwIfAborted(abortController.signal);
          if (!(err instanceof WebCodecsUnsupported)) console.warn('[on-device] direct decode stopped:', err);
        }
        if (Number.isFinite(lastSampleSec)) resumeFromSec = Math.max(resumeFromSec, lastSampleSec + intervalSec);
        let stalledReopens = 0;
        let coveredUntilSec: number | null = null;
        while (!decodedDirectly || stoppedEarly(lastSampleSec, window.endSec, intervalSec)) {
          decodedDirectly = false;
          const reachedSec = lastSampleSec;
          try {
            await sampleFrames(
              video,
              intervalSec,
              MAX_FRAMES - frames,
              (t) => processSample(t, video),
              abortController.signal,
              (rate) => {
                if (isCurrentJob()) setSpeedup(rate);
              },
              { startSec: resumeFromSec, endSec: window.endSec },
            );
          } catch (err) {
            if (!(err instanceof Error) || err.message !== 'video decode error') throw err;
          }
          throwIfAborted(abortController.signal);
          if (frames >= MAX_FRAMES || !stoppedEarly(lastSampleSec, window.endSec, intervalSec))
            break;
          stalledReopens = lastSampleSec > reachedSec ? 0 : stalledReopens + 1;
          if (stalledReopens >= MAX_STALLED_REOPENS) {
            coveredUntilSec = Math.max(0, lastSampleSec - window.startSec);
            break;
          }
          const staleUrl = objectUrl;
          objectUrl = await reopenSource(video, file, abortController.signal);
          URL.revokeObjectURL(staleUrl);
          resumeFromSec = Math.max(resumeFromSec, lastSampleSec + intervalSec);
        }

        if (isCurrentJob()) {
          setProgress((p) => ({ ...p, pct: 100 }));
          setStatus('done');
          onComplete({ coveredUntilSec });
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
    [confThreshold, matchStartSec, onComplete, onFrame, onTelemetry, resolvePose, targetFps],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      activeAbortRef.current?.abort();
    };
  }, []);

  const busy = status === 'loading' || status === 'processing';
  return (
    <div className="video-file-processor">
      {/* The bare file input is wider than a 250px phone and looks unlike the
          rest of the flow, so the button is the visible control. */}
      <div className="odr-actions">
        <label className="center-btn ghost odr-file-btn" aria-disabled={busy || undefined}>
          {fileName ? 'Choose another video' : 'Choose a video'}
          <input
            type="file"
            accept="video/*"
            className="odr-file-input"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) {
                setFileName(file.name);
                void process(file);
              }
            }}
          />
        </label>
      </div>
      {fileName ? <p className="muted odr-file-name">{fileName}</p> : null}
      {status === 'loading' ? <p className="muted">Loading detector model…</p> : null}
      {status === 'processing' ? (
        <p className="muted">
          Processing… {progress.pct}% · {progress.frames} frames · {progress.detections} detections
          {speedup > 1 ? ` · ${speedup}× speed` : ''}
        </p>
      ) : null}
      {status === 'done' ? (
        <p className="muted">
          Done — {progress.frames} frames · {progress.detections} detections
          {speedup > 1 ? ` (${speedup}× speed)` : ''}. Continue to identify.
        </p>
      ) : null}
      {error ? <p className="field-calibration__error">{error}</p> : null}
    </div>
  );
}
