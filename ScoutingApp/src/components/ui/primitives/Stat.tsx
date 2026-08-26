import type { ReactNode } from 'react';
import styles from './Stat.module.css';
import { cx } from './cx';

export type StatTone = 'default' | 'accent' | 'success' | 'warning' | 'danger';

export type StatProps = {
  label: string;
  value: ReactNode;
  unit?: string;
  sub?: ReactNode;
  /** Signed change since the previous period. Sign picks the arrow and colour. */
  trend?: number;
  trendSuffix?: string;
  tone?: StatTone;
  /** 0–1. Rendered as a bar plus a percentage — never hidden. */
  confidence?: number;
  /** `display` is the one-per-screen hero. See src/styles/README.md. */
  size?: 'sm' | 'md' | 'lg' | 'display';
  className?: string;
};

const TONE_CLASS: Record<StatTone, string | undefined> = {
  default: undefined,
  accent: styles.toneAccent,
  success: styles.toneSuccess,
  warning: styles.toneWarning,
  danger: styles.toneDanger,
};

function TrendIcon({ direction }: { direction: 'up' | 'down' | 'flat' }) {
  if (direction === 'flat') {
    return (
      <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <path d="M2 6h8" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {direction === 'up' ? <path d="M6 10V2m0 0L2.5 5.5M6 2l3.5 3.5" /> : <path d="M6 2v8m0 0 3.5-3.5M6 10 2.5 6.5" />}
    </svg>
  );
}

export function Stat({
  label,
  value,
  unit,
  sub,
  trend,
  trendSuffix = '',
  tone = 'default',
  confidence,
  size = 'md',
  className,
}: StatProps) {
  const direction = trend === undefined ? null : trend > 0 ? 'up' : trend < 0 ? 'down' : 'flat';

  // Clamp rather than trust the caller: a confidence outside 0–1 would render a
  // bar wider than its track, which reads as more certainty than exists.
  const pct = confidence === undefined ? null : Math.round(Math.min(1, Math.max(0, confidence)) * 100);

  return (
    <div
      className={cx(
        styles.stat,
        TONE_CLASS[tone],
        size === 'sm' && styles.sm,
        size === 'lg' && styles.lg,
        size === 'display' && styles.display,
        className,
      )}
    >
      <span className={styles.label}>{label}</span>
      <span className={styles.valueRow}>
        <span className={styles.value}>{value}</span>
        {unit ? <span className={styles.unit}>{unit}</span> : null}
        {direction ? (
          <span
            className={cx(
              styles.trend,
              direction === 'up' && styles.trendUp,
              direction === 'down' && styles.trendDown,
              direction === 'flat' && styles.trendFlat,
            )}
          >
            <TrendIcon direction={direction} />
            {`${trend! > 0 ? '+' : ''}${trend}${trendSuffix}`}
          </span>
        ) : null}
      </span>
      {sub ? <span className={styles.sub}>{sub}</span> : null}
      {pct !== null ? (
        <span className={cx(styles.confidence, pct < 50 && styles.confidenceLow)}>
          <span
            className={styles.confidenceTrack}
            role="img"
            aria-label={`Confidence ${pct} percent`}
          >
            <span className={styles.confidenceFill} style={{ width: `${pct}%` }} />
          </span>
          <span className={styles.confidenceText} aria-hidden="true">{pct}%</span>
        </span>
      ) : null}
    </div>
  );
}
