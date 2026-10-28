import { cx } from './primitives/cx';
import { type KeyboardEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { switchTabWithTransition, type TransitionDirection } from '../../utils/viewTransition';
import { useMobileLayout } from '../../hooks/useMobileLayout';
import './SegmentedTabs.css';


function nextEnabledIndex<T extends string>(
  items: readonly SegmentedTabItem<T>[],
  currentIndex: number,
  direction: 1 | -1,
): number {
  for (let step = 1; step <= items.length; step += 1) {
    const nextIndex = (currentIndex + direction * step + items.length) % items.length;
    if (!items[nextIndex].disabled) return nextIndex;
  }
  return currentIndex;
}

function edgeEnabledIndex<T extends string>(
  items: readonly SegmentedTabItem<T>[],
  direction: 1 | -1,
): number {
  const indexes = items.map((_, index) => index);
  const ordered = direction === 1 ? indexes : indexes.reverse();
  return ordered.find((index) => !items[index].disabled) ?? 0;
}

type SegmentedTabItem<T extends string> = {
  value: T;
  label: ReactNode;
  ariaLabel?: string;
  icon?: ReactNode;
  disabled?: boolean;
  title?: string;
  className?: string;
  panelId?: string;
};

type SegmentedTabsProps<T extends string> = {
  items: readonly SegmentedTabItem<T>[];
  value: T;
  onChange: (next: T) => void;
  ariaLabel: string;
  /** Layout only (spacing, alignment). The look is the component's: see `variant`. */
  className?: string;
  /** Only with variant="board". */
  itemClassName?: string;
  /** 'tabs' is the app's one tab look: an underline strip on phones, a pill group on desktop.
   *  Pages used to choose by passing class names, so the same kind of tabs was an underline
   *  strip on one phone page and boxed pills on the next. 'board' is the scouting board's
   *  large gloves-on segments, styled by the board itself. */
  variant?: 'tabs' | 'board';
  /** Page-level tabs: the content slides toward the picked tab while this tab bar and its
   *  header hold still. */
  motion?: boolean;
  /** Phones: a sideways swipe across the page moves to the next or previous tab. */
  swipe?: boolean;
  /** An underline that slides to the active tab (the phone tab bars). */
  ink?: boolean;
  /** What a page tour points at (`data-tour`). */
  tourId?: string;
};

const SWIPE_MIN_PX = 70;
const SWIPE_MAX_MS = 700;
const EDGE_PX = 24;

// A swipe that starts on something that already uses sideways drags (a scrolling table, the tab
// bar, a field, a chart) belongs to that thing.
function swipeAllowedFrom(target: EventTarget | null, area: Element): boolean {
  let el = target instanceof Element ? target : null;
  while (el && el !== area) {
    if (el.matches('input, textarea, select, canvas, [contenteditable="true"], [role="slider"], [data-no-tab-swipe]')) return false;
    if (el.scrollWidth > el.clientWidth + 2) {
      const overflow = window.getComputedStyle(el).overflowX;
      if (overflow === 'auto' || overflow === 'scroll') return false;
    }
    el = el.parentElement;
  }
  return true;
}

function directionBetween(from: number, to: number): TransitionDirection {
  if (from < 0 || to < 0 || from === to) return 'fade';
  return to > from ? 'forward' : 'back';
}

export function SegmentedTabs<T extends string>({
  items,
  value,
  onChange,
  ariaLabel,
  className = '',
  itemClassName = '',
  variant = 'tabs',
  motion = false,
  swipe = false,
  ink: inkProp = false,
  tourId,
}: SegmentedTabsProps<T>) {
  const phone = useMobileLayout();
  const board = variant === 'board';
  const listClass = board ? '' : phone ? 'fm-tab-bar' : 'center-tabs';
  const tabClass = board ? itemClassName : phone ? 'fm-tab' : 'center-tab-btn';
  const ink = board ? inkProp : phone;
  const listRef = useRef<HTMLDivElement | null>(null);
  // Which ends of a scrolling strip have more tabs past them. A strip cut at the screen edge
  // showed "Fo" for Forecast with nothing saying it scrolls; the faded edge does.
  const [more, setMore] = useState<'' | 'start' | 'end' | 'both'>('');
  const [inkBox, setInkBox] = useState<{ x: number; width: number; ready: boolean } | null>(null);

  const change = (next: T) => {
    if (next === value) return;
    if (!motion) {
      onChange(next);
      return;
    }
    const from = items.findIndex((item) => item.value === value);
    const to = items.findIndex((item) => item.value === next);
    const list = listRef.current;
    const pins: Element[] = [];
    if (list) {
      const header = list.closest('.fm-team-hero, .fm-event-hero, .surface-card');
      pins.push(header ?? list);
      const before = list.previousElementSibling;
      if (!header && before && /\bfm-[a-z-]*hero\b/.test(before.className)) pins.push(before);
      // The section switcher above the page (Match · Predictions · Strategy) doesn't change either.
      const sectionBar = list.closest('.ps-content')?.querySelector('.page-view-bar, .page-view-menu');
      if (sectionBar && !pins.includes(sectionBar)) pins.push(sectionBar);
    }
    switchTabWithTransition(() => onChange(next), directionBetween(from, to), pins);
  };
  const changeRef = useRef(change);
  changeRef.current = change;
  const stateRef = useRef({ items, value });
  stateRef.current = { items, value };

  useLayoutEffect(() => {
    if (!ink) return undefined;
    const list = listRef.current;
    if (!list) return undefined;
    const measure = () => {
      const active = list.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
      setInkBox((current) => {
        if (!active) return null;
        const next = { x: active.offsetLeft, width: active.offsetWidth };
        if (current && current.x === next.x && current.width === next.width) return current;
        // No slide on the first placement, only between tabs.
        return { ...next, ready: Boolean(current) };
      });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    return () => observer.disconnect();
  }, [ink, value, items.length]);

  // On a phone the strip is wider than the screen: a deep link, a swipe or the bottom
  // tab could select a tab sitting off to the right with nothing on screen marked as
  // selected. Bring it into view, scrolling only the strip, never the page.
  useLayoutEffect(() => {
    const list = listRef.current;
    const active = list?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
    if (!list || !active) return;
    let scroller: HTMLElement | null = list;
    for (let depth = 0; scroller && depth < 3 && scroller.scrollWidth <= scroller.clientWidth + 1; depth += 1) {
      scroller = scroller.parentElement;
    }
    if (!scroller || scroller.scrollWidth <= scroller.clientWidth + 1) return;
    const tab = active.getBoundingClientRect();
    const box = scroller.getBoundingClientRect();
    const margin = 16;
    if (tab.left < box.left) scroller.scrollLeft -= box.left - tab.left + margin;
    else if (tab.right > box.right) scroller.scrollLeft += tab.right - box.right + margin;
  }, [value, items.length]);

  useEffect(() => {
    const list = listRef.current;
    if (!list) return undefined;
    const measure = () => {
      const start = list.scrollLeft > 2;
      const end = list.scrollLeft + list.clientWidth < list.scrollWidth - 2;
      setMore(start && end ? 'both' : start ? 'start' : end ? 'end' : '');
    };
    measure();
    list.addEventListener('scroll', measure, { passive: true });
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(list);
    return () => {
      list.removeEventListener('scroll', measure);
      observer?.disconnect();
    };
  }, [items.length, phone]);

  useEffect(() => {
    if (!swipe) return undefined;
    const area = listRef.current?.closest('.ps-content');
    if (!area) return undefined;
    try {
      if (!window.matchMedia('(pointer: coarse)').matches) return undefined;
    } catch {
      return undefined;
    }
    let start: { x: number; y: number; at: number } | null = null;
    const onStart = (event: Event) => {
      const touches = (event as TouchEvent).touches;
      if (!touches || touches.length !== 1) {
        start = null;
        return;
      }
      const touch = touches[0];
      const nearEdge = touch.clientX < EDGE_PX || touch.clientX > window.innerWidth - EDGE_PX;
      start = !nearEdge && swipeAllowedFrom(event.target, area)
        ? { x: touch.clientX, y: touch.clientY, at: Date.now() }
        : null;
    };
    const onEnd = (event: Event) => {
      const began = start;
      start = null;
      const touch = (event as TouchEvent).changedTouches?.[0];
      if (!began || !touch) return;
      const dx = touch.clientX - began.x;
      const dy = touch.clientY - began.y;
      if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) < Math.abs(dy) * 2 || Date.now() - began.at > SWIPE_MAX_MS) return;
      if (window.getSelection()?.toString()) return;
      const { items: currentItems, value: currentValue } = stateRef.current;
      const index = currentItems.findIndex((item) => item.value === currentValue);
      if (index < 0) return;
      const step = dx < 0 ? 1 : -1;
      for (let next = index + step; next >= 0 && next < currentItems.length; next += step) {
        if (!currentItems[next].disabled) {
          changeRef.current(currentItems[next].value);
          return;
        }
      }
    };
    area.addEventListener('touchstart', onStart, { passive: true });
    area.addEventListener('touchend', onEnd, { passive: true });
    return () => {
      area.removeEventListener('touchstart', onStart);
      area.removeEventListener('touchend', onEnd);
    };
  }, [swipe]);

  if (items.length === 0) return null;

  const focusTab = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const buttons = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    window.requestAnimationFrame(() => buttons?.[index]?.focus());
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = nextEnabledIndex(items, index, 1);
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = nextEnabledIndex(items, index, -1);
    } else if (event.key === 'Home') {
      nextIndex = edgeEnabledIndex(items, 1);
    } else if (event.key === 'End') {
      nextIndex = edgeEnabledIndex(items, -1);
    }
    if (nextIndex == null || nextIndex === index) return;
    event.preventDefault();
    change(items[nextIndex].value);
    focusTab(event, nextIndex);
  };

  // When the current view isn't one of these tabs (a "⋯" panel is open), the first usable tab
  // stays in the Tab order; otherwise every tab was tabIndex -1 and the list was unreachable.
  const hasActive = items.some((item) => item.value === value);
  const fallbackFocusIndex = hasActive ? -1 : edgeEnabledIndex(items, 1);

  return (
    <div
      ref={listRef}
      className={cx('segmented-tabs', listClass, ink && 'segmented-tabs--ink', className)}
      data-more={more || undefined}
      data-tour={tourId}
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation="horizontal"
    >
      {items.map((item, index) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={active}
            aria-label={item.ariaLabel}
            aria-controls={item.panelId}
            tabIndex={active || index === fallbackFocusIndex ? 0 : -1}
            className={cx('segmented-tabs__item', tabClass, item.className, active && 'active')}
            onClick={() => change(item.value)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            disabled={item.disabled}
            title={item.title}
          >
            {item.icon ? <span className="segmented-tabs__icon">{item.icon}</span> : null}
            <span className="segmented-tabs__label">{item.label}</span>
          </button>
        );
      })}
      {ink && inkBox ? (
        <span
          aria-hidden="true"
          className={cx('segmented-tabs__ink', inkBox.ready && 'segmented-tabs__ink--ready')}
          style={{ width: inkBox.width, transform: `translateX(${inkBox.x}px)` }}
        />
      ) : null}
    </div>
  );
}
