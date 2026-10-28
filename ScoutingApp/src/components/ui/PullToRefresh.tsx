import { useEffect, useRef, useState } from 'react';
import { readFreshFor } from '../../api';
import { activePollCount, refreshActivePolls } from '../../hooks/useSingleFlightPolling';
import { isNativeApp } from '../../platform/runtime';
import { hapticTap } from '../../utils/haptics';
import styles from './PullToRefresh.module.css';

// Pull down from the top of a page to refresh what it shows. Only in the installed app (home-screen
// app or native): a browser tab has its own pull-to-refresh, which reloads the whole page, and two
// competing gestures would be worse than either. Ours refreshes the page's data in place.

const TRIGGER_PX = 64;
const MAX_PULL_PX = 110;
const RESISTANCE = 0.5;
const MIN_SPIN_MS = 500;
const DONE_MS = 900;
// Pages where a pull is too easy to set off by accident mid-task.
const EXCLUDED_PATHS = ['/scouting', '/scouting/record', '/scouting/calibrate'];
// Lets a test browser (not installed) try the gesture.
const PULL_TO_REFRESH_FORCE_KEY = 'frcmob_pull_refresh_force';

function installedApp(): boolean {
  try {
    if (isNativeApp()) return true;
    if (window.localStorage.getItem(PULL_TO_REFRESH_FORCE_KEY) === '1') return true;
    const iosStandalone = (navigator as Navigator & { standalone?: boolean }).standalone === true;
    return iosStandalone || window.matchMedia('(display-mode: standalone)').matches;
  } catch {
    return false;
  }
}

// Something between the finger and the page that is itself scrolled down gets the drag.
function startsAtTop(target: EventTarget | null, area: HTMLElement): boolean {
  if (area.scrollTop > 0 || (document.scrollingElement?.scrollTop ?? 0) > 0) return false;
  let el = target instanceof Element ? target : null;
  while (el && el !== area) {
    if (el.scrollTop > 0) return false;
    if (el.matches('input, textarea, select, canvas, [role="slider"], [data-no-pull-refresh]')) return false;
    el = el.parentElement;
  }
  return true;
}

type Phase = 'idle' | 'pulling' | 'refreshing' | 'done';

export function PullToRefresh({ areaRef, pathname }: { areaRef: { current: HTMLElement | null }; pathname: string }) {
  const [enabled] = useState(installedApp);
  const [pull, setPull] = useState(0);
  const [phase, setPhase] = useState<Phase>('idle');
  const phaseRef = useRef<Phase>('idle');
  phaseRef.current = phase;
  const excluded = EXCLUDED_PATHS.includes(pathname.replace(/\/$/, ''));

  useEffect(() => {
    const area = areaRef.current;
    if (!enabled || excluded || !area) return undefined;
    let startY: number | null = null;
    let distance = 0;

    const onStart = (event: TouchEvent) => {
      if (phaseRef.current === 'refreshing' || event.touches.length !== 1 || activePollCount() === 0) {
        startY = null;
        return;
      }
      startY = startsAtTop(event.target, area) ? event.touches[0].clientY : null;
      distance = 0;
    };
    const onMove = (event: TouchEvent) => {
      if (startY === null) return;
      const dy = event.touches[0].clientY - startY;
      if (dy <= 0) {
        if (distance > 0) {
          distance = 0;
          setPull(0);
          setPhase('idle');
        }
        return;
      }
      // Holding the page still while pulling; otherwise the browser rubber-bands it as well.
      if (event.cancelable) event.preventDefault();
      distance = Math.min(MAX_PULL_PX, dy * RESISTANCE);
      setPull(distance);
      setPhase('pulling');
    };
    const onEnd = () => {
      if (startY === null) return;
      startY = null;
      if (distance < TRIGGER_PX) {
        setPull(0);
        setPhase('idle');
        return;
      }
      setPhase('refreshing');
      setPull(TRIGGER_PX * 0.8);
      hapticTap();
      const startedAt = Date.now();
      const endFreshReads = navigator.onLine === false ? () => undefined : readFreshFor(10_000);
      void refreshActivePolls().finally(() => {
        endFreshReads();
        const wait = Math.max(0, MIN_SPIN_MS - (Date.now() - startedAt));
        window.setTimeout(() => {
          setPhase('done');
          window.setTimeout(() => {
            setPull(0);
            setPhase('idle');
          }, DONE_MS);
        }, wait);
      });
    };

    area.addEventListener('touchstart', onStart, { passive: true });
    area.addEventListener('touchmove', onMove, { passive: false });
    area.addEventListener('touchend', onEnd, { passive: true });
    area.addEventListener('touchcancel', onEnd, { passive: true });
    return () => {
      area.removeEventListener('touchstart', onStart);
      area.removeEventListener('touchmove', onMove);
      area.removeEventListener('touchend', onEnd);
      area.removeEventListener('touchcancel', onEnd);
    };
  }, [areaRef, enabled, excluded]);

  if (!enabled || excluded || (phase === 'idle' && pull === 0)) return null;
  const armed = pull >= TRIGGER_PX;
  const label = phase === 'refreshing'
    ? 'Refreshing…'
    : phase === 'done'
      ? 'Up to date'
      : armed ? 'Release to refresh' : 'Pull to refresh';

  return (
    <div
      className={styles.indicator}
      style={{ transform: `translate(-50%, ${pull - 8}px)`, opacity: Math.min(1, pull / 40) }}
      role="status"
      aria-live="polite"
      data-phase={phase}
    >
      {phase === 'done' ? (
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" className={styles.done}>
          <path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : (
        <svg
          viewBox="0 0 24 24"
          width="18"
          height="18"
          aria-hidden="true"
          className={phase === 'refreshing' ? styles.spin : undefined}
          style={phase === 'refreshing' ? undefined : { transform: `rotate(${pull * 3.2}deg)` }}
        >
          <path d="M12 3a9 9 0 1 0 9 9" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
        </svg>
      )}
      <span>{label}</span>
    </div>
  );
}
