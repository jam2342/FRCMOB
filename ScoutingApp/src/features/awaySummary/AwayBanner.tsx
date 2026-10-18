import type { AwaySummary } from './awaySummary';
import styles from './Away.module.css';

export function AwayBanner({ summary, onShow, onDismiss }: { summary: AwaySummary; onShow: () => void; onDismiss: () => void }) {
  return (
    <div className={styles.banner} role="status">
      <button type="button" className={styles.main} onClick={onShow}>
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" className={styles.icon}>
          <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M12 7v5l3 2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
        <span className={styles.text}>
          <strong>{summary.headline}</strong>
          <span>{summary.details.join(' · ')}</span>
        </span>
        <span className={styles.show}>Show</span>
      </button>
      <button type="button" className={styles.dismiss} onClick={onDismiss} aria-label="Dismiss">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M18 6 6 18M6 6l12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}
