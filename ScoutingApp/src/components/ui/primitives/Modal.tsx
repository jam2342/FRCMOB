import { useCallback, useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import styles from './Modal.module.css';
import { cx } from './cx';

export type ModalSize = 'sm' | 'md' | 'lg' | 'full';

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

const FOCUSABLE = [
  'a[href]',
  'button:not(:disabled)',
  'input:not(:disabled)',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

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
  const restoreFocusTo = useRef<HTMLElement | null>(null);
  const titleId = useId();

  const requestClose = useCallback(() => {
    if (dismissible) onClose();
  }, [dismissible, onClose]);

  // Remember where focus came from before the dialog steals it, and put it
  // back on close — otherwise the keyboard user lands at the top of the page.
  useEffect(() => {
    if (!open) return undefined;
    restoreFocusTo.current = document.activeElement as HTMLElement | null;
    lockScroll();

    const focusFirst = () => {
      const node = dialogRef.current;
      if (!node) return;
      const target = node.querySelector<HTMLElement>(FOCUSABLE) ?? node;
      target.focus();
    };
    // One frame later: the portal content is in the DOM but not yet laid out
    // on the same tick, and focusing an unlaid-out element can scroll the page.
    const raf = requestAnimationFrame(focusFirst);

    return () => {
      cancelAnimationFrame(raf);
      releaseScroll();
      restoreFocusTo.current?.focus?.();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        requestClose();
        return;
      }
      if (event.key !== 'Tab') return;

      const node = dialogRef.current;
      if (!node) return;
      // Filter on attributes, not on layout. `offsetParent` is null for any
      // position:fixed element — which the backdrop is — and is null for
      // everything under jsdom, where there is no layout at all. Either way it
      // empties the list and the trap silently stops trapping. The browser
      // already skips display:none elements on a real Tab; the only thing this
      // needs to catch is content deliberately hidden from assistive tech.
      const focusable = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => !element.hasAttribute('hidden') && element.getAttribute('aria-hidden') !== 'true',
      );
      if (focusable.length === 0) {
        event.preventDefault();
        node.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      // Wrap at both ends, and also catch the case where focus has escaped the
      // dialog entirely (a stray programmatic focus, or the initial body focus).
      if (event.shiftKey && (active === first || !node.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !node.contains(active))) {
        event.preventDefault();
        first.focus();
      }
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
