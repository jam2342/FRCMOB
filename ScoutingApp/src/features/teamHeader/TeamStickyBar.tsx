import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { SegmentedTabs } from '../../components/ui/SegmentedTabs';
import styles from './TeamStickyBar.module.css';

// Team Center's header shrinks into this bar once you scroll past it: team, name and rating stay
// in view, and so do the tabs, so switching tab never means scrolling back up. Tapping the team
// goes back to the top.

type Tab<T extends string> = { value: T; label: ReactNode };

type TeamStickyBarProps<T extends string> = {
  // The header's own tab bar: the bar shows once that has scrolled up out of view.
  watchRef: RefObject<HTMLElement | null>;
  teamNumber: string | number;
  nickname: string;
  rating: ReactNode;
  detail?: ReactNode;
  tabs: readonly Tab<T>[];
  tab: T;
  onTab: (next: T) => void;
  phone: boolean;
};

function scrollerOf(el: HTMLElement): HTMLElement | Window {
  let node = el.parentElement;
  while (node && node !== document.body) {
    const overflow = getComputedStyle(node).overflowY;
    if ((overflow === 'auto' || overflow === 'scroll') && node.scrollHeight > node.clientHeight + 1) return node;
    node = node.parentElement;
  }
  return window;
}

export function TeamStickyBar<T extends string>({ watchRef, teamNumber, nickname, rating, detail, tabs, tab, onTab, phone }: TeamStickyBarProps<T>) {
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const barRef = useRef<HTMLDivElement | null>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    let frame = 0;
    const check = () => {
      frame = 0;
      const watched = watchRef.current;
      const anchor = anchorRef.current;
      const bar = barRef.current;
      if (!watched || !anchor || !bar) return;
      // The anchor is sticky, so its top is the line the bar hangs from. The bar takes over as the
      // header's tabs slide under its bottom edge, so its own tabs land right where those were.
      // (Measured from the anchor, not the bar: the bar slides while it shows and hides.)
      const barBottom = anchor.getBoundingClientRect().top + bar.offsetTop + bar.offsetHeight;
      setShown(watched.getBoundingClientRect().bottom <= barBottom + 1);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(check);
    };
    check();
    window.addEventListener('scroll', onScroll, { capture: true, passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener('scroll', onScroll, { capture: true });
      window.removeEventListener('resize', onScroll);
    };
  }, [watchRef]);

  const backToTop = () => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const scroller = scrollerOf(anchor);
    const smooth = !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    scroller.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'auto' });
  };

  // A tab picked here keeps you at the start of its content, right under the bar.
  const pickTab = (next: T) => {
    onTab(next);
    window.setTimeout(() => {
      const watched = watchRef.current;
      const bar = barRef.current;
      const anchor = anchorRef.current;
      if (!watched || !bar || !anchor) return;
      const offset = watched.getBoundingClientRect().bottom - (anchor.getBoundingClientRect().top + bar.offsetTop + bar.offsetHeight);
      if (offset > 0) return;
      const scroller = scrollerOf(anchor);
      if (scroller === window) window.scrollBy({ top: offset });
      else (scroller as HTMLElement).scrollBy({ top: offset });
    }, 0);
  };

  return (
    <div ref={anchorRef} className={styles.anchor}>
      <div
        ref={barRef}
        className={`${styles.bar} ${shown ? styles.shown : ''} ${phone ? styles.phone : ''}`}
        aria-hidden={!shown}
        inert={!shown}
      >
        <div className={styles.row}>
          <button type="button" className={styles.identity} onClick={backToTop} aria-label={`Team ${teamNumber}, back to the top`}>
            <strong className={styles.number}>{teamNumber}</strong>
            <span className={styles.name}>{nickname}</span>
          </button>
          {detail ? <span className={styles.detail}>{detail}</span> : null}
          <span className={styles.rating}>{rating}</span>
        </div>
        <SegmentedTabs
          ariaLabel="Team center tabs"
          value={tab}
          onChange={pickTab}
          items={tabs}
        />
      </div>
    </div>
  );
}
