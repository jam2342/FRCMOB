import { useCallback, useEffect, useRef, useState } from 'react';

import { CameraCapture } from './CameraCapture';
import { FIELD_LENGTH_M, FIELD_WIDTH_M } from './fieldZones';
import { type Calibration, type Point, calibrateFromTaps, fieldReferenceCorners, projectPoint } from './homography';
import { openDb, saveCalibration } from './offlineStore';
import { grayscaleFromRgba, type GrayImage } from './opticalFlowCore';

// The 4 taps, in order, with field-relative prompts (origin = blue-station x scoring-table
// corner; +X toward red station, +Y toward the audience side — per game_config.field_layout).
const CORNER_PROMPTS = [
  'Blue-station × scoring-table corner',
  'Red-station × scoring-table corner',
  'Red-station × audience-side corner',
  'Blue-station × audience-side corner',
];

const FIELD_CORNERS = fieldReferenceCorners(FIELD_LENGTH_M, FIELD_WIDTH_M);

type Props = {
  // Called when a calibration is accepted, so a parent flow can keep the homography.
  onCalibrated?: (calibration: CalibrationCapture) => void;
};

export type CalibrationCapture = Calibration & { referenceFrame: GrayImage };

export function FieldCalibration({ onCalibrated }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const mountedRef = useRef(true);
  const imageLoadRef = useRef(0);
  const pendingObjectUrlRef = useRef<string | null>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [taps, setTaps] = useState<Point[]>([]);
  const [calibration, setCalibration] = useState<Calibration | null>(null);
  const [error, setError] = useState<string>('');

  const revokePendingObjectUrl = useCallback(() => {
    if (!pendingObjectUrlRef.current) return;
    URL.revokeObjectURL(pendingObjectUrlRef.current);
    pendingObjectUrlRef.current = null;
  }, []);

  const loadFromSrc = useCallback((src: string, objectUrl?: string) => {
    const loadId = ++imageLoadRef.current;
    revokePendingObjectUrl();
    if (objectUrl) pendingObjectUrlRef.current = objectUrl;

    const finishObjectUrl = () => {
      if (!objectUrl || pendingObjectUrlRef.current !== objectUrl) return;
      URL.revokeObjectURL(objectUrl);
      pendingObjectUrlRef.current = null;
    };
    const img = new Image();
    img.onload = () => {
      finishObjectUrl();
      if (!mountedRef.current || imageLoadRef.current !== loadId) return;
      setImage(img);
      setTaps([]);
      setCalibration(null);
      setError('');
    };
    img.onerror = () => {
      finishObjectUrl();
      if (!mountedRef.current || imageLoadRef.current !== loadId) return;
      setError('Could not load that image.');
    };
    img.src = src;
  }, [revokePendingObjectUrl]);

  const handleFile = useCallback(
    (file: File | undefined) => {
      if (!file) return;
      const url = URL.createObjectURL(file);
      loadFromSrc(url, url);
    },
    [loadFromSrc],
  );

  const handleCanvasClick = useCallback(
    (event: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      if (!canvas || !image || taps.length >= 4) return;
      const rect = canvas.getBoundingClientRect();
      // CSS click -> image pixel coords (robust to any CSS scaling of the canvas).
      const ix = ((event.clientX - rect.left) / rect.width) * image.naturalWidth;
      const iy = ((event.clientY - rect.top) / rect.height) * image.naturalHeight;
      const next = [...taps, { x: ix, y: iy }];
      setTaps(next);
      if (next.length === 4) {
        try {
          const cal = calibrateFromTaps(next, FIELD_CORNERS);
          setCalibration(cal);
          setError('');
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Calibration failed.');
        }
      }
    },
    [image, taps],
  );

  const reset = useCallback(() => {
    setTaps([]);
    setCalibration(null);
    setError('');
  }, []);

  // Redraw whenever the image, taps, or calibration change.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !image) return;
    const cw = image.naturalWidth;
    const ch = image.naturalHeight;
    canvas.width = cw;
    canvas.height = ch;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(image, 0, 0, cw, ch);
    const s = cw / image.naturalWidth; // image px -> canvas px

    // verification overlay: project the field grid back onto the frame via the inverse
    // homography. If these lines hug the real field, the calibration is good.
    if (calibration) {
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.9)';
      const toCanvas = (fx: number, fy: number) => {
        const p = projectPoint(calibration.inverse, fx, fy);
        return { x: p.x * s, y: p.y * s };
      };
      const drawLine = (a: Point, b: Point) => {
        const pa = toCanvas(a.x, a.y);
        const pb = toCanvas(b.x, b.y);
        ctx.beginPath();
        ctx.moveTo(pa.x, pa.y);
        ctx.lineTo(pb.x, pb.y);
        ctx.stroke();
      };
      for (let gx = 0; gx <= FIELD_LENGTH_M + 0.01; gx += 2) {
        drawLine({ x: gx, y: 0 }, { x: gx, y: FIELD_WIDTH_M });
      }
      for (let gy = 0; gy <= FIELD_WIDTH_M + 0.01; gy += 2) {
        drawLine({ x: 0, y: gy }, { x: FIELD_LENGTH_M, y: gy });
      }
      ctx.strokeStyle = 'rgba(250, 204, 21, 1)';
      ctx.lineWidth = 2.5;
      for (let i = 0; i < FIELD_CORNERS.length; i++) {
        drawLine(FIELD_CORNERS[i], FIELD_CORNERS[(i + 1) % FIELD_CORNERS.length]);
      }
    }

    // tap markers + numbers
    taps.forEach((tap, i) => {
      const cx = tap.x * s;
      const cy = tap.y * s;
      ctx.fillStyle = '#ef4444';
      ctx.beginPath();
      ctx.arc(cx, cy, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 13px sans-serif';
      ctx.fillText(String(i + 1), cx + 8, cy - 8);
    });
  }, [image, taps, calibration]);

  const accept = useCallback(async () => {
    if (!calibration || !image) return;
    const referenceCanvas = document.createElement('canvas');
    referenceCanvas.width = image.naturalWidth;
    referenceCanvas.height = image.naturalHeight;
    const referenceContext = referenceCanvas.getContext('2d');
    if (!referenceContext) {
      setError('Could not save the calibration frame. Please re-calibrate.');
      return;
    }
    referenceContext.drawImage(image, 0, 0, referenceCanvas.width, referenceCanvas.height);
    const pixels = referenceContext.getImageData(0, 0, referenceCanvas.width, referenceCanvas.height);
    const referenceFrame = grayscaleFromRgba(pixels.data, referenceCanvas.width, referenceCanvas.height);
    // persist locally so the offline breakdown can reuse it without re-tapping
    try {
      const db = await openDb();
      try {
        await saveCalibration(db, {
          id: 'current',
          homography: calibration.homography,
          createdAt: Date.now(),
          calibrationVersion: 'manual_corners_v1',
          rmseM: calibration.rmseM,
          verified: true,
          imageWidth: referenceFrame.width,
          imageHeight: referenceFrame.height,
          referenceGray: referenceFrame.data,
        });
      } finally {
        db.close();
      }
    } catch {
      // non-fatal: calibration still usable in-memory if IndexedDB is unavailable
    }
    onCalibrated?.({ ...calibration, referenceFrame });
  }, [calibration, image, onCalibrated]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      imageLoadRef.current += 1;
      revokePendingObjectUrl();
    };
  }, [revokePendingObjectUrl]);

  return (
    <div className="field-calibration">
      <p className="muted">
        Record a frame of the field, then tap the four field corners in order. This fixes the
        image→field mapping so the on-device breakdown can place robots in real field coordinates —
        no AprilTags needed (they aren&apos;t readable from the stands).
      </p>

      <div className="odr-actions">
        <label className="center-btn ghost odr-file-btn">
          Upload field photo
          <input
            type="file"
            accept="image/*"
            className="odr-file-input"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
        </label>
      </div>
      <CameraCapture onCapture={loadFromSrc} />

      {image ? (
        <>
          <p className="field-calibration__prompt">
            {calibration
              ? '✓ Calibrated — check the grid hugs the field. If it drifts, Reset and re-tap.'
              : `Tap corner ${taps.length + 1} of 4: ${CORNER_PROMPTS[taps.length]}`}
          </p>
          <canvas
            ref={canvasRef}
            onClick={handleCanvasClick}
            style={{ maxWidth: '100%', cursor: calibration ? 'default' : 'crosshair', touchAction: 'none' }}
          />
          <div className="odr-actions">
            <button type="button" className="center-btn ghost" onClick={reset} disabled={taps.length === 0}>
              Reset taps
            </button>
            <button type="button" className="center-btn" onClick={() => void accept()} disabled={!calibration}>
              Use this calibration
            </button>
          </div>
        </>
      ) : (
        <p className="muted">Upload or capture a field photo to begin.</p>
      )}

      {error ? <p className="field-calibration__error">{error}</p> : null}
    </div>
  );
}
