import { useCallback, useEffect, useRef, useState } from 'react';

import { createDetector, detectRobots, type Detector } from './detector';
import { type Mat3 } from './homography';
import { type RawDetection } from './simpleTracker';

// Live match recorder: opens the back camera, samples frames at a target rate, runs the
// in-browser detector on each, and emits a CapturedFrame (timestamp + detections + the
// homography to project them with). The per-frame homography comes from `resolvePose`,
// injected so a static calibration (default) or an optical-flow-stabilized pose (step 4)
// can be swapped in without touching the recorder.

const MODEL_URL = String(
  import.meta.env.VITE_ONDEVICE_MODEL_URL || '/models/frc_robot_detector_v2.onnx',
);

export type CapturedFrame = { timeSec: number; detections: RawDetection[]; homography: Mat3 };

type Props = {
  // image-pixel -> field-metre homography to use for `frameCanvas` at `timeSec`
  resolvePose: (frameCanvas: HTMLCanvasElement, timeSec: number) => Mat3 | null;
  onFrame: (frame: CapturedFrame) => void;
  targetFps?: number;
  confThreshold?: number;
};

type DetectorState = 'idle' | 'loading' | 'ready' | 'error';

export function MatchRecorder({ resolvePose, onFrame, targetFps = 3, confThreshold = 0.35 }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const detectorRef = useRef<Detector | null>(null);
  const workCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const recordingRef = useRef(false);
  const startTimeRef = useRef(0);
  const elapsedRecordingMsRef = useRef(0);
  const mountedRef = useRef(true);
  const startRequestRef = useRef(0);
  const recordingRunRef = useRef(0);
  const loopTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [detectorState, setDetectorState] = useState<DetectorState>('idle');
  const [detectorError, setDetectorError] = useState('');
  const [recording, setRecording] = useState(false);
  const [frames, setFrames] = useState(0);
  const [detections, setDetections] = useState(0);
  const [cameraError, setCameraError] = useState('');

  const ensureDetector = useCallback(async (): Promise<Detector | null> => {
    if (detectorRef.current) return detectorRef.current;
    if (mountedRef.current) {
      setDetectorState('loading');
      setDetectorError('');
    }
    try {
      const det = await createDetector(MODEL_URL);
      detectorRef.current = det;
      if (mountedRef.current) setDetectorState('ready');
      return det;
    } catch (err) {
      if (mountedRef.current) {
        setDetectorState('error');
        setDetectorError(err instanceof Error ? err.message : 'Failed to load the detector model.');
      }
      return null;
    }
  }, []);

  const clearLoopTimeout = useCallback(() => {
    if (loopTimeoutRef.current === null) return;
    clearTimeout(loopTimeoutRef.current);
    loopTimeoutRef.current = null;
  }, []);

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  const stopCamera = useCallback(() => {
    if (recordingRef.current) {
      elapsedRecordingMsRef.current = Math.max(
        elapsedRecordingMsRef.current,
        performance.now() - startTimeRef.current,
      );
    }
    startRequestRef.current += 1;
    recordingRunRef.current += 1;
    recordingRef.current = false;
    clearLoopTimeout();
    releaseStream();
    if (mountedRef.current) setRecording(false);
  }, [clearLoopTimeout, releaseStream]);

  // Recursive timed loop with an in-flight guard, so a slow inference back-pressures the
  // sampler instead of piling up overlapping runs.
  const loop = useCallback(async (runId: number) => {
    const isCurrentRun = () =>
      mountedRef.current && recordingRef.current && recordingRunRef.current === runId;
    const detector = detectorRef.current;
    const video = videoRef.current;
    if (!isCurrentRun() || !detector || !video || !video.videoWidth) {
      if (isCurrentRun()) {
        loopTimeoutRef.current = setTimeout(() => {
          loopTimeoutRef.current = null;
          void loop(runId);
        }, 100);
      }
      return;
    }
    const w = video.videoWidth;
    const h = video.videoHeight;
    const canvas = (workCanvasRef.current ??= document.createElement('canvas'));
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, w, h);

    const timeSec = (performance.now() - startTimeRef.current) / 1000;
    const tStart = performance.now();
    try {
      const homography = resolvePose(canvas, timeSec);
      if (homography) {
        const boxes = await detectRobots(detector, canvas, w, h, { confThreshold });
        if (!isCurrentRun()) return;
        const dets: RawDetection[] = boxes.map((b) => ({
          bbox: [b.x1, b.y1, b.x2, b.y2],
          confidence: b.score,
        }));
        onFrame({ timeSec, detections: dets, homography });
        setFrames((n) => n + 1);
        setDetections((n) => n + dets.length);
      }
    } catch {
      // drop this frame; keep recording
    }
    if (!isCurrentRun()) return;
    const elapsed = performance.now() - tStart;
    const wait = Math.max(0, 1000 / targetFps - elapsed);
    loopTimeoutRef.current = setTimeout(() => {
      loopTimeoutRef.current = null;
      void loop(runId);
    }, wait);
  }, [confThreshold, onFrame, resolvePose, targetFps]);

  const start = useCallback(async () => {
    const requestId = ++startRequestRef.current;
    setCameraError('');
    const detector = await ensureDetector();
    if (!mountedRef.current || startRequestRef.current !== requestId) return;
    if (!detector) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      if (mountedRef.current && startRequestRef.current === requestId) {
        setCameraError('Camera not available on this device.');
      }
      return;
    }
    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' },
        audio: false,
      });
      if (!mountedRef.current || startRequestRef.current !== requestId) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      if (!mountedRef.current || startRequestRef.current !== requestId) {
        if (streamRef.current === stream) releaseStream();
        else stream.getTracks().forEach((t) => t.stop());
        return;
      }
      // Preserve a monotonic capture timeline when a scout pauses and resumes.
      startTimeRef.current = performance.now() - elapsedRecordingMsRef.current;
      const runId = recordingRunRef.current + 1;
      recordingRunRef.current = runId;
      recordingRef.current = true;
      setRecording(true);
      void loop(runId);
    } catch (err) {
      if (streamRef.current === stream) releaseStream();
      else stream?.getTracks().forEach((t) => t.stop());
      if (mountedRef.current && startRequestRef.current === requestId) {
        setCameraError(err instanceof Error ? err.message : 'Could not access the camera.');
      }
    }
  }, [ensureDetector, loop, releaseStream]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopCamera();
    };
  }, [stopCamera]);

  return (
    <div className="camera-capture">
      {detectorState === 'loading' ? <p className="muted">Loading detector model…</p> : null}
      {detectorState === 'error' ? (
        <p className="field-calibration__error">
          Detector unavailable: {detectorError}. Set VITE_ONDEVICE_MODEL_URL or place the model at{' '}
          {MODEL_URL}.
        </p>
      ) : null}
      {cameraError ? <p className="field-calibration__error">{cameraError}</p> : null}

      <video
        ref={videoRef}
        playsInline
        muted
        style={{ maxWidth: '100%', display: recording ? 'block' : 'none', borderRadius: 8 }}
      />

      <div className="odr-actions">
        {!recording ? (
          <button type="button" className="center-btn" onClick={() => void start()} disabled={detectorState === 'loading'}>
            Start recording
          </button>
        ) : (
          <button type="button" className="center-btn ghost" onClick={stopCamera}>
            Stop recording
          </button>
        )}
      </div>

      {recording || frames > 0 ? (
        <p className="muted">
          {frames} frames · {detections} detections{recording ? ' · recording…' : ' captured'}
        </p>
      ) : null}
    </div>
  );
}
