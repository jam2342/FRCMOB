import { useCallback, useEffect, useRef, useState } from 'react';
import QRCode from 'qrcode';
import type { SavedScoutingEntry } from './scoutingPage.types';
import { normalizeEntry } from './scoutingPage.helpers';
import {
  encodeEntryForQr,
  decodeEntryFromQr,
  encodeRoomKeyForQr,
  isQrEntryString,
  parseRoomKeyFromQr,
} from './qrUtils';

type BarcodeDetectorCtor = new (options?: { formats?: string[] }) => {
  detect(image: ImageBitmapSource): Promise<Array<{ rawValue?: string }>>;
};

type WindowWithBarcodeDetector = Window & typeof globalThis & {
  BarcodeDetector?: BarcodeDetectorCtor;
};

/* ================================================================== */
/*  QR Share Modal – Generate QR code for a scouting entry            */
/* ================================================================== */

export function QrShareModal({
  entry,
  onClose,
}: {
  entry: SavedScoutingEntry;
  onClose: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [errorText, setErrorText] = useState('');
  const [payloadSize, setPayloadSize] = useState(0);
  const [copied, setCopied] = useState(false);
  const encodedRef = useRef('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const encoded = await encodeEntryForQr(entry);
        encodedRef.current = encoded;
        setPayloadSize(encoded.length);
        if (cancelled) return;
        if (canvasRef.current) {
          await QRCode.toCanvas(canvasRef.current, encoded, {
            width: 280,
            margin: 2,
            errorCorrectionLevel: encoded.length > 1000 ? 'L' : encoded.length > 600 ? 'M' : 'Q',
            color: { dark: '#0f172a', light: '#ffffff' },
          });
        }
      } catch (err) {
        if (!cancelled) setErrorText((err as Error).message || 'Failed to generate QR code.');
      }
    })();
    return () => { cancelled = true; };
  }, [entry]);

  const handleCopyText = useCallback(async () => {
    if (!encodedRef.current) return;
    try {
      await navigator.clipboard.writeText(encodedRef.current);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback: select text in a temporary textarea
      const ta = document.createElement('textarea');
      ta.value = encodedRef.current;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, []);

  // Close on Escape
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div className="qr-modal-backdrop" onClick={onClose}>
      <div className="qr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="qr-modal-header">
          <h3>Share Entry via QR</h3>
          <button type="button" className="qr-modal-close" onClick={onClose} aria-label="Close">
            &#x2715;
          </button>
        </div>

        <div className="qr-modal-meta">
          <span className="center-chip">{entry.team_label}</span>
          <span className="center-chip">{entry.match_display}</span>
          <span className="center-chip">{entry.event_key}</span>
        </div>

        <div className="qr-modal-canvas-wrap">
          {errorText ? (
            <p className="center-callout warning">{errorText}</p>
          ) : (
            <canvas ref={canvasRef} />
          )}
        </div>

        <p className="qr-modal-hint">
          Point another device's camera at this QR code, or use the "Import via QR" button.
        </p>

        <div className="qr-modal-actions">
          <button type="button" className="center-btn" onClick={handleCopyText}>
            {copied ? 'Copied!' : 'Copy as Text'}
          </button>
          <span className="text-sm text-muted">
            {payloadSize > 0 ? `${payloadSize} chars` : ''}
          </span>
        </div>
      </div>
    </div>
  );
}

/* ================================================================== */
/*  Room QR Modal – Generate QR code for scouting room join           */
/* ================================================================== */

export function RoomQrModal({
  roomKey,
  onClose,
}: {
  roomKey: string;
  onClose: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [errorText, setErrorText] = useState('');
  const [payloadText, setPayloadText] = useState('');
  const [copied, setCopied] = useState(false);
  const normalizedRoomKey = String(roomKey || '').trim().toLowerCase();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const payload = encodeRoomKeyForQr(normalizedRoomKey);
      if (!payload) {
        if (!cancelled) setErrorText('Invalid room key.');
        return;
      }
      setPayloadText(payload);
      try {
        if (canvasRef.current) {
          await QRCode.toCanvas(canvasRef.current, payload, {
            width: 280,
            margin: 2,
            errorCorrectionLevel: 'Q',
            color: { dark: '#0f172a', light: '#ffffff' },
          });
        }
      } catch (err) {
        if (!cancelled) setErrorText((err as Error).message || 'Failed to generate room QR code.');
      }
    })();
    return () => { cancelled = true; };
  }, [normalizedRoomKey]);

  const handleCopyText = useCallback(async () => {
    if (!payloadText) return;
    try {
      await navigator.clipboard.writeText(payloadText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = payloadText;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, [payloadText]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div className="qr-modal-backdrop" onClick={onClose}>
      <div className="qr-modal" onClick={(e) => e.stopPropagation()}>
        <div className="qr-modal-header">
          <h3>Scouting Room QR</h3>
          <button type="button" className="qr-modal-close" onClick={onClose} aria-label="Close">
            &#x2715;
          </button>
        </div>

        <div className="qr-modal-meta">
          <span className="center-chip">Room: {normalizedRoomKey || 'N/A'}</span>
        </div>

        <div className="qr-modal-canvas-wrap">
          {errorText ? (
            <p className="center-callout warning">{errorText}</p>
          ) : (
            <canvas ref={canvasRef} />
          )}
        </div>

        <p className="qr-modal-hint">
          Teammates can use <strong>Scan QR Code</strong> in Scouting Room to join this room.
        </p>

        <div className="qr-modal-actions">
          <button type="button" className="center-btn" onClick={handleCopyText}>
            {copied ? 'Copied!' : 'Copy QR Text'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ================================================================== */
/*  QR Import Modal – Scan or paste a QR code to import an entry      */
/* ================================================================== */

export function QrImportModal({
  onImport,
  onClose,
  existingIds,
  onRoomJoin,
}: {
  onImport: (entry: SavedScoutingEntry) => void;
  onClose: () => void;
  existingIds: Set<string>;
  onRoomJoin?: (roomKey: string) => Promise<void> | void;
}) {
  const [pasteText, setPasteText] = useState('');
  const [errorText, setErrorText] = useState('');
  const [successText, setSuccessText] = useState('');
  const [scanning, setScanning] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const scanIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopCamera = useCallback(() => {
    if (scanIntervalRef.current) {
      clearInterval(scanIntervalRef.current);
      scanIntervalRef.current = null;
    }
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) track.stop();
      streamRef.current = null;
    }
    setScanning(false);
  }, []);

  // Close on Escape
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  // Cleanup camera on unmount
  useEffect(() => {
    return () => {
      stopCamera();
    };
  }, [stopCamera]);

  async function processQrString(text: string) {
    setErrorText('');
    setSuccessText('');
    if (isQrEntryString(text)) {
      const raw = await decodeEntryFromQr(text);
      if (!raw) {
        setErrorText('Failed to decode entry data. The QR code may be corrupted.');
        return;
      }
      const entry = normalizeEntry(raw);
      if (!entry) {
        setErrorText('Decoded data is not a valid scouting entry.');
        return;
      }

      // Check for duplicate — use match_key + team_key + scout_profile as natural key
      const naturalKey = `${entry.match_key}__${entry.team_key}__${entry.scout_profile}`;
      const isDuplicate = existingIds.has(naturalKey);
      if (isDuplicate) {
        setErrorText(`Entry for ${entry.team_label} in ${entry.match_display} by ${entry.scout_profile} already exists.`);
        return;
      }

      stopCamera();
      onImport(entry);
      setSuccessText(`Imported: ${entry.team_label} · ${entry.match_display} · ${entry.scout_profile}`);
      return;
    }

    if (onRoomJoin) {
      const roomKey = parseRoomKeyFromQr(text);
      if (roomKey) {
        try {
          stopCamera();
          await onRoomJoin(roomKey);
        } catch (err) {
          setErrorText((err as Error).message || `Failed to join room ${roomKey}.`);
        }
        return;
      }
      setErrorText('Not a valid scouting entry or scouting room QR code.');
      return;
    }

    setErrorText('Not a valid FRCMOB scouting entry QR code.');
  }

  function handlePasteSubmit() {
    const text = pasteText.trim();
    if (!text) return;
    void processQrString(text);
  }

  async function startCamera() {
    setErrorText('');
    setScanning(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 640 }, height: { ideal: 480 } },
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }

      const BarcodeDetector = (window as WindowWithBarcodeDetector).BarcodeDetector;
      if (BarcodeDetector) {
        const detector = new BarcodeDetector({ formats: ['qr_code'] });
        scanIntervalRef.current = setInterval(async () => {
          if (!videoRef.current || videoRef.current.readyState < 2) return;
          try {
            const barcodes = await detector.detect(videoRef.current);
            if (barcodes.length > 0 && barcodes[0].rawValue) {
              void processQrString(barcodes[0].rawValue);
            }
          } catch { /* scan frame failed, retry */ }
        }, 350);
      } else {
        // No BarcodeDetector — show camera but user must use paste
        setErrorText('Camera scanning not supported in this browser. Use "Paste Code" instead, or try Chrome/Edge.');
      }
    } catch (err) {
      setScanning(false);
      const msg = (err as Error).message || 'Camera access denied.';
      setErrorText(`Camera error: ${msg}`);
    }
  }

  return (
    <div className="qr-modal-backdrop" onClick={onClose}>
      <div className="qr-modal qr-import-modal" onClick={(e) => e.stopPropagation()}>
        <div className="qr-modal-header">
          <h3>{onRoomJoin ? 'Import / Join via QR' : 'Import via QR'}</h3>
          <button type="button" className="qr-modal-close" onClick={onClose} aria-label="Close">
            &#x2715;
          </button>
        </div>

        {successText ? (
          <div className="qr-import-success">
            <p className="center-callout" style={{ background: 'rgba(34,197,94,0.12)', color: '#22c55e' }}>
              {successText}
            </p>
            <button type="button" className="center-btn" onClick={onClose}>
              Done
            </button>
          </div>
        ) : (
          <>
            {/* Camera scanning */}
            <div className="qr-scan-section">
              {scanning ? (
                <div className="qr-video-wrap">
                  <video ref={videoRef} playsInline muted className="qr-video" />
                  <div className="qr-scan-overlay" />
                  <button type="button" className="center-btn qr-stop-btn" onClick={stopCamera}>
                    Stop Camera
                  </button>
                </div>
              ) : (
                <button type="button" className="center-btn qr-scan-btn" onClick={startCamera}>
                  Scan with Camera
                </button>
              )}
            </div>

            <div className="qr-divider">
              <span>OR</span>
            </div>

            {/* Paste text */}
            <div className="qr-paste-section">
              <label className="text-sm text-muted">
                {onRoomJoin ? 'Paste the shared entry or room code:' : 'Paste the shared code:'}
              </label>
              <textarea
                className="qr-paste-input"
                rows={3}
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder={onRoomJoin ? 'FRCMOB1:eJy0k… or room-abc123' : 'FRCMOB1:eJy0k…'}
              />
              <button type="button" className="center-btn" onClick={handlePasteSubmit}>
                Import
              </button>
            </div>

            {errorText ? <p className="center-callout warning" style={{ fontSize: '0.8rem' }}>{errorText}</p> : null}
          </>
        )}
      </div>
    </div>
  );
}
