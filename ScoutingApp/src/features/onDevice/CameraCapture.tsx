import { useCallback, useEffect, useRef, useState } from 'react';

// Live camera preview + still-frame capture for the on-device flow. A scout points the
// phone at the field and captures a frame to calibrate against (or, later, records the
// match). Falls back gracefully where getUserMedia is unavailable — the calibration page
// still accepts a file upload. (Not headless-testable: no camera in CI/jsdom.)

type Props = {
  // Receives a JPEG data URL of the captured frame.
  onCapture: (dataUrl: string) => void;
};

export function CameraCapture({ onCapture }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const mountedRef = useRef(true);
  const startRequestRef = useRef(0);
  const [active, setActive] = useState(false);
  const [error, setError] = useState('');

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  const stop = useCallback(() => {
    startRequestRef.current += 1;
    releaseStream();
    if (!mountedRef.current) return;
    setActive(false);
  }, [releaseStream]);

  const start = useCallback(async () => {
    const requestId = ++startRequestRef.current;
    setError('');
    if (!navigator.mediaDevices?.getUserMedia) {
      if (mountedRef.current && startRequestRef.current === requestId) {
        setError('Camera not available here — upload a frame instead.');
      }
      return;
    }
    let stream: MediaStream | null = null;
    try {
      // back camera on phones; falls back to any camera
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
      setActive(true);
    } catch (err) {
      if (streamRef.current === stream) releaseStream();
      else stream?.getTracks().forEach((t) => t.stop());
      if (mountedRef.current && startRequestRef.current === requestId) {
        setError(err instanceof Error ? err.message : 'Could not access the camera.');
      }
    }
  }, [releaseStream]);

  const capture = useCallback(() => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(video, 0, 0);
    onCapture(canvas.toDataURL('image/jpeg', 0.92));
  }, [onCapture]);

  // release the camera on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      startRequestRef.current += 1;
      releaseStream();
    };
  }, [releaseStream]);

  return (
    <div className="camera-capture">
      {error ? <p className="field-calibration__error">{error}</p> : null}
      <video
        ref={videoRef}
        playsInline
        muted
        style={{ maxWidth: '100%', display: active ? 'block' : 'none', borderRadius: 8 }}
      />
      <div className="odr-actions">
        {!active ? (
          <button type="button" className="center-btn ghost" onClick={start}>
            Use camera
          </button>
        ) : (
          <>
            <button type="button" className="center-btn" onClick={capture}>
              Capture frame
            </button>
            <button type="button" className="center-btn ghost" onClick={stop}>
              Stop camera
            </button>
          </>
        )}
      </div>
    </div>
  );
}
