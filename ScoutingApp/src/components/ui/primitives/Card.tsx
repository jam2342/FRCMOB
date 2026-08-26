import type { ReactNode } from 'react';
import styles from './Card.module.css';
import { cx } from './cx';

// SurfaceCard stays exactly as it is — it already handles portalled expansion,
// mobile collapse and focus correctly. These are only the insides.

export function CardBody({
  children,
  tight = false,
  className,
}: {
  children: ReactNode;
  tight?: boolean;
  className?: string;
}) {
  return <div className={cx(styles.body, tight && styles.bodyTight, className)}>{children}</div>;
}

export type CardRowProps = {
  label: ReactNode;
  value: ReactNode;
  onClick?: () => void;
  className?: string;
};

export function CardRow({ label, value, onClick, className }: CardRowProps) {
  if (onClick) {
    return (
      <button type="button" className={cx(styles.row, styles.rowButton, className)} onClick={onClick}>
        <span className={styles.rowLabel}>{label}</span>
        <span className={styles.rowValue}>{value}</span>
      </button>
    );
  }
  return (
    <div className={cx(styles.row, className)}>
      <span className={styles.rowLabel}>{label}</span>
      <span className={styles.rowValue}>{value}</span>
    </div>
  );
}

export function CardGrid({
  children,
  dense = false,
  className,
}: {
  children: ReactNode;
  dense?: boolean;
  className?: string;
}) {
  return <div className={cx(styles.grid, dense && styles.gridDense, className)}>{children}</div>;
}

export function CardEmpty({
  title,
  children,
  icon,
  action,
  className,
}: {
  title: string;
  children?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx(styles.empty, className)}>
      {icon ? (
        <span className={styles.emptyIcon} aria-hidden="true">
          {icon}
        </span>
      ) : null}
      <span className={styles.emptyTitle}>{title}</span>
      {children ? <span className={styles.emptyText}>{children}</span> : null}
      {action}
    </div>
  );
}
