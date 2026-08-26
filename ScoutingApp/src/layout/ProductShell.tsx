import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { searchTeams } from '../api';
import { BottomTabBar } from '../components/navigation/BottomTabBar';
import { MobileSearchOverlay } from '../components/navigation/MobileSearchOverlay';
import { MoreSheet } from '../components/navigation/MoreSheet';
import {
  CalendarIcon,
  ClipboardCheckIcon,
  DeltaIcon,
  GridIcon,
  ScoreboardIcon,
  SettingsIcon,
  StarIcon,
  UsersIcon,
} from '../components/ui/Icons';
import { OfflineIndicator } from '../components/ui/OfflineIndicator';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { usePwaInstall } from '../hooks/usePwaInstall';
import { TabTutorialOverlay } from '../components/tutorial/TabTutorialOverlay';
import {
  hasSeenTutorial,
  markTutorialSeen,
  SCOUTING_TUTORIAL_PROGRESS_EVENT,
} from '../tutorial/tutorialState';
import { smartSearchEvents } from '../utils/eventSearch';
import { CURRENT_SEASON_YEAR } from '../pages/centerUtils';
import { matchesRegionFilter } from '../utils/regionFilters';
import { resolveScopeFromPath } from './appUx';
import { useContextStrip } from './useContextStrip';
import { usePrefetchRoutes } from '../hooks/usePrefetchRoutes';
import {
  type QuickJumpMode,
  type TutorialScope,
} from './userSettings';
import { useShellSettingsState } from './useShellSettingsState';
import { Spinner } from '../components/ui/Spinner';
import './ProductShell.css';

/** Conditional className builder – falsy values are dropped. */
function cx(...args: (string | false | null | undefined | 0)[]): string {
  return args.filter(Boolean).join(' ');
}

// Each item carries its icon, so the collapsed rail has something to show.
// Collapsed, every link used to render a 14x3px dash via ::before — eight
// identical dashes, which is a nav you cannot read.
const NAV_ITEMS = [
  { to: '/home', label: 'Home', Icon: GridIcon },
  { to: '/events', label: 'Events', Icon: CalendarIcon },
  { to: '/scouting', label: 'Scouting', Icon: ClipboardCheckIcon },
  { to: '/match-center', label: 'Match Center', Icon: ScoreboardIcon },
  { to: '/team-center', label: 'Team Center', Icon: UsersIcon },
  { to: '/compare', label: 'Compare', Icon: DeltaIcon },
  { to: '/favorites', label: 'Favorites', Icon: StarIcon },
  { to: '/settings', label: 'Settings', Icon: SettingsIcon },
];

function linkClass({ isActive }: { isActive: boolean }) {
  return `ps-nav-link ${isActive ? 'active' : ''}`.trim();
}

type QuickJumpGuess = 'team' | 'event' | 'match' | 'search';
const PAGE_SCROLL_STORAGE_PREFIX = 'scouting_page_scroll:';
const DESKTOP_SIDEBAR_BREAKPOINT_PX = 1120;
const FINDER_COLLAPSED_STORAGE = 'scouting_finder_collapsed';
const SIDEBAR_COLLAPSED_STORAGE = 'scouting_sidebar_collapsed';
const QUICK_JUMP_MODE_OPTIONS: Array<{ value: QuickJumpMode; label: string }> = [
  { value: 'auto', label: 'Auto' },
  { value: 'team', label: 'Team' },
  { value: 'event', label: 'Event' },
];

function normalizedTeamKey(raw: string): string | null {
  const value = raw
    .trim()
    .toLowerCase()
    .replace(/^team\s+/, '')
    .replace(/^#/, '');
  if (!value) return null;
  if (/^frc\d+$/.test(value)) return value;
  if (/^\d{1,5}$/.test(value)) return `frc${String(Number(value))}`;
  return null;
}

function isEventKey(value: string): boolean {
  return /^\d{4}[a-z0-9]+$/.test(value);
}

function parseMatchJump(value: string): { eventKey: string; matchKey: string } | null {
  const normalized = value.trim().toLowerCase();
  if (!normalized.includes('_')) return null;
  const [eventKey] = normalized.split('_');
  if (!isEventKey(eventKey)) return null;
  return { eventKey, matchKey: normalized };
}

function guessQuickJump(raw: string): QuickJumpGuess {
  const value = raw.trim().toLowerCase();
  if (!value) return 'search';
  if (parseMatchJump(value)) return 'match';
  if (normalizedTeamKey(value)) return 'team';
  if (isEventKey(value)) return 'event';
  if (/(^|\s)(team|frc)\s*\d{1,5}$/.test(value)) return 'team';
  if (/(event|regional|district|championship|cmp|week\s*\d+)/.test(value)) return 'event';
  return 'search';
}

function guessLabel(kind: QuickJumpGuess): string {
  if (kind === 'team') return 'Team';
  if (kind === 'event') return 'Event';
  if (kind === 'match') return 'Match';
  return 'Search';
}

function formatEventDisplay(raw: string): string {
  const value = String(raw || '').trim().toLowerCase();
  if (!value) return 'Choose event';
  if (!/^\d{4}[a-z0-9]+$/.test(value)) return value.toUpperCase();
  const year = value.slice(0, 4);
  const code = value.slice(4).toUpperCase();
  return `${year} ${code}`;
}

function formatTeamDisplay(raw: string): string {
  const normalized = normalizedTeamKey(raw) || String(raw || '').trim().toLowerCase();
  if (!normalized) return 'Choose team';
  if (/^frc\d+$/.test(normalized)) return `Team ${normalized.slice(3)}`;
  return normalized.toUpperCase();
}

function BurgerIcon() {
  return (
    <svg className="ps-burger-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path className="line line-top" d="M2.5 4h11" />
      <path className="line line-mid" d="M2.5 8h11" />
      <path className="line line-bot" d="M2.5 12h11" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="7" cy="7" r="4.25" />
      <path d="M10.3 10.3 13.5 13.5" />
    </svg>
  );
}

function PinIcon({ filled }: { filled: boolean }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" width="13" height="13">
      {filled ? (
        <path d="M9.5 1.5 14.5 6.5 10 9 9 15 7 15 6 9 1.5 6.5Z" fill="currentColor" stroke="currentColor" strokeWidth="0.5" strokeLinejoin="round" />
      ) : (
        <path d="M9.5 1.5 14.5 6.5 10 9 9 15 7 15 6 9 1.5 6.5Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      )}
      <path d="M8 9V4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function FinderIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg className="ps-finder-toggle-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <rect x="2" y="2.5" width="12" height="11" rx="2" />
      <path d={collapsed ? 'M6.2 5 9.8 8 6.2 11' : 'M9.8 5 6.2 8 9.8 11'} />
      <path d="M5 2.8v10.4" className="panel-rail" />
    </svg>
  );
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      className={cx('ps-context-toggle-icon', expanded && 'expanded')}
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 6.5 8 10l4-3.5" />
    </svg>
  );
}

export function ProductShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const { online: isOnline, queueSize, isShowingOfflineData } = useOnlineStatus();
  const pwaInstall = usePwaInstall();
  usePrefetchRoutes(location.pathname);
  const {
    jumpMode,
    jumpRegion,
    densityMode,
    themeMode,
    tutorialAutoplay,
    setJumpMode,
  } = useShellSettingsState();
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const contentRef = useRef<HTMLElement | null>(null);
  const [jumpInput, setJumpInput] = useState('');
  const [jumpBusy, setJumpBusy] = useState(false);
  const [mobileMoreOpen, setMobileMoreOpen] = useState(false);
  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const [isDesktopSidebarViewport, setIsDesktopSidebarViewport] = useState<boolean>(
    () => window.innerWidth > DESKTOP_SIDEBAR_BREAKPOINT_PX,
  );
  const [desktopSidebarCollapsed, setDesktopSidebarCollapsed] = useState<boolean>(
    () => window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE) === '1',
  );
  const [desktopFinderCollapsed, setDesktopFinderCollapsed] = useState<boolean>(
    () => window.localStorage.getItem(FINDER_COLLAPSED_STORAGE) === '1',
  );
  const [quickSearchShortcutNonce, setQuickSearchShortcutNonce] = useState(0);
  const [tutorialOpen, setTutorialOpen] = useState<boolean>(false);
  const [tutorialScope, setTutorialScope] = useState<TutorialScope>(() => resolveScopeFromPath(location.pathname));
  const [, setTutorialProgressVersion] = useState<number>(0);

  const {
    contextSnapshot,
    contextStripCollapsed,
    setContextStripCollapsed,
    hasContextStripContent,
    contextMatchLabel,
    contextPins,
    recentEventQuickPicks,
    recentTeamQuickPicks,
    eventPinned,
    teamPinned,
    toggleContextPin,
    openEventQuickPick,
    openTeamQuickPick,
  } = useContextStrip(location.search);

  const guessedTarget = useMemo(() => guessQuickJump(jumpInput), [jumpInput]);
  const activeScope = useMemo(() => resolveScopeFromPath(location.pathname), [location.pathname]);
  const activeTutorialSeen = hasSeenTutorial(activeScope);
  /* Whether the page on screen actually has a finder column.
   *
   * This used to be a list of path prefixes matched with startsWith, so
   * '/scouting' also matched /scouting/pit, /coverage, /calibrate, /record,
   * /assignments and /auto-paths — none of which have a finder. Twelve of the
   * app's twenty-four routes offered a "Collapse Finder" button for a finder
   * that was not there.
   *
   * Observed rather than declared: a page that adds or removes its finder
   * cannot forget to update a list it does not know exists, and the finder
   * appears after its data loads, so a render-time check alone would miss it. */
  // contentRef is declared above with the other shell refs.
  const [hasFinder, setHasFinder] = useState(false);

  useEffect(() => {
    const root = contentRef.current;
    if (!root) return undefined;
    const check = () => setHasFinder(Boolean(root.querySelector('.center-sidebar')));
    check();
    const observer = new MutationObserver(check);
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [location.pathname]);
  const isLiveScoutingRoute = location.pathname === '/scouting' || location.pathname === '/scouting/';
  const contextToggleButton = (
    <button
      type="button"
      className="ps-context-toggle-btn"
      onClick={() => setContextStripCollapsed((current) => !current)}
      aria-expanded={!contextStripCollapsed}
      aria-label={contextStripCollapsed ? 'Expand context bar' : 'Collapse context bar'}
      title={contextStripCollapsed ? 'Expand context bar' : 'Collapse context bar'}
    >
      <span>Context</span>
      <ChevronIcon expanded={!contextStripCollapsed} />
    </button>
  );

  const sidebarCollapsed = isDesktopSidebarViewport && desktopSidebarCollapsed;
  const sidebarCollapseAvailable = isDesktopSidebarViewport;
  const finderCollapseAvailable = isDesktopSidebarViewport && hasFinder && !isLiveScoutingRoute;
  const finderCollapsed = finderCollapseAvailable && desktopFinderCollapsed;
  const isMobileLiveScoutingRoute = !isDesktopSidebarViewport && isLiveScoutingRoute;
  const showTopbarTools = finderCollapseAvailable || !isMobileLiveScoutingRoute;
  const hasContextEvent = Boolean(contextSnapshot.eventKey);
  const hasContextTeam = Boolean(contextSnapshot.teamKey);
  const contextEventLabel = useMemo(() => formatEventDisplay(contextSnapshot.eventKey), [contextSnapshot.eventKey]);
  const contextTeamLabel = useMemo(() => formatTeamDisplay(contextSnapshot.teamKey), [contextSnapshot.teamKey]);
  const contextStripSummary = useMemo(() => {
    const matchLabel = contextSnapshot.matchKey ? contextMatchLabel : 'No match';
    return `Event ${contextEventLabel} · Match ${matchLabel} · Team ${contextTeamLabel}`;
  }, [contextEventLabel, contextMatchLabel, contextSnapshot.matchKey, contextTeamLabel]);

  useEffect(() => {
    window.localStorage.setItem(FINDER_COLLAPSED_STORAGE, desktopFinderCollapsed ? '1' : '0');
  }, [desktopFinderCollapsed]);

  useEffect(() => {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE, desktopSidebarCollapsed ? '1' : '0');
  }, [desktopSidebarCollapsed]);

  useEffect(() => {
    function onTutorialProgressUpdated() {
      setTutorialProgressVersion((current) => current + 1);
    }
    window.addEventListener(SCOUTING_TUTORIAL_PROGRESS_EVENT, onTutorialProgressUpdated as EventListener);
    return () => window.removeEventListener(SCOUTING_TUTORIAL_PROGRESS_EVENT, onTutorialProgressUpdated as EventListener);
  }, []);

  // Sticky elements inside the content need to stack below the sticky topbar
  // (and the offline banner when it's showing) instead of painting over each
  // other. Publish both real heights as CSS variables; ps-foundation-shell.css
  // composes them into --ps-sticky-top, the single anchor every in-page sticky
  // bar uses. Heights vary by viewport, content, and safe-area, so measure.
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return;
    const root = document.documentElement;
    const topbar = document.querySelector<HTMLElement>('.ps-topbar');
    if (!topbar) return;

    const update = () => {
      root.style.setProperty('--ps-topbar-height', `${topbar.offsetHeight}px`);
      const banner = document.querySelector<HTMLElement>('.offline-banner');
      root.style.setProperty(
        '--ps-offline-banner-height',
        banner ? `${banner.offsetHeight}px` : '0px',
      );
    };
    update();

    const observer = new ResizeObserver(update);
    observer.observe(topbar);
    // The offline banner mounts/unmounts and changes height; watch the content
    // subtree so the offset stays correct as it appears and disappears.
    const content = contentRef.current;
    const mutationObserver =
      content && typeof MutationObserver !== 'undefined'
        ? new MutationObserver(() => {
            const banner = document.querySelector<HTMLElement>('.offline-banner');
            if (banner) observer.observe(banner);
            update();
          })
        : null;
    mutationObserver?.observe(content as Node, { childList: true, subtree: true });
    const existingBanner = document.querySelector<HTMLElement>('.offline-banner');
    if (existingBanner) observer.observe(existingBanner);

    return () => {
      observer.disconnect();
      mutationObserver?.disconnect();
    };
  }, []);

  useEffect(() => {
    setTutorialScope(activeScope);
  }, [activeScope]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const isQuickSearchKey = event.key === '/' || event.key === 'Divide' || event.code === 'Slash';
      if (!isQuickSearchKey) return;
      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName?.toLowerCase() || '';
      if (tagName === 'input' || tagName === 'textarea' || target?.isContentEditable) return;
      event.preventDefault();
      if (window.innerWidth > DESKTOP_SIDEBAR_BREAKPOINT_PX) {
        setQuickSearchShortcutNonce((current) => current + 1);
        return;
      }
      searchInputRef.current?.focus();
      searchInputRef.current?.select();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    if (!quickSearchShortcutNonce) return;
    const raf = window.requestAnimationFrame(() => {
      const input = searchInputRef.current;
      if (!input) return;
      input.focus();
      input.select();
    });
    return () => window.cancelAnimationFrame(raf);
  }, [quickSearchShortcutNonce]);

  useEffect(() => {
    const node = contentRef.current;
    if (!node) return;
    const storageKey = `${PAGE_SCROLL_STORAGE_PREFIX}${location.pathname}${location.search}`;
    const saved = Number(window.sessionStorage.getItem(storageKey) || '');
    if (Number.isFinite(saved) && saved > 0) {
      node.scrollTo({ top: saved, behavior: 'auto' });
      return;
    }
    node.scrollTo({ top: 0, behavior: 'auto' });
  }, [location.pathname, location.search]);

  useEffect(() => {
    function handleResize() {
      const desktopViewport = window.innerWidth > DESKTOP_SIDEBAR_BREAKPOINT_PX;
      setIsDesktopSidebarViewport(desktopViewport);
    }
    window.addEventListener('resize', handleResize, { passive: true });
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    const node = contentRef.current;
    if (!node) return;
    const storageKey = `${PAGE_SCROLL_STORAGE_PREFIX}${location.pathname}${location.search}`;
    const onScroll = () => {
      window.sessionStorage.setItem(storageKey, String(Math.max(0, Math.round(node.scrollTop))));
    };
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => node.removeEventListener('scroll', onScroll);
  }, [location.pathname, location.search]);

  async function runGlobalJump(rawInput?: string) {
    const value = String(rawInput ?? jumpInput).trim().toLowerCase();
    if (!value) return;

    const activeMode: QuickJumpGuess = jumpMode === 'auto' ? guessedTarget : jumpMode;
    const region = jumpRegion;

    if (activeMode === 'match') {
      const matchJump = parseMatchJump(value);
      if (matchJump) {
        navigate(`/match-center?event=${matchJump.eventKey}&match=${matchJump.matchKey}`);
        return;
      }
    }

    if (activeMode === 'team') {
      const teamKey = normalizedTeamKey(value);
      if (!teamKey) {
        try {
          const payload = await searchTeams(value, 40);
          const filtered = payload.teams.filter((team) =>
            matchesRegionFilter(region, team.state || null, team.country || null),
          );
          const selected = filtered[0] || payload.teams[0];
          if (selected?.team_key) {
            const params = new URLSearchParams();
            params.set('team', selected.team_key.toLowerCase());
            navigate(`/team-center?${params.toString()}`);
            return;
          }
        } catch {
          // fallback below
        }
        navigate(`/team-center`);
        return;
      }
      const params = new URLSearchParams();
      params.set('team', teamKey);
      navigate(`/team-center?${params.toString()}`);
      return;
    }

    if (activeMode === 'event') {
      if (isEventKey(value)) {
        navigate(`/events?event=${value}`);
        return;
      }
      try {
        const resolved = await smartSearchEvents(value, {
          preferredYear: CURRENT_SEASON_YEAR,
          fallbackYear: CURRENT_SEASON_YEAR - 1,
          maxResults: 60,
        });
        const filtered = resolved.events.filter((event) =>
          matchesRegionFilter(region, event.state_prov || null, event.country || null),
        );
        const selected = filtered[0] || resolved.bestMatch;
        if (selected?.event_key) {
          navigate(`/events?event=${selected.event_key.toLowerCase()}`);
          return;
        }
      } catch {
        // fallback below
      }
      const params = new URLSearchParams();
      params.set('q', value);
      if (region !== 'all') params.set('region', region);
      navigate(`/events?${params.toString()}`);
      return;
    }

    if (activeMode === 'search') {
      const params = new URLSearchParams();
      params.set('q', value);
      if (region !== 'all') params.set('region', region);
      navigate(`/events?${params.toString()}`);
      return;
    }

    if (jumpMode === 'auto') {
      if (isEventKey(value)) {
        navigate(`/events?event=${value}`);
        return;
      }
      const matchJump = parseMatchJump(value);
      if (matchJump) {
        navigate(`/match-center?event=${matchJump.eventKey}&match=${matchJump.matchKey}`);
        return;
      }
    }

    const params = new URLSearchParams();
    params.set('q', value);
    if (region !== 'all') params.set('region', region);
    navigate(`/events?${params.toString()}`);
  }

  function handleJumpSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (jumpBusy) return;
    setJumpBusy(true);
    void runGlobalJump().finally(() => setJumpBusy(false));
  }

  const placeholder =
    jumpMode === 'team'
      ? 'Team key or number (e.g. frc118 or 118)'
      : jumpMode === 'event'
        ? 'Try "houston district 2026" or "2026txhou"'
        : 'Jump to team, event, or match…';

  // Only worth saying once there is something to classify. On an empty field
  // the guess falls back to "Search", so the hint rendered as "Detected input
  // type: Search" on every page — uppercased by the stylesheet, which made a
  // real feature read like leftover debug output.
  const helperLabel = !jumpInput.trim()
    ? ''
    : jumpMode === 'auto'
      ? `Detected input type: ${guessLabel(guessedTarget)}`
      : `Searching as: ${guessLabel(jumpMode)}`;

  const openTutorial = useCallback((scope: TutorialScope) => {
    markTutorialSeen(scope);
    setTutorialScope(scope);
    setTutorialOpen(true);
  }, []);

  useEffect(() => {
    if (!tutorialAutoplay) return;
    if (activeTutorialSeen) return;
    if (tutorialOpen) return;
    openTutorial(activeScope);
  }, [activeScope, activeTutorialSeen, openTutorial, tutorialAutoplay, tutorialOpen]);

  return (
    <div
      className={cx(
        'product-shell',
        `theme-${themeMode}`,
        `density-${densityMode}`,
        sidebarCollapsed && 'sidebar-collapsed',
        finderCollapsed && 'finder-collapsed',
        sidebarCollapseAvailable && 'sidebar-collapse-available',
        finderCollapseAvailable && 'finder-collapse-available',
      )}
    >
      <aside
        className="ps-sidebar"
        aria-label="Primary navigation"
      >
        <div className="ps-brand">
          <picture>
            <source srcSet="/Heading.webp" type="image/webp" />
            <img src="/Heading.png" alt="FRCMOB logo" className="ps-brand-logo" decoding="async" />
          </picture>
          <div>
            <strong>FRCMOB</strong>
          </div>
        </div>
        {sidebarCollapseAvailable ? (
          <button
            type="button"
            className={cx('ps-sidebar-toggle', sidebarCollapsed && 'collapsed')}
            onClick={() => setDesktopSidebarCollapsed((current) => !current)}
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-pressed={sidebarCollapsed}
          >
            <span className="ps-sidebar-toggle-icon" aria-hidden="true">
              <svg viewBox="0 0 16 16" focusable="false">
                <path d={sidebarCollapsed ? 'M6 3.5 10.5 8 6 12.5' : 'M10 3.5 5.5 8 10 12.5'} />
              </svg>
            </span>
            {/* The region, not the verb. Three panel toggles were on screen at
                once, all reading "Collapse", which said nothing about which
                panel each one hid. The chevron shows the direction. */}
            <span className="ps-sidebar-toggle-label">Menu</span>
          </button>
        ) : null}
        <nav className="ps-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={linkClass}
              title={sidebarCollapsed ? item.label : undefined}
            >
              <item.Icon className="ps-nav-link-icon" />
              <span className="ps-nav-link-label">{item.label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="ps-main-area">
        <header className="ps-topbar">
          <div className="ps-topbar-title">
            <button
              type="button"
              className={cx('ps-mobile-burger-btn', mobileMoreOpen && 'active')}
              onClick={() => setMobileMoreOpen((current) => !current)}
              aria-expanded={mobileMoreOpen}
              aria-controls="mobile-more-sheet"
              aria-label={mobileMoreOpen ? 'Close navigation menu' : 'Open navigation menu'}
            >
              <BurgerIcon />
            </button>
            {/* Only where the sidebar is not already showing it. This was
                rendered on every screen with aria-hidden when the sidebar was
                visible — hidden from a screen reader while still occupying the
                top of the page twice. The byline moved to Settings > About:
                permanent chrome is for things you navigate with. */}
            {!isDesktopSidebarViewport ? (
              <span className="ps-topbar-brand-meta">
                <span className="ps-topbar-brand-name">FRCMOB</span>
              </span>
            ) : null}
            <span
              className={cx('ps-network-pill', isOnline ? 'online' : 'offline', queueSize > 0 && 'has-queue')}
              title={
                queueSize > 0
                  ? `${queueSize} pending offline change${queueSize === 1 ? '' : 's'}`
                  : isOnline
                    ? 'Online'
                    : 'Offline'
              }
            >
              {isOnline ? 'Online' : 'Offline'}
              {queueSize > 0 ? <span className="ps-network-queue-badge">{queueSize}</span> : null}
            </span>
            <button
              type="button"
              className="ps-mobile-search-trigger"
              onClick={() => setMobileSearchOpen(true)}
              aria-label="Open search"
            >
              <SearchIcon />
            </button>
          </div>
          {showTopbarTools ? (
          <div className="ps-topbar-tools">
            {finderCollapseAvailable ? (
              <button
                type="button"
                className={cx('ps-finder-toggle', finderCollapsed && 'active')}
                onClick={() => setDesktopFinderCollapsed((current) => !current)}
                aria-pressed={finderCollapsed}
                aria-label={finderCollapsed ? 'Expand finder' : 'Collapse finder'}
                title={finderCollapsed ? 'Expand finder' : 'Collapse finder'}
              >
                <FinderIcon collapsed={finderCollapsed} />
                <span>Finder</span>
              </button>
            ) : null}
            {!isMobileLiveScoutingRoute ? (
              <div className="ps-topbar-search">
                <form className="ps-topbar-search-form" onSubmit={handleJumpSubmit}>
                  <div className="ps-topbar-search-modes" role="group" aria-label="Quick search mode">
                    {QUICK_JUMP_MODE_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        className={cx('ps-topbar-search-mode-pill', jumpMode === option.value && 'active')}
                        onClick={() => setJumpMode(option.value)}
                        aria-pressed={jumpMode === option.value}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                  <input
                    ref={searchInputRef}
                    type="search"
                    enterKeyHint="search"
                    value={jumpInput}
                    onChange={(event) => setJumpInput(event.target.value)}
                    placeholder={placeholder}
                    aria-label="Global quick jump"
                  />
                  <button
                    type="submit"
                    className="ps-topbar-search-btn"
                    aria-label={jumpBusy ? 'Searching' : 'Search'}
                    disabled={jumpBusy}
                  >
                    {jumpBusy ? <Spinner size={14} /> : <SearchIcon />}
                    <span className="ps-topbar-search-btn-label">{jumpBusy ? '' : 'Search'}</span>
                  </button>
                </form>
                {helperLabel ? <div className="ps-topbar-search-hint">{helperLabel}</div> : null}
              </div>
            ) : null}
          </div>
          ) : null}
        </header>

        {!isMobileLiveScoutingRoute && hasContextStripContent ? (
        <section
          className={cx('ps-context-strip', contextStripCollapsed && 'collapsed')}
          aria-label="Active context and quick picks"
          data-onboard="shell-context"
        >
          {/* The header row this replaces held one uppercase label — "QUICK
              CONTEXT" — and a collapse toggle, on a line of its own above the
              chips. The chips say what they are ("Event: 2026 ARC"), so the
              label named a region nobody needed named, and the toggle now sits
              at the end of the row it collapses. One line instead of two, on
              every screen in the app. */}
          {contextStripCollapsed ? (
            <div className="ps-context-current">
              <p className="ps-context-summary">{contextStripSummary}</p>
              {contextToggleButton}
            </div>
          ) : (
          <>
          <div className="ps-context-current">
            <div className={cx('ps-context-chip-group', hasContextEvent && 'active')}>
              <button
                type="button"
                className={cx('ps-context-chip', 'event', hasContextEvent && 'active')}
                onClick={() => {
                  if (contextSnapshot.eventKey) {
                    openEventQuickPick(contextSnapshot.eventKey);
                    return;
                  }
                  navigate('/events');
                }}
                title={hasContextEvent ? `Open event ${contextEventLabel}` : 'Choose an event'}
              >
                Event: <strong>{contextEventLabel}</strong>
              </button>
              {contextSnapshot.eventKey ? (
                <button
                  type="button"
                  className={cx('ps-context-pin-btn', eventPinned && 'active')}
                  onClick={() => toggleContextPin('event', contextSnapshot.eventKey)}
                  aria-label={eventPinned ? 'Unpin current event' : 'Pin current event'}
                  title={eventPinned ? 'Unpin' : 'Pin'}
                >
                  <PinIcon filled={eventPinned} />
                </button>
              ) : null}
            </div>
            <span className={cx('ps-context-chip', 'match', contextSnapshot.matchKey && 'active')}>
              Match: <strong>{contextMatchLabel}</strong>
            </span>
            <div className={cx('ps-context-chip-group', hasContextTeam && 'active')}>
              <button
                type="button"
                className={cx('ps-context-chip', 'team', hasContextTeam && 'active')}
                onClick={() => {
                  if (contextSnapshot.teamKey) {
                    openTeamQuickPick(contextSnapshot.teamKey);
                    return;
                  }
                  navigate('/team-center');
                }}
                title={hasContextTeam ? `Open ${contextTeamLabel}` : 'Choose a team'}
              >
                Team: <strong>{contextTeamLabel}</strong>
              </button>
              {contextSnapshot.teamKey ? (
                <button
                  type="button"
                  className={cx('ps-context-pin-btn', teamPinned && 'active')}
                  onClick={() => toggleContextPin('team', contextSnapshot.teamKey)}
                  aria-label={teamPinned ? 'Unpin current team' : 'Pin current team'}
                  title={teamPinned ? 'Unpin' : 'Pin'}
                >
                  <PinIcon filled={teamPinned} />
                </button>
              ) : null}
            </div>
            {contextToggleButton}
          </div>
          <div className="ps-context-quick-picks">
            {(contextPins.event.length > 0 || contextPins.team.length > 0) ? (
              <div className="ps-context-quick-row">
                <span className="ps-context-quick-label">Pinned</span>
                {contextPins.event.map((eventKey) => (
                  <button
                    key={`pin-event-${eventKey}`}
                    type="button"
                    className="ps-context-quick-chip event"
                    onClick={() => openEventQuickPick(eventKey)}
                  >
                    {formatEventDisplay(eventKey)}
                  </button>
                ))}
                {contextPins.team.map((teamKey) => (
                  <button
                    key={`pin-team-${teamKey}`}
                    type="button"
                    className="ps-context-quick-chip team"
                    onClick={() => openTeamQuickPick(teamKey)}
                  >
                    {formatTeamDisplay(teamKey)}
                  </button>
                ))}
              </div>
            ) : null}
            {(recentEventQuickPicks.length > 0 || recentTeamQuickPicks.length > 0) ? (
              <div className="ps-context-quick-row">
                <span className="ps-context-quick-label">Recent</span>
                {recentEventQuickPicks.map((eventKey) => (
                  <button
                    key={`recent-event-${eventKey}`}
                    type="button"
                    className="ps-context-quick-chip event"
                    onClick={() => openEventQuickPick(eventKey)}
                  >
                    {formatEventDisplay(eventKey)}
                  </button>
                ))}
                {recentTeamQuickPicks.map((teamKey) => (
                  <button
                    key={`recent-team-${teamKey}`}
                    type="button"
                    className="ps-context-quick-chip team"
                    onClick={() => openTeamQuickPick(teamKey)}
                  >
                    {formatTeamDisplay(teamKey)}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          </>
          )}
        </section>
        ) : null}

        <main
          ref={contentRef}
          className="ps-content"
        >
          <OfflineIndicator />
          {isShowingOfflineData ? (
            <div
              role="status"
              aria-live="polite"
              className="offline-banner offline-banner-warning"
            >
              <span className="offline-banner-status">
                <span className="offline-banner-dot offline-dot-warning" />
                Showing data from last session
              </span>
            </div>
          ) : null}
          {pwaInstall.canPrompt && !isDesktopSidebarViewport && (location.pathname === '/home' || location.pathname.startsWith('/home/')) ? (
            <div className="ps-pwa-install-banner">
              <span>Install FRCMOB for offline access and a native app experience.</span>
              <button type="button" className="center-btn" onClick={pwaInstall.promptInstall}>Install</button>
              <button type="button" className="ps-pwa-dismiss" onClick={pwaInstall.dismiss} aria-label="Dismiss">×</button>
            </div>
          ) : null}
          <Outlet />
        </main>
      </div>

      <MoreSheet
        open={mobileMoreOpen}
        onClose={() => setMobileMoreOpen(false)}
      />

      <MobileSearchOverlay
        open={mobileSearchOpen}
        onClose={() => setMobileSearchOpen(false)}
        onSubmit={(query) => {
          setJumpInput(query);
          setJumpBusy(true);
          void runGlobalJump(query).finally(() => setJumpBusy(false));
        }}
        busy={jumpBusy}
        jumpMode={jumpMode}
        jumpRegion={jumpRegion}
      />

      {tutorialOpen ? <TabTutorialOverlay key={tutorialScope} scope={tutorialScope} onClose={() => setTutorialOpen(false)} /> : null}

      <BottomTabBar
        moreOpen={mobileMoreOpen}
        onToggleMore={() => setMobileMoreOpen((c) => !c)}
      />
    </div>
  );
}
