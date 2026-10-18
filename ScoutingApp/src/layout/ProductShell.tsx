import { QuietBoundary } from '../components/ui/QuietBoundary';
import { cx } from '../components/ui/primitives/cx';
import { type FormEvent, Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { searchTeams } from '../api';
import { BottomTabBar } from '../components/navigation/BottomTabBar';
import { BrandWordmark } from './BrandWordmark';
import { MobileSearchOverlay } from '../components/navigation/MobileSearchOverlay';
import { MoreSheet } from '../components/navigation/MoreSheet';
import {
  CalendarIcon,
  ClipboardCheckIcon,
  DeltaIcon,
  GridIcon,
  ScoreboardIcon,
  SettingsIcon,
  ShieldIcon,
  StarIcon,
  UsersIcon,
} from '../components/ui/Icons';
import { OfflineIndicator } from '../components/ui/OfflineIndicator';
import { ToastHost } from '../components/ui/ToastHost';
import { PullToRefresh } from '../components/ui/PullToRefresh';
import { PastSeasonBanner } from '../features/seasons/PastSeasonBanner';
import { SiteFooter } from '../components/SiteFooter';
import paletteStyles from './PaletteTrigger.module.css';
import { ThemeToggle } from './ThemeToggle';
import themeToggleStyles from './ThemeToggle.module.css';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { usePwaInstall } from '../hooks/usePwaInstall';
import { TourPrompt } from '../components/tutorial/TourPrompt';
import { dismissTourPrompt, tourPromptDismissed } from '../tutorial/tourPrompt';
import {
  countSeenTutorials,
  hasSeenTutorial,
  markTutorialSeen,
  SCOUTING_TUTORIAL_PROGRESS_EVENT,
} from '../tutorial/tutorialState';
import { smartSearchEvents } from '../utils/eventSearch';
import { CURRENT_SEASON_YEAR } from '../pages/centerUtils';
import { matchesRegionFilter } from '../utils/regionFilters';
import { resolveScopeFromPath } from './appUx';
import { buildContextSummary, useContextStrip } from './useContextStrip';
import { typedTeamName } from './quickJump';
import { eventDisplayName } from '../utils/eventSearchIndex';
import { useEventNames } from '../hooks/useEventNames';
import { type TutorialScope } from './userSettings';
import { useShellSettingsState } from './useShellSettingsState';
import { Spinner } from '../components/ui/Spinner';
import { isPlainClick, navigateWithTransition, notifyRouteCommitted, sectionDirection } from '../utils/viewTransition';
import './ProductShell.css';
import { storageGet, storageSet } from '../platform/safeStorage';


// Each item carries its icon, so the collapsed rail has something to show.
// Collapsed, every link used to render a 14x3px dash via ::before — eight
// identical dashes, which is a nav you cannot read.
// The tour (its steps and mascot) loads only when someone opens it; it no longer
// starts on its own, so it was ~50 KB of every first page load for nothing.
const TabTutorialOverlay = lazy(() =>
  import('../components/tutorial/TabTutorialOverlay').then((module) => ({ default: module.TabTutorialOverlay })),
);

// Extras that aren't needed to show the first page: their code loads after it (the palette only
// when first opened), so the startup script stays the size the speed work got it to.
const CommandPalette = lazy(() => import('../features/palette/CommandPalette').then((module) => ({ default: module.CommandPalette })));
const TeamPeek = lazy(() => import('../features/teamPeek/TeamPeek').then((module) => ({ default: module.TeamPeek })));
const NextMatchPill = lazy(() => import('../features/nextMatch/NextMatchPill').then((module) => ({ default: module.NextMatchPill })));

const NAV_ITEMS = [
  { to: '/home', label: 'Home', Icon: GridIcon },
  { to: '/events', label: 'Events', Icon: CalendarIcon },
  { to: '/scouting', label: 'Scouting', Icon: ClipboardCheckIcon },
  { to: '/my-team', label: 'My Team', Icon: ShieldIcon },
  { to: '/match-center', label: 'Match Center', Icon: ScoreboardIcon },
  { to: '/team-center', label: 'Team Center', Icon: UsersIcon },
  { to: '/compare', label: 'Compare', Icon: DeltaIcon },
  { to: '/favorites', label: 'Favorites', Icon: StarIcon },
  { to: '/settings', label: 'Settings', Icon: SettingsIcon },
];

const NAV_ORDER = NAV_ITEMS.map((item) => item.to);

function linkClass({ isActive }: { isActive: boolean }) {
  return `ps-nav-link ${isActive ? 'active' : ''}`.trim();
}

type QuickJumpGuess = 'team' | 'event' | 'match' | 'search';
const PAGE_SCROLL_STORAGE_PREFIX = 'scouting_page_scroll:';
const DESKTOP_SIDEBAR_BREAKPOINT_PX = 1120;
const FINDER_COLLAPSED_STORAGE = 'scouting_finder_collapsed';
const SIDEBAR_COLLAPSED_STORAGE = 'scouting_sidebar_collapsed';

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
  return String(raw || '').trim() ? eventDisplayName(raw) : 'Choose event';
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
  // Re-renders once the shared event index loads, so chips switch from codes to names.
  useEventNames();
  const navigate = useNavigate();
  const location = useLocation();
  const { online: isOnline, queueSize, isShowingOfflineData } = useOnlineStatus();
  const pwaInstall = usePwaInstall();
  const {
    jumpRegion,
    densityMode,
    themeMode,
    tutorialAutoplay,
  } = useShellSettingsState();
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const contentRef = useRef<HTMLElement | null>(null);
  // Tells a running page transition the new page is on screen.
  useLayoutEffect(() => {
    notifyRouteCommitted();
  }, [location.key]);
  const [jumpInput, setJumpInput] = useState('');
  const [jumpBusy, setJumpBusy] = useState(false);
  const [mobileMoreOpen, setMobileMoreOpen] = useState(false);
  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const [isDesktopSidebarViewport, setIsDesktopSidebarViewport] = useState<boolean>(
    () => window.innerWidth > DESKTOP_SIDEBAR_BREAKPOINT_PX,
  );
  const [desktopSidebarCollapsed, setDesktopSidebarCollapsed] = useState<boolean>(
    () => storageGet(SIDEBAR_COLLAPSED_STORAGE) === '1',
  );
  const [desktopFinderCollapsed, setDesktopFinderCollapsed] = useState<boolean>(
    () => storageGet(FINDER_COLLAPSED_STORAGE) === '1',
  );
  const [quickSearchShortcutNonce, setQuickSearchShortcutNonce] = useState(0);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Each opening starts clean (a fresh query and highlight).
  const [paletteSession, setPaletteSession] = useState(0);
  const paletteOpenRef = useRef(paletteOpen);
  paletteOpenRef.current = paletteOpen;
  // Any route change closes the sheets that cover the page: a bottom tab tapped behind
  // the More sheet, or the phone's Back button, used to leave them open over the new
  // page (with scrolling still locked under them).
  useEffect(() => {
    setMobileMoreOpen(false);
    setMobileSearchOpen(false);
    setPaletteOpen(false);
  }, [location.key]);
  const openPalette = useCallback(() => {
    setPaletteSession((current) => current + 1);
    setPaletteOpen(true);
  }, []);
  const paletteShortcut = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? '⌘K' : 'Ctrl K';
  const [tutorialOpen, setTutorialOpen] = useState<boolean>(false);
  const [tutorialScope, setTutorialScope] = useState<TutorialScope>(() => resolveScopeFromPath(location.pathname));
  const [, setTutorialProgressVersion] = useState<number>(0);

  const {
    contextSnapshot,
    contextStripCollapsed,
    setContextStripCollapsed,
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
  const contextEventLabel = formatEventDisplay(contextSnapshot.eventKey);
  const contextTeamLabel = useMemo(() => formatTeamDisplay(contextSnapshot.teamKey), [contextSnapshot.teamKey]);
  const contextStripSummary = useMemo(() => {
    return buildContextSummary(
      hasContextEvent ? contextEventLabel : '',
      contextSnapshot.matchKey ? contextMatchLabel : '',
      hasContextTeam ? contextTeamLabel : '',
    );
  }, [contextEventLabel, contextMatchLabel, contextSnapshot.matchKey, contextTeamLabel, hasContextEvent, hasContextTeam]);

  useEffect(() => {
    storageSet(FINDER_COLLAPSED_STORAGE, desktopFinderCollapsed ? '1' : '0');
  }, [desktopFinderCollapsed]);

  useEffect(() => {
    storageSet(SIDEBAR_COLLAPSED_STORAGE, desktopSidebarCollapsed ? '1' : '0');
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
      // Offline and stale-data banners can show together; sticky bars must clear both.
      const bannerHeight = Array.from(document.querySelectorAll<HTMLElement>('.offline-banner'))
        .reduce((sum, banner) => sum + banner.offsetHeight, 0);
      root.style.setProperty('--ps-offline-banner-height', `${bannerHeight}px`);
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
            document.querySelectorAll<HTMLElement>('.offline-banner').forEach((banner) => observer.observe(banner));
            update();
          })
        : null;
    mutationObserver?.observe(content as Node, { childList: true, subtree: true });
    document.querySelectorAll<HTMLElement>('.offline-banner').forEach((banner) => observer.observe(banner));

    return () => {
      observer.disconnect();
      mutationObserver?.disconnect();
    };
  }, []);

  useEffect(() => {
    setTutorialScope(activeScope);
  }, [activeScope]);

  // ⌘K / Ctrl+K opens the palette from anywhere, typing included (it's the one shortcut people
  // expect to work inside a field).
  useEffect(() => {
    function onPaletteKey(event: KeyboardEvent) {
      if (event.key.toLowerCase() !== 'k' || !(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
      event.preventDefault();
      if (paletteOpenRef.current) setPaletteOpen(false);
      else openPalette();
    }
    window.addEventListener('keydown', onPaletteKey);
    return () => window.removeEventListener('keydown', onPaletteKey);
  }, [openPalette]);

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

  // Back to a page puts you where you were. Phones scroll the window, wider screens the content
  // pane, so both are saved and restored. The page is usually still filling in when it returns
  // and the browser can't scroll past content that isn't there yet, so the restore keeps trying
  // while it grows, and stops the moment you scroll or touch.
  useEffect(() => {
    try {
      window.history.scrollRestoration = 'manual';
    } catch {
      // Not settable here; the restore below still runs.
    }
  }, []);

  useEffect(() => {
    const node = contentRef.current;
    if (!node) return undefined;
    const storageKey = `${PAGE_SCROLL_STORAGE_PREFIX}${location.pathname}${location.search}`;
    const read = (key: string) => {
      const value = Number(window.sessionStorage.getItem(key) || '');
      return Number.isFinite(value) && value > 0 ? value : 0;
    };
    const savedPane = read(storageKey);
    const savedWindow = read(`${storageKey}#window`);
    if (!savedPane && !savedWindow) {
      node.scrollTo({ top: 0, behavior: 'auto' });
      window.scrollTo({ top: 0, behavior: 'auto' });
      return undefined;
    }
    let stopped = false;
    let timer: number | null = null;
    const startedAt = performance.now();
    const stop = () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
    };
    const attempt = () => {
      if (stopped) return;
      if (savedPane) node.scrollTo({ top: savedPane, behavior: 'auto' });
      if (savedWindow) window.scrollTo({ top: savedWindow, behavior: 'auto' });
      const reached = Math.abs(node.scrollTop - savedPane) <= 2 && Math.abs(window.scrollY - savedWindow) <= 2;
      if (reached || performance.now() - startedAt > 2500) return;
      timer = window.setTimeout(attempt, 100);
    };
    attempt();
    const inputs = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;
    inputs.forEach((type) => window.addEventListener(type, stop, { passive: true, capture: true }));
    return () => {
      stop();
      inputs.forEach((type) => window.removeEventListener(type, stop, { capture: true }));
    };
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
    const route = `${location.pathname}${location.search}`;
    const storageKey = `${PAGE_SCROLL_STORAGE_PREFIX}${route}`;
    // Only while this page is the one on screen: leaving it, the next (shorter) page makes the
    // browser clamp the scroll, and saving that clamped spot is what sent Back ~140px short.
    const stillHere = () => window.location.hash.replace(/^#/, '') === route;
    const onPaneScroll = () => {
      if (stillHere()) window.sessionStorage.setItem(storageKey, String(Math.max(0, Math.round(node.scrollTop))));
    };
    const onWindowScroll = () => {
      if (stillHere()) window.sessionStorage.setItem(`${storageKey}#window`, String(Math.max(0, Math.round(window.scrollY))));
    };
    node.addEventListener('scroll', onPaneScroll, { passive: true });
    window.addEventListener('scroll', onWindowScroll, { passive: true });
    return () => {
      node.removeEventListener('scroll', onPaneScroll);
      window.removeEventListener('scroll', onWindowScroll);
    };
  }, [location.pathname, location.search]);

  async function runGlobalJump(rawInput?: string) {
    const value = String(rawInput ?? jumpInput).trim().toLowerCase();
    if (!value) return;
    // A team or event search can take seconds on venue wifi. If the scout has moved on
    // by the time it answers, stay where they went instead of pulling them away.
    const startedOn = window.location.hash;
    const go = (path: string) => {
      if (window.location.hash === startedOn) navigate(path);
    };

    // What was typed decides where it goes; the Auto/Team/Event switch made people choose first.
    const activeMode: QuickJumpGuess = guessQuickJump(value);
    const region = jumpRegion;

    if (activeMode === 'match') {
      const matchJump = parseMatchJump(value);
      if (matchJump) {
        go(`/match-center?event=${matchJump.eventKey}&match=${matchJump.matchKey}`);
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
            go(`/team-center?${params.toString()}`);
            return;
          }
        } catch {
          // fallback below
        }
        go(`/team-center`);
        return;
      }
      const params = new URLSearchParams();
      params.set('team', teamKey);
      go(`/team-center?${params.toString()}`);
      return;
    }

    if (activeMode === 'event') {
      if (isEventKey(value)) {
        go(`/events?event=${value}`);
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
          go(`/events?event=${selected.event_key.toLowerCase()}`);
          return;
        }
      } catch {
        // fallback below
      }
      const params = new URLSearchParams();
      params.set('q', value);
      if (region !== 'all') params.set('region', region);
      go(`/events?${params.toString()}`);
      return;
    }

    if (activeMode === 'search') {
      // A team's name ("cheesy poofs") used to need the Team switch. Try names before events.
      try {
        const payload = await searchTeams(value, 10);
        const named = payload.teams.find((team) => typedTeamName(value, team.nickname));
        if (named?.team_key) {
          go(`/team-center?team=${named.team_key.toLowerCase()}`);
          return;
        }
      } catch {
        // fall through to the events search
      }
    }

    const params = new URLSearchParams();
    params.set('q', value);
    if (region !== 'all') params.set('region', region);
    go(`/events?${params.toString()}`);
  }

  function handleJumpSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (jumpBusy) return;
    setJumpBusy(true);
    void runGlobalJump().finally(() => setJumpBusy(false));
  }

  const placeholder = 'Jump to team, event, or match…';

  // Only worth saying once there is something to classify. On an empty field
  // the guess falls back to "Search", so the hint rendered as "Detected input
  // type: Search" on every page — uppercased by the stylesheet, which made a
  // real feature read like leftover debug output.
  const helperLabel = !jumpInput.trim() ? '' : `Detected input type: ${guessLabel(guessedTarget)}`;

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

  // One quiet offer for brand-new visitors, never on a live scouting screen.
  const [tourPromptHidden, setTourPromptHidden] = useState<boolean>(() => tourPromptDismissed() || countSeenTutorials() > 0);
  const showTourPrompt = !tourPromptHidden && !tutorialAutoplay && !tutorialOpen && !isLiveScoutingRoute;
  const closeTourPrompt = useCallback(() => {
    dismissTourPrompt();
    setTourPromptHidden(true);
  }, []);

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
          <img src="/icon.svg" alt="" className="ps-brand-logo" decoding="async" />
          <div>
            <strong><BrandWordmark className="ps-brand-wordmark" /></strong>
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
              onClick={(event) => {
                if (!isPlainClick(event) || location.pathname === item.to) return;
                event.preventDefault();
                navigateWithTransition(navigate, item.to, {
                  direction: sectionDirection(NAV_ORDER, location.pathname, item.to),
                });
              }}
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
            <ThemeToggle theme={themeMode} className={themeToggleStyles.phoneOnly} />
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
            <ThemeToggle theme={themeMode} className={themeToggleStyles.desktopOnly} />
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
                  <button
                    type="button"
                    className={paletteStyles.trigger}
                    onClick={openPalette}
                    aria-label={`Search everything (${paletteShortcut})`}
                    title={`Search everything (${paletteShortcut})`}
                  >
                    {paletteShortcut}
                  </button>
                </form>
                {helperLabel ? <div className="ps-topbar-search-hint">{helperLabel}</div> : null}
              </div>
            ) : null}
          </div>
          ) : null}
        </header>

        {/* Desktop only. On a phone it stacked Event/Match/Team chips, pins and recents above
            pages that already show their own event and team: up to ~200 of 450px on a small
            phone before any content. Search and each page's own picker cover switching there. */}
        {/* Always there on desktop, even before an event is chosen ("Choose event"):
            appearing once Home picked one moved every page down after it had painted. */}
        {isDesktopSidebarViewport ? (
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
                    title={formatEventDisplay(eventKey)}
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
                    title={formatEventDisplay(eventKey)}
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
            ) : contextPins.event.length === 0 && contextPins.team.length === 0 ? (
              // Keeps the row's height before anything is recent, so the first
              // event a visitor opens doesn't push the page down.
              <div className="ps-context-quick-row">
                <span className="ps-context-quick-label">Recent</span>
                <span className="ps-context-quick-empty">Events and teams you open show up here.</span>
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
          {/* Event pages carry ?event=; an earlier season's event says so first. */}
          {/^\/(events|match-center|team-center|compare|scouting)(\/|$)/.test(location.pathname) ? (
            <PastSeasonBanner eventKey={new URLSearchParams(location.search).get('event')} />
          ) : null}
          <Outlet />
          <SiteFooter />
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
        jumpRegion={jumpRegion}
      />

      {tutorialOpen ? (
        <QuietBoundary name="tour">
          <Suspense fallback={null}>
            <TabTutorialOverlay key={tutorialScope} scope={tutorialScope} onClose={() => setTutorialOpen(false)} />
          </Suspense>
        </QuietBoundary>
      ) : null}
      {showTourPrompt ? (
        <TourPrompt
          onStart={() => {
            closeTourPrompt();
            openTutorial(activeScope);
          }}
          onDismiss={closeTourPrompt}
        />
      ) : null}

      <BottomTabBar
        moreOpen={mobileMoreOpen}
        onToggleMore={() => setMobileMoreOpen((c) => !c)}
      />
      <ToastHost />
      <PullToRefresh areaRef={contentRef} pathname={location.pathname} />
      {/* Each extra on its own: one that fails to download is left out, the rest stay. */}
      <QuietBoundary name="next-match">
        <Suspense fallback={null}>
          <NextMatchPill pathname={location.pathname} search={location.search} />
        </Suspense>
      </QuietBoundary>
      <QuietBoundary name="team-peek">
        <Suspense fallback={null}>
          <TeamPeek />
        </Suspense>
      </QuietBoundary>
      {paletteSession > 0 ? (
        <QuietBoundary key={paletteSession} name="palette">
          <Suspense fallback={null}>
            <CommandPalette
              open={paletteOpen}
              onClose={() => setPaletteOpen(false)}
              recentEvents={recentEventQuickPicks}
              recentTeams={recentTeamQuickPicks}
              onStartTour={() => openTutorial(activeScope)}
            />
          </Suspense>
        </QuietBoundary>
      ) : null}
    </div>
  );
}
