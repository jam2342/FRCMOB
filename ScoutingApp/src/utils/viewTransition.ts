import { flushSync } from 'react-dom';

// Page-to-page motion through the browser's View Transitions API. Browsers without it, and anyone
// with reduced motion on, navigate exactly as before.
//
// The router doesn't render the new page inside the transition's callback (it commits in a
// React transition, after the page's chunk loads), so the callback waits for the shell to say
// the new location has committed. The screen holds the old frame while it waits, so the wait is
// capped short: a page that isn't ready just cross-fades a little later.

export type TransitionDirection = 'forward' | 'back' | 'fade';

type NavigateFn = (to: string) => void;

type TransitionOptions = {
  direction?: TransitionDirection;
  // An element on the old page that becomes `morphTarget` on the new one (a match row growing
  // into the Match Center header). Named only for this transition.
  morphFrom?: Element | null;
  morphName?: string;
  // CSS selector for the new page's counterpart; the callback waits a few frames for it.
  morphTarget?: string;
};

const COMMIT_TIMEOUT_MS = 350;
// How long a morph waits for its target (the opened match's header) after the page commits.
const TARGET_WAIT_MS = 320;
// How long a new page may hold the transition while it fills placeholders marked
// `data-vt-pending` from data it already has. Cached data lands in a few frames; a slow phone
// took ~170 ms, and without the wait the page slid in half-empty and then filled in.
const SETTLE_WAIT_MS = 260;
const PENDING_SELECTOR = '.ps-content [data-vt-pending]';

let pendingCommit: (() => void) | null = null;
let activeTransition = false;

type ViewTransitionLike = { finished: Promise<void>; ready?: Promise<void> };
type DocumentWithTransitions = Document & {
  startViewTransition?: (update: () => Promise<void> | void) => ViewTransitionLike;
};

// The page that just slid in already made its entrance. The shell's own page fade-in is held off
// while a transition runs; without this mark it started when the transition ended, so the page
// slid in, went blank, and faded in again ("loads, then reloads").
function markArrived(): void {
  document.querySelectorAll('.ps-content > *').forEach((el) => el.setAttribute('data-arrived', ''));
}

function viewTransitionsAvailable(): boolean {
  if (typeof document === 'undefined' || typeof window === 'undefined') return false;
  if (typeof (document as DocumentWithTransitions).startViewTransition !== 'function') return false;
  try {
    return !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return true;
  }
}

// Called by the shell in a layout effect whenever the location changes.
export function notifyRouteCommitted(): void {
  const resolve = pendingCommit;
  pendingCommit = null;
  resolve?.();
}

// Polled with timers, not requestAnimationFrame: the browser holds rendering (rAF included) while
// a transition waits for its update, so an rAF poll never ran and every morph sat out the timeout.
function waitForTarget(selector: string | undefined): Promise<void> {
  if (!selector || document.querySelector(selector)) return Promise.resolve();
  return new Promise((resolve) => {
    const startedAt = performance.now();
    const check = () => {
      if (document.querySelector(selector) || performance.now() - startedAt > TARGET_WAIT_MS) resolve();
      else window.setTimeout(check, 16);
    };
    window.setTimeout(check, 16);
  });
}

// Waits for the page's first effects to run (they start its loads), then until nothing on it is
// marked pending, capped by SETTLE_WAIT_MS.
function waitForSettled(): Promise<void> {
  return new Promise((resolve) => {
    const startedAt = performance.now();
    const check = () => {
      if (!document.querySelector(PENDING_SELECTOR) || performance.now() - startedAt > SETTLE_WAIT_MS) resolve();
      else window.setTimeout(check, 16);
    };
    window.setTimeout(check, 16);
  });
}

export function navigateWithTransition(navigate: NavigateFn, to: string, options: TransitionOptions = {}): void {
  const doc = document as DocumentWithTransitions;
  if (!viewTransitionsAvailable() || activeTransition || !doc.startViewTransition) {
    navigate(to);
    return;
  }
  const root = document.documentElement;
  const direction = options.direction ?? 'fade';
  const morphFrom = options.morphFrom instanceof HTMLElement ? options.morphFrom : null;
  const morphName = options.morphName ?? 'shared-hero';
  activeTransition = true;
  root.dataset.vt = direction;
  if (morphFrom) {
    morphFrom.style.viewTransitionName = morphName;
    root.dataset.vtMorph = morphName;
  }

  const cleanup = () => {
    activeTransition = false;
    markArrived();
    delete root.dataset.vt;
    delete root.dataset.vtMorph;
    if (morphFrom) morphFrom.style.viewTransitionName = '';
  };

  try {
    const transition = doc.startViewTransition(() => new Promise<void>((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        // The old element is gone or about to be; only the new page's counterpart keeps the name.
        if (morphFrom) morphFrom.style.viewTransitionName = '';
        resolve();
      };
      let timer = window.setTimeout(finish, COMMIT_TIMEOUT_MS);
      pendingCommit = () => {
        // Committed in time: the waits below carry their own caps.
        window.clearTimeout(timer);
        timer = 0;
        void Promise.all([waitForTarget(morphFrom ? options.morphTarget : undefined), waitForSettled()]).then(finish);
      };
      navigate(to);
    }));
    // A phone aborts it when the viewport changes (URL bar, keyboard); the update still runs.
    transition.ready?.catch(() => undefined);
    transition.finished.then(cleanup, cleanup);
  } catch {
    cleanup();
    navigate(to);
  }
}

// A tab switch inside one page: the content slides toward the tab you picked while the page's
// header and tab bar stay put (they are named while it runs, see view-transitions.css).
export function switchTabWithTransition(
  update: () => void,
  direction: TransitionDirection,
  pinned: readonly Element[] = [],
): void {
  const doc = document as DocumentWithTransitions;
  if (!viewTransitionsAvailable() || activeTransition || !doc.startViewTransition) {
    update();
    return;
  }
  const root = document.documentElement;
  const pins = pinned.filter((el): el is HTMLElement => el instanceof HTMLElement);
  activeTransition = true;
  root.dataset.vt = direction;
  root.dataset.vtTabs = '';
  pins.forEach((el, index) => {
    el.style.viewTransitionName = `tab-pin-${index}`;
  });
  const cleanup = () => {
    activeTransition = false;
    delete root.dataset.vt;
    delete root.dataset.vtTabs;
    pins.forEach((el) => {
      el.style.viewTransitionName = '';
    });
  };
  try {
    const transition = doc.startViewTransition(() => {
      flushSync(update);
    });
    transition.ready?.catch(() => undefined);
    transition.finished.then(cleanup, cleanup);
  } catch {
    cleanup();
    update();
  }
}

// A theme change that spreads out in a circle from where it was asked for (the toggle, the
// settings field), instead of the whole screen flipping at once. The whole page is one picture
// for this one (view-transitions.css drops the usual names), so the circle covers everything.
export function revealThemeChange(apply: () => void, origin?: { x: number; y: number } | null): void {
  const doc = document as DocumentWithTransitions;
  if (!viewTransitionsAvailable() || activeTransition || !doc.startViewTransition) {
    apply();
    return;
  }
  const root = document.documentElement;
  activeTransition = true;
  root.dataset.vtTheme = '';
  const cleanup = () => {
    activeTransition = false;
    delete root.dataset.vtTheme;
  };
  try {
    const transition = doc.startViewTransition(() => {
      flushSync(apply);
    });
    const x = origin?.x ?? window.innerWidth / 2;
    const y = origin?.y ?? 0;
    const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
    transition.ready
      ?.then(() => {
        root.animate(
          { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
          { duration: 520, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', pseudoElement: '::view-transition-new(root)' },
        );
      })
      .catch(() => undefined);
    transition.finished.then(cleanup, cleanup);
  } catch {
    cleanup();
    apply();
  }
}

// A plain left click; anything else (new tab, new window) is left to the browser.
export function isPlainClick(event: { defaultPrevented: boolean; button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean }): boolean {
  return !event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

// Direction for moving between top-level sections, from their order in the navigation.
export function sectionDirection(order: readonly string[], fromPath: string, toPath: string): TransitionDirection {
  const indexOf = (path: string) => order.findIndex((root) => path === root || path.startsWith(`${root}/`));
  const from = indexOf(fromPath);
  const target = indexOf(toPath);
  if (from < 0 || target < 0 || from === target) return 'fade';
  return target > from ? 'forward' : 'back';
}
