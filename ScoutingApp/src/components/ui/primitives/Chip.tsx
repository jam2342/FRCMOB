import type { ReactNode } from 'react';
import styles from './Chip.module.css';
import { cx } from './cx';

// `red` and `blue` are alliance tones. They carry a real fact about the data —
// which side a row belongs to — and must never be used as decoration.
export type ChipTone = 'neutral' | 'accent' | 'warn' | 'danger' | 'red' | 'blue';

export type ChipProps = {
  children: ReactNode;
  tone?: ChipTone;
  size?: 'sm' | 'md';
  dot?: boolean;
  icon?: ReactNode;
  onRemove?: () => void;
  removeLabel?: string;
  className?: string;
  title?: string;
};

function CloseIcon() {
  return (
    <svg viewBox="0 0 10 10" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <path d="M1.5 1.5 8.5 8.5" />
      <path d="M8.5 1.5 1.5 8.5" />
    </svg>
  );
}

export function Chip({
  children,
  tone = 'neutral',
  size = 'md',
  dot = false,
  icon,
  onRemove,
  removeLabel,
  className,
  title,
}: ChipProps) {
  return (
    <span className={cx(styles.chip, styles[tone], size === 'sm' && styles.sm, className)} title={title}>
      {dot ? <span className={styles.dot} aria-hidden="true" /> : null}
      {icon ? (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      ) : null}
      <span className={styles.label}>{children}</span>
      {onRemove ? (
        <button
          type="button"
          className={styles.remove}
          onClick={onRemove}
          aria-label={removeLabel ?? (typeof children === 'string' ? `Remove ${children}` : 'Remove')}
        >
          <CloseIcon />
        </button>
      ) : null}
    </span>
  );
}
