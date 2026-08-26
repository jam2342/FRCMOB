import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { NavLink, useLocation } from 'react-router-dom';

export type ViewBarItem = {
  label: string;
  to: string;
  preserveSearch?: boolean;
  /** A tool rather than a peer view. Rendered after a divider and quieter.
   *  Field Calibration and On-Device Breakdown sat beside Live Scouting as
   *  equals, which said the app's main job and a camera-calibration utility
   *  were the same kind of thing. */
  secondary?: boolean;
};

const MOBILE_MEDIA_QUERY = '(max-width: 1120px)';

function readIsMobile(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(MOBILE_MEDIA_QUERY).matches;
}

function useIsMobile(onChange?: (matches: boolean) => void): boolean {
  const [isMobile, setIsMobile] = useState(readIsMobile);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const mq = window.matchMedia(MOBILE_MEDIA_QUERY);
    const update = () => {
      const matches = mq.matches;
      setIsMobile(matches);
      onChange?.(matches);
    };
    update();
    mq.addEventListener?.('change', update);
    return () => mq.removeEventListener?.('change', update);
  }, [onChange]);
  return isMobile;
}

function ChevronIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
      <path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Segmented bar for switching between related sub-views. With
// collapseToMenuOnMobile it folds into a single dropdown pill on small
// screens, so it doesn't stack a second full-width bar onto the already
// bar-heavy scouting layout.
export function PageViewBar({
  items,
  className = '',
  collapseToMenuOnMobile = false,
}: {
  items: readonly ViewBarItem[];
  className?: string;
  collapseToMenuOnMobile?: boolean;
}) {
  const location = useLocation();
  const navRef = useRef<HTMLElement | null>(null);
  // Track whether content is hidden off either edge so CSS can show a fade hint.
  const [edges, setEdges] = useState({ left: false, right: false });

  // Dropdown state for the collapsed (mobile) form.
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenuOnDesktop = useCallback((matches: boolean) => {
    if (!matches) setMenuOpen(false);
  }, []);
  const isMobile = useIsMobile(closeMenuOnDesktop);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [menuPos, setMenuPos] = useState({ top: 0, left: 0, width: 0 });
  const showCollapsedMenu = collapseToMenuOnMobile && isMobile;

  const toFor = useCallback(
    (item: ViewBarItem) =>
      item.preserveSearch && location.search
        ? { pathname: item.to, search: location.search }
        : item.to,
    [location.search],
  );

  const updateEdges = useCallback(() => {
    const el = navRef.current;
    if (!el) return;
    const left = el.scrollLeft > 2;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
    setEdges((prev) => (prev.left === left && prev.right === right ? prev : { left, right }));
  }, []);

  // Center the active tab horizontally within the bar only — never call
  // scrollIntoView, which also scrolls ancestor/page scrollers and makes the
  // whole page jump when a tab is tapped while scrolled down.
  useLayoutEffect(() => {
    if (showCollapsedMenu) return;
    const el = navRef.current;
    if (!el) return;
    const active = el.querySelector<HTMLElement>('.page-view-tab.active');
    if (active) {
      const navRect = el.getBoundingClientRect();
      const aRect = active.getBoundingClientRect();
      const delta = aRect.left - navRect.left - (el.clientWidth - aRect.width) / 2;
      el.scrollTo?.({ left: el.scrollLeft + delta, behavior: 'smooth' });
    }
    updateEdges();
  }, [location.pathname, location.search, showCollapsedMenu, updateEdges]);

  useEffect(() => {
    if (showCollapsedMenu) return undefined;
    const el = navRef.current;
    if (!el) return undefined;
    updateEdges();
    el.addEventListener('scroll', updateEdges, { passive: true });
    window.addEventListener('resize', updateEdges);
    return () => {
      el.removeEventListener('scroll', updateEdges);
      window.removeEventListener('resize', updateEdges);
    };
  }, [showCollapsedMenu, updateEdges, items.length]);

  // Position the dropdown under its trigger (fixed, portaled — escapes any
  // overflow:hidden ancestor that would otherwise clip it).
  const placeMenu = useCallback(() => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setMenuPos({ top: rect.bottom + 6, left: rect.left, width: rect.width });
  }, []);

  // Place on open and keep it pinned while scrolling/resizing (mirrors
  // EventPicker's portal dropdown).
  useLayoutEffect(() => {
    if (!menuOpen || !showCollapsedMenu) return;
    placeMenu();
    window.addEventListener('scroll', placeMenu, true);
    window.addEventListener('resize', placeMenu);
    return () => {
      window.removeEventListener('scroll', placeMenu, true);
      window.removeEventListener('resize', placeMenu);
    };
  }, [menuOpen, placeMenu, showCollapsedMenu]);

  useEffect(() => {
    if (!menuOpen || !showCollapsedMenu) return undefined;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) {
        setMenuOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [menuOpen, showCollapsedMenu]);

  // No route-change effect needed: each scouting page renders its own
  // PageViewBar, so navigating between tools remounts this with a fresh closed
  // menu; selecting an item also closes it via onClick below.

  if (items.length < 2) return null;

  if (showCollapsedMenu) {
    const active = items.find((item) => item.to === location.pathname) ?? items[0];
    const wrapClassName = ['page-view-menu', className].filter(Boolean).join(' ');
    return (
      <div className={wrapClassName}>
        <button
          ref={triggerRef}
          type="button"
          className="page-view-menu-trigger center-btn ghost"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="Switch scouting tool"
          onClick={() => setMenuOpen((open) => !open)}
        >
          <span className="page-view-menu-current">{active.label}</span>
          <ChevronIcon />
        </button>
        {menuOpen
          ? createPortal(
              <div
                ref={menuRef}
                className="page-view-menu-popover"
                role="menu"
                aria-label="Page view"
                style={{ top: menuPos.top, left: menuPos.left, minWidth: menuPos.width }}
              >
                {items.map((item, index) => (
                  <Fragment key={item.to}>
                  {index === items.findIndex((entry) => entry.secondary)
                    ? <span className="page-view-menu-divider" role="separator" />
                    : null}
                  <NavLink
                    to={toFor(item)}
                    end
                    role="menuitem"
                    className={({ isActive }) =>
                      `page-view-menu-item${item.secondary ? ' page-view-menu-item--secondary' : ''}${isActive ? ' active' : ''}`
                    }
                    onClick={() => setMenuOpen(false)}
                  >
                    {item.label}
                  </NavLink>
                  </Fragment>
                ))}
              </div>,
              document.body,
            )
          : null}
      </div>
    );
  }

  const navClassName = [
    'page-view-bar segmented-tabs',
    edges.left && 'can-scroll-left',
    edges.right && 'can-scroll-right',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  // A divider once, before the first secondary item — not between every pair.
  const firstSecondary = items.findIndex((item) => item.secondary);
  return (
    <nav ref={navRef} className={navClassName} aria-label="Page view">
      {items.map((item, index) => (
        <Fragment key={item.to}>
          {index === firstSecondary ? <span className="page-view-divider" aria-hidden="true" /> : null}
          <NavLink
            to={toFor(item)}
            end
            className={({ isActive }) =>
              `page-view-tab segmented-tabs__item${item.secondary ? ' page-view-tab--secondary' : ''}${isActive ? ' active' : ''}`
            }
          >
            {item.label}
          </NavLink>
        </Fragment>
      ))}
    </nav>
  );
}
