import { cx } from './cx';
import { useCallback, useEffect, useId, useRef, type ReactNode } from 'react';
import { useFocusTrap } from '../../../hooks/useFocusTrap';
import { createPortal } from 'react-dom';
import styles from './Modal.module.css';

type ModalSize = 'sm' | 'md' | 'lg' | 'full';

export type ModalProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  size?: ModalSize;
  /** When false, Escape and backdrop clicks do not close. Use for destructive confirms. */
  dismissible?: boolean;
  footer?: ReactNode;
  className?: string;
};

// Scroll lock is refcounted: two stacked modals both lock, and the page only
// regains its scrollbar when the last one closes. A plain boolean here means
// closing an inner modal unlocks the page behind the outer one.
let scrollLocks = 0;

function lockScroll() {
  scrollLocks += 1;
  if (scrollLocks === 1) document.body.style.overflow = 'hidden';
}

function releaseScroll() {
  scrollLocks = Math.max(0, scrollLocks - 1);
  if (scrollLocks === 0) document.body.style.overflow = '';
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M5 5l10 10" />
      <path d="M15 5L5 15" />
    </svg>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  size = 'md',
  dismissible = true,
  footer,
  className,
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  const requestClose = useCallback(() => {
    if (dismissible) onClose();
  }, [dismissible, onClose]);

  useFocusTrap(dialogRef, open);

  useEffect(() => {
    if (!open) return undefined;
    lockScroll();
    return releaseScroll;
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      requestClose();
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [open, requestClose]);

  if (!open || typeof document === 'undefined') return null;

  return createPortal(
    <div
      className={styles.backdrop}
      onMouseDown={(event) => {
        // mousedown, not click: a click that *starts* inside the dialog and
        // ends on the backdrop (a drag-select overrunning the edge) would
        // otherwise close the dialog and lose whatever was being edited.
        if (event.target === event.currentTarget) requestClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cx(styles.dialog, size !== 'md' && styles[size], className)}
      >
        <header className={styles.head}>
          <h2 className={styles.title} id={titleId}>
            {title}
          </h2>
          {dismissible ? (
            <button type="button" className={styles.close} onClick={onClose} aria-label={`Close ${title}`}>
              <CloseIcon />
            </button>
          ) : null}
        </header>
        <div className={styles.body}>{children}</div>
        {footer ? <div className={styles.footer}>{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
