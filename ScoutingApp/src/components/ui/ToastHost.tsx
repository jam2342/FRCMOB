import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { currentToast, pauseToast, resumeToast, runToastAction, subscribeToasts, type ToastRecord } from './toast';
import styles from './ToastHost.module.css';

const LEAVE_MS = 200;

function ToneIcon({ tone }: { tone: ToastRecord['tone'] }) {
  if (tone === 'warning') {
    return (
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" className={styles.iconWarning}>
        <path d="M12 4 2.5 20h19z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
        <path d="M12 10v4.5M12 17.2v.1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  }
  if (tone === 'info') {
    return (
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" className={styles.iconInfo}>
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M12 11v5.5M12 7.8v.1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" className={styles.iconSuccess}>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <path className={styles.tick} d="M8 12.5l2.8 2.8L16.5 9.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function ToastHost() {
  const toast = useSyncExternalStore(subscribeToasts, currentToast, () => null);
  // The last toast stays rendered for a moment after it closes so it can slide out.
  const [leavingToast, setLeavingToast] = useState<ToastRecord | null>(null);
  const lastToastRef = useRef<ToastRecord | null>(toast);
  const [pausedId, setPausedId] = useState(0);

  useEffect(() => {
    const previous = lastToastRef.current;
    lastToastRef.current = toast;
    if (toast || !previous) return undefined;
    setLeavingToast(previous);
    const timer = window.setTimeout(() => setLeavingToast(null), LEAVE_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const shown = toast ?? leavingToast;
  if (!shown || typeof document === 'undefined') return null;
  const leaving = !toast;
  const paused = pausedId === shown.id;
  const hold = () => {
    pauseToast(shown.id);
    setPausedId(shown.id);
  };
  const release = () => {
    resumeToast(shown.id);
    setPausedId(0);
  };

  return createPortal(
    <div className={styles.region}>
      <div
        key={shown.id}
        role="status"
        aria-live="polite"
        className={`${styles.toast} ${leaving ? styles.leaving : ''}`}
        onPointerEnter={hold}
        onPointerLeave={release}
        onFocus={hold}
        onBlur={release}
      >
        <div className={styles.row}>
          <ToneIcon tone={shown.tone} />
          <span className={styles.message}>{shown.message}</span>
          {shown.actionLabel ? (
            <button
              type="button"
              className={styles.action}
              onClick={() => runToastAction(shown.id)}
              disabled={leaving}
            >
              {shown.actionLabel}
            </button>
          ) : null}
        </div>
        {shown.actionLabel ? (
          <span
            className={styles.timer}
            style={{ animationDuration: `${shown.durationMs}ms`, animationPlayState: paused || leaving ? 'paused' : 'running' }}
            aria-hidden="true"
          />
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
