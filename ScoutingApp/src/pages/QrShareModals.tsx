import { useCallback, useEffect, useRef, useState } from 'react';
import QRCode from 'qrcode';
import { Button, Chip, FieldTextarea, Modal } from '../components/ui/primitives';
import type { SavedScoutingEntry } from './scoutingPage.types';
import { normalizeEntry } from './scoutingPage.helpers';
import {
  encodeEntryForQr,
  decodeEntryFromQr,
  encodeRoomKeyForQr,
  isQrEntryString,
  parseRoomKeyFromQr,
} from './qrUtils';
import styles from './QrShareModals.module.css';

// These three modals are conditionally mounted by ScoutingPage, so `open` is
// always true here and every prop signature is unchanged — nothing at the call
// site moves. What changed is that the overlay, Escape handling, focus trap,
// focus restore, scroll lock and portal now come from Modal instead of being
// hand-rolled three times with none of the last four.

// A QR code is dark-on-white because that is what scanners are built to read.
// These two are deliberately not tokens: theming them would render a light
// code on a dark ground in dark mode and cost real scans at an event.
const QR_COLORS = { dark: '#0f172a', light: '#ffffff' };
const QR_WIDTH = 280;

type BarcodeDetectorCtor = new (options?: { formats?: string[] }) => {
  detect(image: ImageBitmapSource): Promise<Array<{ rawValue?: string }>>;
};

type WindowWithBarcodeDetector = Window & typeof globalThis & {
  BarcodeDetector?: BarcodeDetectorCtor;
};

// Clipboard access is blocked outside a secure context and on some in-app
// browsers, so the temporary-textarea path is a real fallback, not legacy.
async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    document.body.removeChild(area);
  }
}

function useCopyFlag() {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const flag = useCallback(() => {
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  }, []);

  return [copied, flag] as const;
}

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
  const [copied, flagCopied] = useCopyFlag();
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
            width: QR_WIDTH,
            margin: 2,
            errorCorrectionLevel: encoded.length > 1000 ? 'L' : encoded.length > 600 ? 'M' : 'Q',
            color: QR_COLORS,
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
    await copyToClipboard(encodedRef.current);
    flagCopied();
  }, [flagCopied]);

  return (
    <Modal
      open
      onClose={onClose}
      title="Share Entry via QR"
      footer={
        <>
          <span className={styles.size}>{payloadSize > 0 ? `${payloadSize} chars` : ''}</span>
          <Button variant="primary" onClick={handleCopyText}>
            {copied ? 'Copied!' : 'Copy as Text'}
          </Button>
        </>
      }
    >
      <div className={styles.meta}>
        <Chip>{entry.team_label}</Chip>
        <Chip>{entry.match_display}</Chip>
        <Chip tone="accent">{entry.event_key}</Chip>
      </div>

      {errorText ? (
        <p className={`${styles.status} ${styles.statusError}`} role="alert">{errorText}</p>
      ) : (
        <div className={styles.canvasWrap}>
          <canvas ref={canvasRef} />
        </div>
      )}

      <p className={styles.hint}>
        Point another device's camera at this QR code, or use the "Import via QR" button.
      </p>
    </Modal>
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
  const [copied, flagCopied] = useCopyFlag();
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
            width: QR_WIDTH,
            margin: 2,
            errorCorrectionLevel: 'Q',
            color: QR_COLORS,
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
    await copyToClipboard(payloadText);
    flagCopied();
  }, [payloadText, flagCopied]);

  return (
    <Modal
      open
      onClose={onClose}
      title="Scouting Room QR"
      footer={
        <Button variant="primary" onClick={handleCopyText} disabled={!payloadText}>
          {copied ? 'Copied!' : 'Copy QR Text'}
        </Button>
      }
    >
      <div className={styles.meta}>
        <Chip tone="accent" dot>Room: {normalizedRoomKey || 'N/A'}</Chip>
      </div>

      {errorText ? (
        <p className={`${styles.status} ${styles.statusError}`} role="alert">{errorText}</p>
      ) : (
        <div className={styles.canvasWrap}>
          <canvas ref={canvasRef} />
        </div>
      )}

      <p className={styles.hint}>
        Teammates can use <strong>Scan QR Code</strong> in Scouting Room to join this room.
      </p>
    </Modal>
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

  // Releasing the camera on unmount is not optional — a live stream keeps the
  // phone's camera light on and drains the battery a scout needs all day.
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

  if (successText) {
    return (
      <Modal
        open
        onClose={onClose}
        title={onRoomJoin ? 'Import / Join via QR' : 'Import via QR'}
        footer={<Button variant="primary" onClick={onClose}>Done</Button>}
      >
        <div className={styles.successBody}>
          <p className={`${styles.status} ${styles.statusSuccess}`} role="status">{successText}</p>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={onRoomJoin ? 'Import / Join via QR' : 'Import via QR'}
      footer={<Button variant="primary" onClick={handlePasteSubmit} disabled={!pasteText.trim()}>Import</Button>}
    >
      <div className={styles.scanSection}>
        {scanning ? (
          <div className={styles.videoWrap}>
            <video ref={videoRef} playsInline muted className={styles.video} />
            <div className={styles.scanOverlay} />
            <Button variant="quiet" onClick={stopCamera} fullWidth>
              Stop Camera
            </Button>
          </div>
        ) : (
          <Button onClick={startCamera} fullWidth>
            Scan with Camera
          </Button>
        )}
      </div>

      <div className={styles.divider}>OR</div>

      <div className={styles.pasteSection}>
        <FieldTextarea
          label={onRoomJoin ? 'Paste the shared entry or room code' : 'Paste the shared code'}
          rows={3}
          value={pasteText}
          onChange={(e) => setPasteText(e.target.value)}
          placeholder={onRoomJoin ? 'FRCMOB1:eJy0k… or room-abc123' : 'FRCMOB1:eJy0k…'}
        />
      </div>

      {errorText ? (
        <p className={`${styles.status} ${styles.statusError}`} role="alert">{errorText}</p>
      ) : null}
    </Modal>
  );
}
