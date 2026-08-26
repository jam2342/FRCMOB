import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  getEventLiveStream,
  getEventRankings,
  getEventSchedule,
} from '../api';
import type {
  EventLiveStreamResponse,
  EventRankingsResponse,
  EventScheduleItem,
  EventSearchItem,
} from '../api';
import {
  asRecord,
  buildMatchCenterPath,
  copyTextToClipboard,
  CURRENT_SEASON_YEAR,
  FALLBACK_SEASON_YEAR,
  fmtDateShort,
  isTransientAbortLikeError,
  liveTimerLabel,
  normalizeMatchKey,
  parseNumber,
  relativeFromTimestamp,
} from './centerUtils';
import {
  buildHomeFilterCounts,
  filterHomeWindowMatches,
  hasResolvedMatchScores,
  pickMobileHomeAutoEventKey,
  resolveHomeMatchState,
  selectHomeDayMatches,
  type HomeFilter,
} from './homeFeed';
import {
  writeCenterContext,
  readStoredCenterContext,
} from '../layout/centerContext';
import { TeamAvatar } from '../components/TeamAvatar';
import {
  LiveDotIcon, ClockIcon, CheckCircleIcon, BarChartIcon, InfoIcon,
  TrophyIcon, VideoIcon, PuzzleIcon, CalendarIcon, UsersIcon, LinkIcon,
  ChevronLeftIcon, ChevronRightIcon, ChevronDownIcon, SearchIcon,
} from '../components/ui/Icons';
import { SkeletonBlock } from '../components/ui/SkeletonBlock';
import { EmptyState } from '../components/ui/EmptyState';
import { loadSeasonEventCatalog, loadSeasonSearchFallback } from '../features/events/eventCatalog';
import { useLiveRefreshSetting } from '../hooks/useLiveRefreshSetting';
import { MOBILE_LAYOUT_BREAKPOINT, useMobileLayout } from '../hooks/useMobileLayout';
import { Stat, Table, type TableColumn } from '../components/ui/primitives';
import { usePageClock } from '../hooks/usePageClock';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling, type SingleFlightPollReason } from '../hooks/useSingleFlightPolling';
import { smartSearchEvents } from '../utils/eventSearch';

const HOME_EVENT_VIEW_PREFS_STORAGE = 'scouting_home_event_view_prefs_v1';
const HOME_MOBILE_AUTO_EVENT_GUARD_STORAGE = 'scouting_home_mobile_auto_event_guard_v1';

type HomeTeamsSortMode = 'rank' | 'team';

type HomeEventSchedule = {
  event_name: string | null;
  matches: EventScheduleItem[];
};

type HomeFeedSection = {
  event_key: string;
  event_name: string;
  location: string;
  matches: EventScheduleItem[];
  total_filtered_matches: number;
  total_matches: number;
};

type HomeRankedEvent = {
  event_key: string;
  name: string;
  location: string;
  status_label: string;
  live_count: number;
  match_count: number;
  is_live: boolean;
};

type RankingRow = {
  team_key: string;
  team_number: number;
  nickname: string;
  rank: number | null;
  matches_played: number | null;
  record: string;
};

type EventDateRange = {
  startMs: number | null;
  endMs: number | null;
};

type HomeEventViewPrefs = {
  activeFilter?: HomeFilter;
  teamsSortMode?: HomeTeamsSortMode;
};

type HomeMobileAutoEventGuard = {
  dayToken: string;
};

const HOME_EVENT_SUGGEST_LIMIT = 120;
const HOME_EVENT_PRELOAD_LIMIT = 48;
const HOME_EVENT_INITIAL_PRELOAD_LIMIT = 10;
const HOME_EVENT_REFRESH_LIMIT = 8;
const HOME_EVENT_LIVE_REFRESH_LIMIT = 4;
const HOME_SCHEDULE_FETCH_BATCH = 8;
const HOME_MIN_SUGGESTED_EVENT_TARGET = 24;
const HOME_TRENDING_TEAM_COUNT_FETCH_LIMIT = 12;
const HOME_CALENDAR_EVENT_LIMIT = 700;
const HOME_CALENDAR_DISPLAY_YEAR = new Date().getUTCFullYear();
const HOME_ONE_DAY_MS = 24 * 60 * 60 * 1000;
const HOME_MOBILE_DEFAULT_COLLAPSED_CARD_KEYS = [
  'all-events',
  'compare-builder',
  'live-stream',
  'leaderboard',
  'teams-insights',
] as const;

function normalizeEventKey(eventKey: string): string {
  return eventKey.trim().toLowerCase();
}

function mergeEventLists(...lists: EventSearchItem[][]): EventSearchItem[] {
  const byKey = new Map<string, EventSearchItem>();
  for (const list of lists) {
    for (const event of list) {
      const key = normalizeEventKey(event.event_key || '');
      if (!key) continue;
      const previous = byKey.get(key);
      if (!previous) {
        byKey.set(key, event);
        continue;
      }
      byKey.set(key, {
        ...previous,
        ...event,
        start_date: event.start_date || previous.start_date,
        end_date: event.end_date || previous.end_date,
      });
    }
  }
  return Array.from(byKey.values());
}

function readHomeEventViewPrefs(): Record<string, HomeEventViewPrefs> {
  try {
    const raw = window.localStorage.getItem(HOME_EVENT_VIEW_PREFS_STORAGE);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return parsed as Record<string, HomeEventViewPrefs>;
  } catch {
    return {};
  }
}

function writeHomeEventViewPrefs(next: Record<string, HomeEventViewPrefs>) {
  window.localStorage.setItem(HOME_EVENT_VIEW_PREFS_STORAGE, JSON.stringify(next));
}

function readHomeMobileAutoEventGuard(): HomeMobileAutoEventGuard | null {
  try {
    const raw = window.sessionStorage.getItem(HOME_MOBILE_AUTO_EVENT_GUARD_STORAGE);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    const dayToken = typeof parsed.dayToken === 'string' ? parsed.dayToken.trim() : '';
    if (!dayToken) return null;
    return { dayToken };
  } catch {
    return null;
  }
}

function writeHomeMobileAutoEventGuard(dayToken: string) {
  try {
    window.sessionStorage.setItem(
      HOME_MOBILE_AUTO_EVENT_GUARD_STORAGE,
      JSON.stringify({ dayToken } satisfies HomeMobileAutoEventGuard),
    );
  } catch {
    // Ignore storage failures on restricted browsers.
  }
}

/* The rankings payload sets `nickname` to the team key when a team has none,
   so printing both gives "#5940 frc5940". */
function teamSuffix(row: { nickname?: string | null; team_key: string }): string {
  const nickname = (row.nickname || '').trim();
  return nickname && nickname !== row.team_key ? ` ${nickname}` : '';
}

function normalizeHomeTeamsSortMode(value: unknown): HomeTeamsSortMode {
  return value === 'team' ? 'team' : 'rank';
}

function eventLocationLabel(event: EventSearchItem): string {
  const location = [event.city, event.state_prov, event.country]
    .map((value) => (value || '').trim())
    .filter((value) => Boolean(value));
  if (location.length > 0) return location.join(', ');
  return 'Location unavailable';
}

function stateLabel(state: ReturnType<typeof liveTimerLabel>['state']): React.ReactNode {
  if (state === 'live') return <><LiveDotIcon className="icon-inline icon-status-live icon-live-pulse" /> Live</>;
  if (state === 'upcoming') return <><ClockIcon className="icon-inline icon-status-upcoming" /> Upcoming</>;
  if (state === 'ended') return <><CheckCircleIcon className="icon-inline icon-status-final" /> Final</>;
  return 'Pending';
}

function stateTextLabel(state: ReturnType<typeof liveTimerLabel>['state']): string {
  if (state === 'live') return 'Live';
  if (state === 'upcoming') return 'Upcoming';
  if (state === 'ended') return 'Final';
  return 'Pending';
}

function compactAllianceLabel(teams: EventScheduleItem['red']): string {
  if (!Array.isArray(teams) || teams.length === 0) return 'TBD';
  const labels = teams.map((team) => `#${team.team_number}`);
  if (labels.length <= 2) return labels.join(' · ');
  return `${labels.slice(0, 2).join(' · ')} +${labels.length - 2}`;
}

function homeFilterLabel(filter: HomeFilter): string {
  if (filter === 'all') return 'All';
  if (filter === 'live') return 'Live';
  if (filter === 'upcoming') return 'Upcoming';
  return 'Final';
}

function parseEventDateValue(value: string | null | undefined): number | null {
  if (!value || typeof value !== 'string') return null;
  const token = value.trim().slice(0, 10);
  if (!token) return null;
  const ms = Date.parse(`${token}T00:00:00Z`);
  return Number.isFinite(ms) ? ms : null;
}

function parseEventDateToken(value: string | null | undefined): string | null {
  if (!value || typeof value !== 'string') return null;
  const token = value.trim().slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(token) ? token : null;
}

function localDateTokenFromMs(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function eventRunsOnDateToken(event: EventSearchItem | undefined, targetToken: string): boolean {
  const startToken = parseEventDateToken(event?.start_date ?? null);
  const endToken = parseEventDateToken(event?.end_date ?? null) ?? startToken;
  const first = startToken ?? endToken;
  const last = endToken ?? startToken;
  if (!first || !last) return false;
  const low = first <= last ? first : last;
  const high = first <= last ? last : first;
  return targetToken >= low && targetToken <= high;
}

function parseEventYearValue(value: unknown): number | null {
  const normalized = typeof value === 'number' ? value : Number(String(value || '').trim());
  if (!Number.isFinite(normalized)) return null;
  const year = Math.trunc(normalized);
  if (year < 1992 || year > 2100) return null;
  return year;
}

function yearFromEventKey(eventKey: string): number | null {
  const match = /^(\d{4})/.exec((eventKey || '').trim().toLowerCase());
  if (!match) return null;
  return parseEventYearValue(match[1]);
}

function monthTokenFromMs(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

function monthTokensForRange(startMs: number | null, endMs: number | null): string[] {
  const firstMs = startMs ?? endMs;
  const lastMs = endMs ?? startMs;
  if (!firstMs || !lastMs) return [];
  const tokens: string[] = [];
  const current = new Date(Date.UTC(new Date(firstMs).getUTCFullYear(), new Date(firstMs).getUTCMonth(), 1));
  const terminal = new Date(Date.UTC(new Date(lastMs).getUTCFullYear(), new Date(lastMs).getUTCMonth(), 1));
  while (current <= terminal) {
    tokens.push(`${current.getUTCFullYear()}-${String(current.getUTCMonth() + 1).padStart(2, '0')}`);
    current.setUTCMonth(current.getUTCMonth() + 1);
  }
  return tokens;
}

function normalizeDateRange(range: EventDateRange): EventDateRange {
  if (range.startMs && range.endMs && range.endMs < range.startMs) {
    return { startMs: range.endMs, endMs: range.startMs };
  }
  return range;
}

function resolveEventDateRange(event: EventSearchItem, fallback?: EventDateRange): EventDateRange {
  const startMs = parseEventDateValue(event.start_date ?? null) ?? fallback?.startMs ?? null;
  const endMs = parseEventDateValue(event.end_date ?? null) ?? fallback?.endMs ?? startMs;
  return normalizeDateRange({ startMs, endMs });
}

function resolveCalendarYear(event: EventSearchItem, fallback?: EventDateRange): number | null {
  const explicit = parseEventYearValue(event.year);
  if (explicit !== null) return explicit;
  const resolved = resolveEventDateRange(event, fallback);
  const ms = resolved.startMs ?? resolved.endMs;
  if (ms) return new Date(ms).getUTCFullYear();
  return yearFromEventKey(event.event_key || '');
}

function matchesCalendarDisplayYear(
  event: EventSearchItem,
  displayYear: number,
  fallback?: EventDateRange,
): boolean {
  const keyYear = yearFromEventKey(event.event_key || '');
  if (keyYear !== null && keyYear !== displayYear) return false;
  return resolveCalendarYear(event, fallback) === displayYear;
}

function formatMonthLabel(token: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(token);
  if (!match) return token;
  const d = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' });
}

function shiftMonthToken(monthToken: string, delta: number): string {
  const token = (monthToken || '').trim();
  const match = /^(\d{4})-(\d{2})$/.exec(token);
  const base = match
    ? new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1))
    : new Date(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), 1));
  base.setUTCMonth(base.getUTCMonth() + delta);
  const year = base.getUTCFullYear();
  const month = String(base.getUTCMonth() + 1).padStart(2, '0');
  return `${year}-${month}`;
}

function startOfLocalDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function shiftLocalDay(ms: number, deltaDays: number): number {
  const d = new Date(startOfLocalDay(ms));
  d.setDate(d.getDate() + deltaDays);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function toLocalDateInputValue(ms: number): string {
  const d = new Date(ms);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function fromLocalDateInputValue(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec((value || '').trim());
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  if (!Number.isFinite(date.getTime())) return null;
  date.setHours(0, 0, 0, 0);
  return date.getTime();
}

function fromDateTokenToLocalDayMs(token: string): number | null {
  return fromLocalDateInputValue(token);
}

function homeDayHeading(dayMs: number, nowMs: number): string {
  const dayStart = startOfLocalDay(dayMs);
  const nowStart = startOfLocalDay(nowMs);
  const offset = Math.round((dayStart - nowStart) / HOME_ONE_DAY_MS);
  if (offset === 0) return 'Today';
  if (offset === 1) return 'Tomorrow';
  if (offset === -1) return 'Yesterday';
  return new Date(dayMs).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

export function HomePage() {
  const navigate = useNavigate();
  const pageVisible = usePageVisibility();
  const isMobileLayout = useMobileLayout();
  const nowMs = usePageClock(pageVisible);
  const liveRefreshSec = useLiveRefreshSetting();

  const [suggestedEvents, setSuggestedEvents] = useState<EventSearchItem[]>([]);
  const [calendarSeasonEvents, setCalendarSeasonEvents] = useState<EventSearchItem[]>([]);
  const [selectedEventKey, setSelectedEventKey] = useState(() =>
    readStoredCenterContext().eventKey,
  );
  const [scheduleByEvent, setScheduleByEvent] = useState<Record<string, HomeEventSchedule>>({});
  const [selectedEventRankings, setSelectedEventRankings] = useState<EventRankingsResponse | null>(null);
  const [selectedEventStream, setSelectedEventStream] = useState<EventLiveStreamResponse | null>(null);

  const [activeFilter, setActiveFilter] = useState<HomeFilter>('all');
  const [loadingHome, setLoadingHome] = useState(false);
  const [loadingSelectedContext, setLoadingSelectedContext] = useState(false);
  const [, setStatusText] = useState('Loading Home feed...');
  const [errorText, setErrorText] = useState('');
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [selectedDayMs, setSelectedDayMs] = useState(() => startOfLocalDay(Date.now()));
  const [teamsPanelOpen, setTeamsPanelOpen] = useState(false);
  const [teamsQueryInput, setTeamsQueryInput] = useState('');
  const [teamsQuery, setTeamsQuery] = useState('');
  const [teamsPage, setTeamsPage] = useState(1);
  const [teamsPerPage, setTeamsPerPage] = useState(10);
  const [teamsSortMode, setTeamsSortMode] = useState<HomeTeamsSortMode>('rank');
  const [mobileCollapsedCards, setMobileCollapsedCards] = useState<Record<string, boolean>>({});
  const [desktopCollapsedFeedCards, setDesktopCollapsedFeedCards] = useState<Record<string, boolean>>({});
  const [mobileExpandedMatches, setMobileExpandedMatches] = useState<Record<string, boolean>>({});
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [mobileDrawerClosing, setMobileDrawerClosing] = useState(false);
  const [mobileCalendarOpen, setMobileCalendarOpen] = useState(false);
  const [mobileCalendarClosing, setMobileCalendarClosing] = useState(false);
  const [calendarModalOpen, setCalendarModalOpen] = useState(false);
  const [expandedCalendarDays, setExpandedCalendarDays] = useState<Set<string>>(() => new Set());
  const [eventSearchOpen, setEventSearchOpen] = useState(false);
  const [eventSearchQuery, setEventSearchQuery] = useState('');
  const [eventSearchResults, setEventSearchResults] = useState<EventSearchItem[]>([]);
  const [eventSearchBusy, setEventSearchBusy] = useState(false);
  const eventSearchInputRef = useRef<HTMLInputElement>(null);
  const eventSearchContainerRef = useRef<HTMLDivElement>(null);
  const eventSearchRequestSeqRef = useRef(0);
  const dayPickerInputRef = useRef<HTMLInputElement>(null);
  const selectedContextEventRef = useRef('');
  const autoAdjustedCalendarMonthRef = useRef(false);
  const [calendarMonth, setCalendarMonth] = useState(() => {
    const now = new Date();
    const year = now.getUTCFullYear();
    const month = String(now.getUTCMonth() + 1).padStart(2, '0');
    return `${year}-${month}`;
  });

  useEffect(() => {
    if (isMobileLayout) return;
    setMobileDrawerOpen(false);
    setMobileCalendarOpen(false);
  }, [isMobileLayout]);

  const closeDrawer = useCallback(() => {
    setMobileDrawerClosing(true);
    setTimeout(() => { setMobileDrawerOpen(false); setMobileDrawerClosing(false); }, 200);
  }, []);

  const closeCalendarDrawer = useCallback(() => {
    setMobileCalendarClosing(true);
    setTimeout(() => { setMobileCalendarOpen(false); setMobileCalendarClosing(false); }, 200);
  }, []);

  useEffect(() => {
    if (!calendarModalOpen) {
      setExpandedCalendarDays(new Set());
      return;
    }
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCalendarModalOpen(false);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [calendarModalOpen]);

  useEffect(() => {
    setExpandedCalendarDays(new Set());
  }, [calendarMonth]);

  const toggleCalendarDayExpanded = useCallback((token: string) => {
    setExpandedCalendarDays((prev) => {
      const next = new Set(prev);
      if (next.has(token)) {
        next.delete(token);
      } else {
        next.add(token);
      }
      return next;
    });
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setTeamsQuery(teamsQueryInput.trim());
    }, 220);
    return () => window.clearTimeout(timer);
  }, [teamsQueryInput]);

  /* ── Event search: auto-focus when opened ────────────────────────── */
  useEffect(() => {
    if (eventSearchOpen) {
      requestAnimationFrame(() => eventSearchInputRef.current?.focus());
    } else {
      eventSearchRequestSeqRef.current += 1;
      setEventSearchQuery('');
      setEventSearchResults([]);
      setEventSearchBusy(false);
    }
  }, [eventSearchOpen]);

  /* ── Event search: click-outside to close ────────────────────────── */
  useEffect(() => {
    if (!eventSearchOpen) return;
    function handleClickOutside(event: MouseEvent) {
      if (eventSearchContainerRef.current && !eventSearchContainerRef.current.contains(event.target as Node)) {
        setEventSearchOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [eventSearchOpen]);

  /* ── Event search: debounced query ───────────────────────────────── */
  useEffect(() => {
    const requestSeq = ++eventSearchRequestSeqRef.current;
    const trimmed = eventSearchQuery.trim();
    if (!trimmed) {
      setEventSearchResults([]);
      setEventSearchBusy(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setEventSearchBusy(true);
      smartSearchEvents(trimmed, {
        maxResults: 12,
        seedEvents: suggestedEvents,
        fastMode: true,
        localOnly: false,
        maxNetworkVariants: 4,
        includeSuggestedFallback: true,
      })
        .then((result) => {
          if (requestSeq === eventSearchRequestSeqRef.current) {
            setEventSearchResults(result.events);
          }
        })
        .catch(() => {
          if (requestSeq === eventSearchRequestSeqRef.current) {
            setEventSearchResults([]);
          }
        })
        .finally(() => {
          if (requestSeq === eventSearchRequestSeqRef.current) {
            setEventSearchBusy(false);
          }
        });
    }, 280);
    return () => window.clearTimeout(timer);
  }, [eventSearchQuery, suggestedEvents]);

  useEffect(() => {
    setTeamsPage(1);
  }, [teamsPerPage, teamsQuery, teamsSortMode, selectedEventKey]);

  useEffect(() => {
    if (!selectedEventKey) return;
    const prefs = readHomeEventViewPrefs();
    const eventPrefs = prefs[selectedEventKey];
    setActiveFilter(eventPrefs?.activeFilter || 'all');
    setTeamsSortMode(normalizeHomeTeamsSortMode(eventPrefs?.teamsSortMode));
  }, [selectedEventKey]);

  useEffect(() => {
    if (!selectedEventKey) return;
    const prefs = readHomeEventViewPrefs();
    const previous = prefs[selectedEventKey] || {};
    const next: HomeEventViewPrefs = {
      ...previous,
      activeFilter,
      teamsSortMode,
    };
    prefs[selectedEventKey] = next;
    writeHomeEventViewPrefs(prefs);
  }, [activeFilter, selectedEventKey, teamsSortMode]);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setLoadingHome(true);
      setErrorText('');
      try {
        const events = await loadSeasonEventCatalog({
          preferredYear: CURRENT_SEASON_YEAR,
          fallbackYear: FALLBACK_SEASON_YEAR,
          limit: HOME_EVENT_SUGGEST_LIMIT,
          minTarget: HOME_MIN_SUGGESTED_EVENT_TARGET,
          preferLiveNow: true,
          remoteTeamCountFetchLimit: HOME_TRENDING_TEAM_COUNT_FETCH_LIMIT,
        });
        if (cancelled) return;

        setSuggestedEvents(events);

        const normalizedEventKeys = events.map((event) => normalizeEventKey(event.event_key));
        const stored = readStoredCenterContext().eventKey;
        const preferredEventKey =
          stored && normalizedEventKeys.includes(stored) ? stored : normalizedEventKeys[0] || '';

        setSelectedEventKey(preferredEventKey);

        const todayToken = localDateTokenFromMs(Date.now());
        const todayPriorityKeys = events
          .filter((event) => eventRunsOnDateToken(event, todayToken))
          .map((event) => normalizeEventKey(event.event_key));

        const preloadKeys = [preferredEventKey, ...todayPriorityKeys, ...normalizedEventKeys]
          .filter((key, idx, array) => Boolean(key) && array.indexOf(key) === idx)
          .slice(0, HOME_EVENT_PRELOAD_LIMIT);

        const initialPreloadKeys = preloadKeys.slice(
          0,
          Math.max(HOME_SCHEDULE_FETCH_BATCH, HOME_EVENT_INITIAL_PRELOAD_LIMIT),
        );
        const backgroundPreloadKeys = preloadKeys.slice(initialPreloadKeys.length);

        async function fetchSchedulesInBatches(
          eventKeys: string[],
          options?: { pushPartial?: boolean },
        ): Promise<Record<string, HomeEventSchedule>> {
          const nextScheduleMap: Record<string, HomeEventSchedule> = {};
          for (let offset = 0; offset < eventKeys.length; offset += HOME_SCHEDULE_FETCH_BATCH) {
            const chunk = eventKeys.slice(offset, offset + HOME_SCHEDULE_FETCH_BATCH);
            if (chunk.length === 0) break;
            const chunkResults = await Promise.allSettled(
              chunk.map((eventKey) => getEventSchedule(eventKey, false)),
            );
            if (cancelled) return nextScheduleMap;
            const chunkMap: Record<string, HomeEventSchedule> = {};
            chunkResults.forEach((result, idx) => {
              const key = chunk[idx];
              if (result.status === 'fulfilled') {
                chunkMap[key] = {
                  event_name: result.value.event_name || null,
                  matches: result.value.matches || [],
                };
              }
            });
            if (Object.keys(chunkMap).length > 0) {
              Object.assign(nextScheduleMap, chunkMap);
              if (options?.pushPartial) {
                setScheduleByEvent((previous) => ({ ...previous, ...chunkMap }));
                setLastUpdatedAt(Date.now());
              }
            }
          }
          return nextScheduleMap;
        }

        const initialScheduleMap = await fetchSchedulesInBatches(initialPreloadKeys);
        if (cancelled) return;
        setScheduleByEvent(initialScheduleMap);
        setLastUpdatedAt(Date.now());
        setStatusText(
          preferredEventKey
            ? `${preferredEventKey} loaded.`
            : 'Select an event to continue.',
        );

        if (backgroundPreloadKeys.length > 0) {
          void (async () => {
            try {
              await fetchSchedulesInBatches(backgroundPreloadKeys, { pushPartial: true });
            } catch {
              // Keep initial payload responsive; ignore background preload failures.
            }
          })();
        }
      } catch (error) {
        if (cancelled) return;
        if (isTransientAbortLikeError(error)) {
          setErrorText('');
          try {
            const fallbackEvents = await loadSeasonSearchFallback({
              preferredYear: CURRENT_SEASON_YEAR,
              fallbackYear: FALLBACK_SEASON_YEAR,
              limit: HOME_EVENT_SUGGEST_LIMIT,
            });
            if (cancelled) return;
            if (fallbackEvents.length > 0) {
              setSuggestedEvents(fallbackEvents);
              setStatusText('Home feed timed out; using seasonal fallback events.');
            } else {
              setStatusText('Home feed refresh timed out; keeping current data.');
            }
          } catch {
            setStatusText('Home feed refresh timed out; keeping current data.');
          }
          return;
        }
        setSuggestedEvents([]);
        setScheduleByEvent({});
        setErrorText((error as Error).message || 'Unable to load suggested events.');
      } finally {
        if (!cancelled) setLoadingHome(false);
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      try {
        const events = await loadSeasonSearchFallback({
          preferredYear: CURRENT_SEASON_YEAR,
          fallbackYear: FALLBACK_SEASON_YEAR,
          limit: HOME_CALENDAR_EVENT_LIMIT,
        });
        if (cancelled) return;
        setCalendarSeasonEvents(events);
      } catch {
        if (cancelled) return;
        setCalendarSeasonEvents([]);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selectedEventKey) return;
    selectedContextEventRef.current = '';
    setSelectedEventRankings(null);
    setSelectedEventStream(null);
  }, [selectedEventKey]);

  const refreshSelectedContext = useCallback(async (reason: SingleFlightPollReason): Promise<boolean> => {
    if (!selectedEventKey) return true;
    const contextChanged = selectedContextEventRef.current !== selectedEventKey;
    selectedContextEventRef.current = selectedEventKey;
    const shouldRefreshStatic = contextChanged || reason !== 'poll';

    writeCenterContext({ eventKey: selectedEventKey, sourcePath: '/home' });
    if (shouldRefreshStatic) setLoadingSelectedContext(true);
    try {
      const [rankingsResult, streamResult] = await Promise.allSettled([
        shouldRefreshStatic ? getEventRankings(selectedEventKey) : Promise.resolve(null),
        shouldRefreshStatic ? getEventLiveStream(selectedEventKey) : Promise.resolve(null),
      ]);

      const errors: string[] = [];

      if (shouldRefreshStatic && rankingsResult.status === 'fulfilled' && rankingsResult.value) {
        setSelectedEventRankings(rankingsResult.value);
      } else if (shouldRefreshStatic && rankingsResult.status === 'rejected') {
        const rankingReason = rankingsResult.reason instanceof Error ? rankingsResult.reason.message : 'failed';
        if (!isTransientAbortLikeError(rankingsResult.reason)) {
          setSelectedEventRankings(null);
          errors.push(`Rankings: ${rankingReason}`);
        }
      }

      if (shouldRefreshStatic && streamResult.status === 'fulfilled' && streamResult.value) {
        setSelectedEventStream(streamResult.value);
      } else if (shouldRefreshStatic && streamResult.status === 'rejected') {
        const streamReason = streamResult.reason instanceof Error ? streamResult.reason.message : 'failed';
        if (!isTransientAbortLikeError(streamResult.reason)) {
          setSelectedEventStream(null);
          errors.push(`Live stream: ${streamReason}`);
        }
      }

      if (shouldRefreshStatic) {
        setErrorText(errors.join(' | '));
        setLastUpdatedAt(Date.now());
      }
      return errors.length === 0;
    } finally {
      if (shouldRefreshStatic) setLoadingSelectedContext(false);
    }
  }, [selectedEventKey]);

  useSingleFlightPolling({
    enabled: Boolean(selectedEventKey),
    visible: pageVisible,
    intervalMs: Math.max(10, liveRefreshSec) * 1000,
    run: refreshSelectedContext,
    backoffMultiplier: 1.6,
    minBackoffMs: Math.max(10, liveRefreshSec) * 1000,
    maxBackoffMs: 60000,
  });

  useEffect(() => {
    if (!selectedEventKey || scheduleByEvent[selectedEventKey]) return;

    let cancelled = false;

    async function run() {
      try {
        const payload = await getEventSchedule(selectedEventKey, false);
        if (cancelled) return;
        setScheduleByEvent((prev) => ({
          ...prev,
          [selectedEventKey]: {
            event_name: payload.event_name || null,
            matches: payload.matches || [],
          },
        }));
        setLastUpdatedAt(Date.now());
      } catch {
        if (cancelled) return;
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, [scheduleByEvent, selectedEventKey]);

  const eventByKey = useMemo(() => {
    const lookup: Record<string, EventSearchItem> = {};
    for (const event of suggestedEvents) {
      lookup[normalizeEventKey(event.event_key)] = event;
    }
    return lookup;
  }, [suggestedEvents]);

  const feedEventKeys = useMemo(() => {
    const suggestedKeys = suggestedEvents.map((event) => normalizeEventKey(event.event_key));
    const prioritized = [selectedEventKey, ...suggestedKeys]
      .filter((key, idx, array) => Boolean(key) && array.indexOf(key) === idx)
      .slice(0, HOME_EVENT_PRELOAD_LIMIT);

    const dayKeys = prioritized.filter((eventKey) => {
      const matches = scheduleByEvent[eventKey]?.matches || [];
      return selectHomeDayMatches(matches, selectedDayMs).length > 0;
    });

    if (dayKeys.length > 0) return dayKeys;

    const fallbackKeys = prioritized.filter((eventKey) => {
      const matches = scheduleByEvent[eventKey]?.matches || [];
      return matches.length > 0;
    });
    if (fallbackKeys.length > 0) return fallbackKeys;

    return Object.keys(scheduleByEvent)
      .filter((key) => Boolean(key))
      .filter((eventKey) => {
        const matches = scheduleByEvent[eventKey]?.matches || [];
        return matches.length > 0;
      })
      .sort((a, b) => {
        const aTimes = (scheduleByEvent[a]?.matches || [])
          .map((match) => (typeof match.scheduled_time === 'number' ? match.scheduled_time : null))
          .filter((value): value is number => value !== null);
        const bTimes = (scheduleByEvent[b]?.matches || [])
          .map((match) => (typeof match.scheduled_time === 'number' ? match.scheduled_time : null))
          .filter((value): value is number => value !== null);
        const aLatest = aTimes.length > 0 ? Math.max(...aTimes) : 0;
        const bLatest = bTimes.length > 0 ? Math.max(...bTimes) : 0;
        return bLatest - aLatest;
      });
  }, [scheduleByEvent, selectedDayMs, selectedEventKey, suggestedEvents]);

  const scheduleRefreshKeys = useMemo(() => {
    return [selectedEventKey, ...feedEventKeys]
      .filter((key, idx, arr) => Boolean(key) && arr.indexOf(key) === idx)
      .slice(0, HOME_EVENT_REFRESH_LIMIT);
  }, [feedEventKeys, selectedEventKey]);

  const hasLiveMatchesInRefreshWindow = useMemo(() => {
    return scheduleRefreshKeys.some((eventKey) => {
      const matches = scheduleByEvent[eventKey]?.matches || [];
      return matches.some((match) => resolveHomeMatchState(match, nowMs) === 'live');
    });
  }, [nowMs, scheduleByEvent, scheduleRefreshKeys]);

  const effectiveSchedulePollSec = hasLiveMatchesInRefreshWindow ? 5 : Math.max(10, liveRefreshSec);

  const refreshSchedules = useCallback(async (reason: SingleFlightPollReason): Promise<boolean> => {
    if (scheduleRefreshKeys.length === 0) return true;
    const shouldBypassLiveCache = reason === 'poll' && hasLiveMatchesInRefreshWindow;
    const refreshTargetKeys = shouldBypassLiveCache
      ? scheduleRefreshKeys.slice(0, HOME_EVENT_LIVE_REFRESH_LIMIT)
      : scheduleRefreshKeys;
    if (refreshTargetKeys.length === 0) return true;
    const requestOptions = shouldBypassLiveCache
      ? { bypassCache: true, cacheTtlMs: 0, staleWhileRevalidateMs: 0 }
      : undefined;
    const results = await Promise.allSettled(
      refreshTargetKeys.map((eventKey) => getEventSchedule(eventKey, false, undefined, requestOptions)),
    );
    setScheduleByEvent((prev) => {
      const next = { ...prev };
      results.forEach((result, idx) => {
        const eventKey = refreshTargetKeys[idx];
        if (!eventKey || result.status !== 'fulfilled') return;
        next[eventKey] = {
          event_name: result.value.event_name || null,
          matches: result.value.matches || [],
        };
      });
      return next;
    });
    setLastUpdatedAt(Date.now());
    return true;
  }, [hasLiveMatchesInRefreshWindow, scheduleRefreshKeys]);

  useSingleFlightPolling({
    enabled: scheduleRefreshKeys.length > 0,
    visible: pageVisible,
    intervalMs: effectiveSchedulePollSec * 1000,
    run: refreshSchedules,
    backoffMultiplier: 1.5,
    minBackoffMs: effectiveSchedulePollSec * 1000,
    maxBackoffMs: 60000,
  });

  const feedSections = useMemo<HomeFeedSection[]>(() => {
    return feedEventKeys
      .map((eventKey) => {
        const schedule = scheduleByEvent[eventKey];
        const allMatches = schedule?.matches || [];
        const dayMatches = selectHomeDayMatches(allMatches, selectedDayMs);
        const filtered = filterHomeWindowMatches(dayMatches, activeFilter, nowMs);

        return {
          event_key: eventKey,
          event_name: schedule?.event_name || eventByKey[eventKey]?.name || eventKey,
          location: eventByKey[eventKey] ? eventLocationLabel(eventByKey[eventKey]) : 'Location unavailable',
          matches: filtered,
          total_filtered_matches: filtered.length,
          total_matches: dayMatches.length,
        };
      })
      .filter((section) => {
        if (activeFilter === 'all') return section.total_matches > 0;
        return section.total_filtered_matches > 0;
      });
  }, [activeFilter, eventByKey, feedEventKeys, nowMs, scheduleByEvent, selectedDayMs]);

  const filterCounts = useMemo(() => {
    return buildHomeFilterCounts(feedEventKeys, scheduleByEvent, nowMs, { dayMs: selectedDayMs });
  }, [feedEventKeys, nowMs, scheduleByEvent, selectedDayMs]);

  const todayDateToken = useMemo(() => localDateTokenFromMs(nowMs), [nowMs]);

  const selectHomeEventKey = useCallback((eventKey: string, options?: { manual?: boolean }) => {
    const normalized = normalizeEventKey(eventKey || '');
    if (!normalized) return;
    if (options?.manual && isMobileLayout) {
      writeHomeMobileAutoEventGuard(todayDateToken);
    }
    setSelectedEventKey(normalized);
  }, [isMobileLayout, todayDateToken]);

  const mobileAutoOpenEventKey = useMemo(
    () => pickMobileHomeAutoEventKey(suggestedEvents, scheduleByEvent, nowMs),
    [nowMs, scheduleByEvent, suggestedEvents],
  );

  const activeEvents = useMemo<HomeRankedEvent[]>(() => {
    const rows: HomeRankedEvent[] = [];
    for (const [eventKey, eventMeta] of Object.entries(eventByKey)) {
      if (!eventRunsOnDateToken(eventMeta, todayDateToken)) continue;
      const schedule = scheduleByEvent[eventKey];
      const matches = schedule?.matches || [];
      const startsToday = parseEventDateToken(eventMeta.start_date ?? null) === todayDateToken;
      const resolvedName = (schedule?.event_name || eventMeta?.name || eventKey).trim();
      rows.push({
        event_key: eventKey,
        name: resolvedName || eventKey,
        location: eventMeta ? eventLocationLabel(eventMeta) : 'Location unavailable',
        status_label: startsToday ? 'Starts today' : 'Running today',
        live_count: 0,
        match_count: matches.length,
        is_live: true,
      });
    }
    rows.sort((a, b) => {
      if (a.is_live !== b.is_live) return a.is_live ? -1 : 1;
      if (b.live_count !== a.live_count) return b.live_count - a.live_count;
      if (b.match_count !== a.match_count) return b.match_count - a.match_count;
      return a.event_key.localeCompare(b.event_key);
    });
    return rows;
  }, [eventByKey, scheduleByEvent, todayDateToken]);

  const liveEvents = useMemo(() => {
    return activeEvents.filter((row) => row.is_live);
  }, [activeEvents]);

  useEffect(() => {
    if (!isMobileLayout) return;

    const guard = readHomeMobileAutoEventGuard();
    if (guard?.dayToken === todayDateToken) return;

    const selectedSchedule = selectedEventKey ? scheduleByEvent[selectedEventKey]?.matches || [] : [];
    const selectedHasTodayMatches = selectedEventKey
      ? selectHomeDayMatches(selectedSchedule, nowMs).length > 0
      : false;
    const selectedRunsTodayByDate = selectedEventKey
      ? eventRunsOnDateToken(eventByKey[selectedEventKey], todayDateToken)
      : false;

    if (selectedEventKey && (selectedRunsTodayByDate || selectedHasTodayMatches)) {
      writeHomeMobileAutoEventGuard(todayDateToken);
      return;
    }

    if (!mobileAutoOpenEventKey) return;

    setSelectedEventKey(mobileAutoOpenEventKey);
    writeHomeMobileAutoEventGuard(todayDateToken);
  }, [
    eventByKey,
    isMobileLayout,
    mobileAutoOpenEventKey,
    nowMs,
    scheduleByEvent,
    selectedEventKey,
    todayDateToken,
  ]);

  const scheduleDateRangeByEvent = useMemo(() => {
    const next: Record<string, EventDateRange> = {};
    for (const [eventKey, schedule] of Object.entries(scheduleByEvent)) {
      const matches = schedule?.matches || [];
      let minSec: number | null = null;
      let maxSec: number | null = null;
      for (const match of matches) {
        const sec = typeof match.scheduled_time === 'number' && match.scheduled_time > 0
          ? match.scheduled_time
          : null;
        if (sec === null) continue;
        if (minSec === null || sec < minSec) minSec = sec;
        if (maxSec === null || sec > maxSec) maxSec = sec;
      }
      if (minSec === null && maxSec === null) continue;
      const startDate = minSec !== null ? new Date(minSec * 1000) : null;
      const endDate = maxSec !== null ? new Date(maxSec * 1000) : startDate;
      const startMs = startDate
        ? Date.UTC(startDate.getUTCFullYear(), startDate.getUTCMonth(), startDate.getUTCDate())
        : null;
      const endMs = endDate
        ? Date.UTC(endDate.getUTCFullYear(), endDate.getUTCMonth(), endDate.getUTCDate())
        : startMs;
      next[normalizeEventKey(eventKey)] = normalizeDateRange({ startMs, endMs });
    }
    return next;
  }, [scheduleByEvent]);

  const calendarSourceEvents = useMemo(
    () =>
      mergeEventLists(calendarSeasonEvents, suggestedEvents).filter((event) => {
        const key = normalizeEventKey(event.event_key);
        return matchesCalendarDisplayYear(event, HOME_CALENDAR_DISPLAY_YEAR, scheduleDateRangeByEvent[key]);
      }),
    [calendarSeasonEvents, scheduleDateRangeByEvent, suggestedEvents],
  );

  const calendarEventRows = useMemo(() => {
    return calendarSourceEvents
      .map((event) => {
        const key = normalizeEventKey(event.event_key);
        const resolved = resolveEventDateRange(event, scheduleDateRangeByEvent[key]);
        if (!resolved.startMs && !resolved.endMs) return null;
        return {
          event,
          startMs: resolved.startMs,
          endMs: resolved.endMs,
        };
      })
      .filter((row): row is { event: EventSearchItem; startMs: number | null; endMs: number | null } => Boolean(row))
      .sort((a, b) => {
        const aMs = a.startMs ?? a.endMs ?? Number.MAX_SAFE_INTEGER;
        const bMs = b.startMs ?? b.endMs ?? Number.MAX_SAFE_INTEGER;
        if (aMs !== bMs) return aMs - bMs;
        return a.event.event_key.localeCompare(b.event.event_key);
      });
  }, [calendarSourceEvents, scheduleDateRangeByEvent]);

  const modalDayEvents = useMemo(() => {
    const map = new Map<string, EventSearchItem[]>();
    for (const row of calendarEventRows) {
      if (!row.startMs) continue;
      const start = new Date(row.startMs);
      const end = row.endMs ? new Date(row.endMs) : start;
      const cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
      const endUtc = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate()));
      while (cursor.getTime() <= endUtc.getTime()) {
        const token = `${cursor.getUTCFullYear()}-${String(cursor.getUTCMonth() + 1).padStart(2, '0')}-${String(cursor.getUTCDate()).padStart(2, '0')}`;
        const existing = map.get(token);
        if (existing) {
          existing.push(row.event);
        } else {
          map.set(token, [row.event]);
        }
        cursor.setUTCDate(cursor.getUTCDate() + 1);
      }
    }
    return map;
  }, [calendarEventRows]);

  const modalGridDays = useMemo(() => {
    const [yearStr, monthStr] = calendarMonth.split('-');
    const year = parseInt(yearStr, 10);
    const month = parseInt(monthStr, 10) - 1;
    if (!Number.isFinite(year) || !Number.isFinite(month)) return [];
    const firstOfMonth = new Date(Date.UTC(year, month, 1));
    const firstDay = firstOfMonth.getUTCDay();
    const gridStart = new Date(firstOfMonth);
    gridStart.setUTCDate(1 - firstDay);
    const days: { date: Date; token: string; inMonth: boolean; dayNum: number }[] = [];
    for (let i = 0; i < 42; i++) {
      const d = new Date(gridStart);
      d.setUTCDate(gridStart.getUTCDate() + i);
      const token = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
      days.push({ date: d, token, inMonth: d.getUTCMonth() === month, dayNum: d.getUTCDate() });
    }
    return days;
  }, [calendarMonth]);

  const calendarAvailableMonths = useMemo(() => {
    const tokens = new Set<string>();
    for (const row of calendarEventRows) {
      for (const token of monthTokensForRange(row.startMs, row.endMs)) {
        tokens.add(token);
      }
    }
    return Array.from(tokens).sort();
  }, [calendarEventRows]);

  useEffect(() => {
    if (autoAdjustedCalendarMonthRef.current) return;
    if (calendarAvailableMonths.length === 0) return;
    autoAdjustedCalendarMonthRef.current = true;
    if (calendarAvailableMonths.includes(calendarMonth)) return;
    const nowToken = monthTokenFromMs(Date.now());
    const nextMonth = calendarAvailableMonths.find((token) => token >= nowToken) || calendarAvailableMonths[0];
    if (nextMonth) setCalendarMonth(nextMonth);
  }, [calendarAvailableMonths, calendarMonth]);

  const visibleCalendarEvents = useMemo(
    () =>
      calendarEventRows
        .filter((row) => monthTokensForRange(row.startMs, row.endMs).includes(calendarMonth))
        .map((row) => row.event),
    [calendarEventRows, calendarMonth],
  );

  const dateTbaCalendarEvents = useMemo(
    () =>
      calendarSourceEvents
        .filter((event) => {
          const key = normalizeEventKey(event.event_key);
          const resolved = resolveEventDateRange(event, scheduleDateRangeByEvent[key]);
          return !resolved.startMs && !resolved.endMs;
        })
        .sort((a, b) => a.event_key.localeCompare(b.event_key)),
    [calendarSourceEvents, scheduleDateRangeByEvent],
  );

  const selectedDayUtcMs = useMemo(() => {
    const day = new Date(selectedDayMs);
    return Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate());
  }, [selectedDayMs]);

  const selectedDayPrimaryEventName = useMemo(() => {
    const dayCandidates: { name: string; earliestSec: number }[] = [];
    for (const [eventKey, schedule] of Object.entries(scheduleByEvent)) {
      const dayMatches = selectHomeDayMatches(schedule?.matches || [], selectedDayMs);
      if (dayMatches.length === 0) continue;
      const earliestSec = Math.min(
        ...dayMatches.map((match) =>
          typeof match.scheduled_time === 'number' && Number.isFinite(match.scheduled_time)
            ? match.scheduled_time
            : Number.MAX_SAFE_INTEGER),
      );
      const resolvedName = (schedule?.event_name || eventByKey[eventKey]?.name || '').trim();
      if (!resolvedName) continue;
      dayCandidates.push({ name: resolvedName, earliestSec });
    }

    dayCandidates.sort((a, b) => {
      if (a.earliestSec !== b.earliestSec) return a.earliestSec - b.earliestSec;
      return a.name.localeCompare(b.name);
    });
    if (dayCandidates.length > 0) return dayCandidates[0].name;

    const calendarFallback = calendarEventRows.find((row) => {
      const startMs = row.startMs ?? row.endMs;
      const endMs = row.endMs ?? row.startMs;
      if (startMs === null || endMs === null) return false;
      return selectedDayUtcMs >= startMs && selectedDayUtcMs <= endMs;
    });
    return (calendarFallback?.event.name || '').trim();
  }, [calendarEventRows, eventByKey, scheduleByEvent, selectedDayMs, selectedDayUtcMs]);

  const selectedEventDisplay = useMemo(() => {
    const selectedEvent = eventByKey[selectedEventKey];
    if (selectedEvent?.name) return selectedEvent.name;
    const scheduledName = (selectedEventKey ? scheduleByEvent[selectedEventKey]?.event_name || '' : '').trim();
    if (scheduledName) return scheduledName;
    if (selectedDayPrimaryEventName) return selectedDayPrimaryEventName;
    if (selectedEventKey) return selectedEventKey;
    return 'No event selected';
  }, [eventByKey, scheduleByEvent, selectedDayPrimaryEventName, selectedEventKey]);

  const selectedStreamWatchUrl = useMemo(() => {
    if (!selectedEventStream) return null;
    const preferred = selectedEventStream.preferred_stream;
    const fallback = selectedEventStream.streams.find((stream) => Boolean(stream.watch_url));
    return preferred?.watch_url || fallback?.watch_url || selectedEventStream.game_day_url || null;
  }, [selectedEventStream]);

  const selectedStreamEmbedUrl = selectedEventStream?.preferred_stream?.embed_url || null;

  const hasAnySelectedDayMatches = useMemo(() => {
    return feedEventKeys.some((eventKey) => {
      const matches = scheduleByEvent[eventKey]?.matches || [];
      return selectHomeDayMatches(matches, selectedDayMs).length > 0;
    });
  }, [feedEventKeys, scheduleByEvent, selectedDayMs]);

  const selectedDayHeadingLabel = useMemo(
    () => homeDayHeading(selectedDayMs, nowMs),
    [nowMs, selectedDayMs],
  );
  const selectedDayDateLabel = useMemo(
    () => new Date(selectedDayMs).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' }),
    [selectedDayMs],
  );
  const selectedDayInputValue = useMemo(() => toLocalDateInputValue(selectedDayMs), [selectedDayMs]);

  const moveSelectedDay = useCallback((delta: number) => {
    setSelectedDayMs((prev) => shiftLocalDay(prev, delta));
  }, []);

  const openSelectedDayPicker = useCallback(() => {
    if (isMobileLayout) {
      setMobileDrawerOpen(false);
      setMobileCalendarOpen(true);
      return;
    }
    setCalendarModalOpen(true);
  }, [isMobileLayout]);

  const nextLiveHomeMatch = useMemo(() => {
    const candidateEventKeys = [selectedEventKey, ...feedEventKeys, ...Object.keys(scheduleByEvent)].filter(
      (key, idx, arr) => Boolean(key) && arr.indexOf(key) === idx,
    );
    const nowSec = Math.floor(nowMs / 1000);
    const scheduleSec = (match: EventScheduleItem): number | null => {
      if (typeof match.scheduled_time !== 'number' || !Number.isFinite(match.scheduled_time)) return null;
      return Math.floor(match.scheduled_time);
    };

    const sortedRowsByEvent = candidateEventKeys.map((eventKey) => {
      const rows = [...(scheduleByEvent[eventKey]?.matches || [])];
      rows.sort((a, b) => {
        const aTime = scheduleSec(a) ?? Number.MAX_SAFE_INTEGER;
        const bTime = scheduleSec(b) ?? Number.MAX_SAFE_INTEGER;
        if (aTime !== bTime) return aTime - bTime;
        return a.match_key.localeCompare(b.match_key);
      });
      return { eventKey, rows };
    });

    for (const { eventKey, rows } of sortedRowsByEvent) {
      const liveRow = rows.find((row) => resolveHomeMatchState(row, nowMs) === 'live');
      if (liveRow) return { eventKey, match: liveRow };
    }

    // Fallback: if scores are delayed, treat the most recently started unfinished match as live-like.
    for (const { eventKey, rows } of sortedRowsByEvent) {
      const delayedRow = rows
        .filter((row) => resolveHomeMatchState(row, nowMs) !== 'ended')
        .filter((row) => {
          const scheduled = scheduleSec(row);
          return scheduled !== null && scheduled <= nowSec;
        })
        .sort((a, b) => {
          const aTime = scheduleSec(a) ?? Number.MIN_SAFE_INTEGER;
          const bTime = scheduleSec(b) ?? Number.MIN_SAFE_INTEGER;
          if (aTime !== bTime) return bTime - aTime;
          return a.match_key.localeCompare(b.match_key);
        })[0];
      if (delayedRow) return { eventKey, match: delayedRow };
    }

    for (const { eventKey, rows } of sortedRowsByEvent) {
      const upcomingRow = rows.find((row) => {
        if (resolveHomeMatchState(row, nowMs) === 'ended') return false;
        const scheduled = scheduleSec(row);
        return scheduled !== null && scheduled > nowSec;
      });
      if (upcomingRow) return { eventKey, match: upcomingRow };
    }

    for (const { eventKey, rows } of sortedRowsByEvent) {
      const unresolvedRow = rows.find((row) => resolveHomeMatchState(row, nowMs) !== 'ended');
      if (unresolvedRow) return { eventKey, match: unresolvedRow };
    }

    return null;
  }, [feedEventKeys, nowMs, scheduleByEvent, selectedEventKey]);

  const nextHomeMatchHero = useMemo(() => {
    if (!nextLiveHomeMatch) return null;
    const timer = liveTimerLabel(nextLiveHomeMatch.match.scheduled_time ?? null, nowMs);
    // 'unknown' means no published start time and 'ended' means it is over.
    // Neither has a countdown, so neither gets the display step — a hero
    // reading "Pending publish" would be a label pretending to be a value.
    if (timer.state !== 'upcoming' && timer.state !== 'live') return null;
    const eventName =
      scheduleByEvent[nextLiveHomeMatch.eventKey]?.event_name
      || nextLiveHomeMatch.eventKey.toUpperCase();
    return {
      label: timer.label,
      value: timer.value,
      sub: `${nextLiveHomeMatch.match.display_name} \u00b7 ${eventName}`,
      live: timer.state === 'live',
    };
  }, [nextLiveHomeMatch, nowMs, scheduleByEvent]);


  const eventRankingRows = useMemo(() => {
    const payload = asRecord(selectedEventRankings?.rankings);
    const rows = Array.isArray(payload?.rankings) ? payload.rankings : [];

    return rows
      .map((item) => {
        const row = asRecord(item);
        if (!row) return null;
        const teamKey = typeof row.team_key === 'string' ? row.team_key.toLowerCase() : '';
        if (!teamKey) return null;
        const record = asRecord(row.record);
        const teamNumberFromRecord = parseNumber(row.team_number);
        const match = /^frc(\d+)$/i.exec(teamKey);
        const parsedFromKey = match ? Number(match[1]) : 0;

        return {
          team_key: teamKey,
          team_number: teamNumberFromRecord ?? parsedFromKey,
          nickname: typeof row.nickname === 'string' ? row.nickname : teamKey,
          rank: parseNumber(row.rank),
          matches_played: parseNumber(row.matches_played),
          record:
            record && (record.wins !== undefined || record.losses !== undefined || record.ties !== undefined)
              ? `${parseNumber(record.wins) ?? 0}-${parseNumber(record.losses) ?? 0}-${parseNumber(record.ties) ?? 0}`
              : 'N/A',
        } satisfies RankingRow;
      })
      .filter((row): row is RankingRow => Boolean(row))
      .sort((a, b) => {
        const rankA = a.rank ?? Number.POSITIVE_INFINITY;
        const rankB = b.rank ?? Number.POSITIVE_INFINITY;
        if (rankA !== rankB) return rankA - rankB;
        return a.team_number - b.team_number;
      });
  }, [selectedEventRankings]);

  const leaderboardRows = useMemo(() => eventRankingRows.slice(0, 8), [eventRankingRows]);

  const filteredTeamsRows = useMemo(() => {
    const query = teamsQuery.trim().toLowerCase();
    if (!query) return eventRankingRows;
    return eventRankingRows.filter((row) => (
      row.team_key.includes(query) ||
      row.nickname.toLowerCase().includes(query) ||
      String(row.team_number).includes(query)
    ));
  }, [eventRankingRows, teamsQuery]);

  const sortedTeamsRows = useMemo(() => {
    const rows = [...filteredTeamsRows];
    if (teamsSortMode === 'team') {
      rows.sort((a, b) => a.team_number - b.team_number);
      return rows;
    }
    rows.sort((a, b) => {
      const rankA = a.rank ?? Number.POSITIVE_INFINITY;
      const rankB = b.rank ?? Number.POSITIVE_INFINITY;
      if (rankA !== rankB) return rankA - rankB;
      return a.team_number - b.team_number;
    });
    return rows;
  }, [filteredTeamsRows, teamsSortMode]);

  const teamsTotalCount = sortedTeamsRows.length;
  const teamsTotalPages = Math.max(1, Math.ceil(teamsTotalCount / Math.max(1, teamsPerPage)));

  useEffect(() => {
    setTeamsPage((current) => Math.min(current, teamsTotalPages));
  }, [teamsTotalPages]);

  const pagedTeamsRows = useMemo(() => {
    const start = Math.max(0, (teamsPage - 1) * teamsPerPage);
    return sortedTeamsRows.slice(start, start + teamsPerPage);
  }, [sortedTeamsRows, teamsPage, teamsPerPage]);

  /* Team number and nickname were two columns on desktop and one line on the
     phone. They are one entity, so they are one column now — which also makes
     the narrow-width card head read as the team rather than as a bare index. */
  const teamsInsightColumns: TableColumn<(typeof pagedTeamsRows)[number]>[] = [
    {
      key: 'team',
      label: 'Team',
      render: (row) => (
        <button type="button" className="center-inline-link" onClick={() => openTeamCenter(row.team_key)}>
          #{row.team_number}
          {row.nickname && row.nickname !== row.team_key ? ` ${row.nickname}` : ''}
        </button>
      ),
    },
    { key: 'rank', label: 'Rank', numeric: true, render: (row) => row.rank ?? '-' },
    { key: 'record', label: 'Record', render: (row) => row.record },
    { key: 'matches', label: 'Matches', numeric: true, render: (row) => row.matches_played ?? '-' },
  ];

  const teamsPageSummary = useMemo(() => {
    const start = teamsTotalCount > 0 ? (teamsPage - 1) * teamsPerPage + 1 : 0;
    const end = Math.min(teamsTotalCount, teamsPage * teamsPerPage);
    return `${start}-${end} of ${teamsTotalCount}`;
  }, [teamsPage, teamsPerPage, teamsTotalCount]);

  const streamCardSummary = useMemo(() => {
    if (loadingSelectedContext) return 'Loading stream context...';
    if (!selectedEventStream) return 'No stream data available.';
    if (!selectedEventStream.available) return selectedEventStream.detail || 'No webcast available.';
    const streamCount = selectedEventStream.streams.length;
    return `${streamCount} stream${streamCount === 1 ? '' : 's'} available for ${selectedEventDisplay}.`;
  }, [loadingSelectedContext, selectedEventDisplay, selectedEventStream]);

  const leaderboardSummary = useMemo(() => {
    if (loadingSelectedContext) return 'Loading rankings...';
    if (leaderboardRows.length === 0) return 'No rankings available yet.';
    const topRow = leaderboardRows[0];
    return `Top team: #${topRow.team_number} (${topRow.record})`;
  }, [leaderboardRows, loadingSelectedContext]);

  const liveEventsSummary = useMemo(() => {
    if (loadingHome) return 'Loading today events...';
    if (liveEvents.length === 0) return 'No events scheduled for today in current feed.';
    return `${liveEvents.length} event(s) today`;
  }, [liveEvents.length, loadingHome]);

  const teamsInsightsSummary = useMemo(() => {
    if (loadingSelectedContext) return 'Loading event rankings...';
    if (sortedTeamsRows.length === 0) return 'No team rows matched current filters.';
    return `${pagedTeamsRows.length} row(s) · ${teamsPageSummary}`;
  }, [loadingSelectedContext, pagedTeamsRows.length, sortedTeamsRows.length, teamsPageSummary]);

  const mobileCollapsibleCardKeys = useMemo(() => {
    return [
      ...HOME_MOBILE_DEFAULT_COLLAPSED_CARD_KEYS,
      ...feedSections.map((section) => `feed-${section.event_key}`),
    ];
  }, [feedSections]);

  const collapsedMobileCardCount = useMemo(() => {
    return mobileCollapsibleCardKeys.reduce((count, key) => {
      if (!mobileCollapsedCards[key]) return count;
      return count + 1;
    }, 0);
  }, [mobileCollapsedCards, mobileCollapsibleCardKeys]);

  const desktopCollapsibleFeedKeys = useMemo(
    () => feedSections.map((section) => `feed-${section.event_key}`),
    [feedSections],
  );

  const collapsedDesktopFeedCount = useMemo(() => {
    return desktopCollapsibleFeedKeys.reduce((count, key) => {
      if (!desktopCollapsedFeedCards[key]) return count;
      return count + 1;
    }, 0);
  }, [desktopCollapsedFeedCards, desktopCollapsibleFeedKeys]);

  const allMobileCardsCollapsed =
    isMobileLayout &&
    mobileCollapsibleCardKeys.length > 0 &&
    collapsedMobileCardCount === mobileCollapsibleCardKeys.length;

  const allDesktopFeedCollapsed =
    !isMobileLayout &&
    desktopCollapsibleFeedKeys.length > 0 &&
    collapsedDesktopFeedCount === desktopCollapsibleFeedKeys.length;

  const allHomeCardsCollapsed = isMobileLayout ? allMobileCardsCollapsed : allDesktopFeedCollapsed;
  const canToggleAllHomeCards = isMobileLayout
    ? mobileCollapsibleCardKeys.length > 0
    : desktopCollapsibleFeedKeys.length > 0;

  function toggleAllHomeCardsCollapsed() {
    if (!canToggleAllHomeCards) return;
    if (isMobileLayout) {
      const collapse = !allMobileCardsCollapsed;
      setMobileCollapsedCards((previous) => {
        const next = { ...previous };
        for (const key of mobileCollapsibleCardKeys) {
          next[key] = collapse;
        }
        return next;
      });
      return;
    }

    const collapse = !allDesktopFeedCollapsed;
    setDesktopCollapsedFeedCards((previous) => {
      const next = { ...previous };
      for (const key of desktopCollapsibleFeedKeys) {
        next[key] = collapse;
      }
      return next;
    });
  }

  useEffect(() => {
    if (!isMobileLayout) return;
    setMobileCollapsedCards((previous) => {
      const next = { ...previous };
      let changed = false;

      for (const key of HOME_MOBILE_DEFAULT_COLLAPSED_CARD_KEYS) {
        if (next[key] !== undefined) continue;
        next[key] = true;
        changed = true;
      }

      for (const section of feedSections) {
        const key = `feed-${section.event_key}`;
        if (next[key] !== undefined) continue;
        next[key] = false;
        changed = true;
      }

      return changed ? next : previous;
    });
  }, [feedSections, isMobileLayout]);

  useEffect(() => {
    if (!isMobileLayout) {
      setMobileExpandedMatches((previous) => (Object.keys(previous).length > 0 ? {} : previous));
      return;
    }
    const available = new Set<string>();
    for (const section of feedSections) {
      for (const match of section.matches) {
        available.add(`${section.event_key}::${match.match_key}`);
      }
    }
    setMobileExpandedMatches((previous) => {
      const next: Record<string, boolean> = {};
      let changed = false;
      for (const [key, expanded] of Object.entries(previous)) {
        if (!expanded) continue;
        if (!available.has(key)) {
          changed = true;
          continue;
        }
        next[key] = true;
      }
      if (!changed && Object.keys(next).length === Object.keys(previous).length) {
        return previous;
      }
      return next;
    });
  }, [feedSections, isMobileLayout]);

  useEffect(() => {
    setDesktopCollapsedFeedCards((previous) => {
      const next: Record<string, boolean> = {};
      for (const section of feedSections) {
        const key = `feed-${section.event_key}`;
        if (previous[key]) next[key] = true;
      }
      const previousKeys = Object.keys(previous);
      const nextKeys = Object.keys(next);
      if (
        previousKeys.length === nextKeys.length &&
        previousKeys.every((key) => previous[key] === next[key])
      ) {
        return previous;
      }
      return next;
    });
  }, [feedSections]);

  function cardContentId(cardId: string): string {
    return `home-card-${cardId.replace(/[^a-z0-9_-]/gi, '-')}`;
  }

  function isCardCollapsed(cardId: string): boolean {
    if (!isMobileLayout) return false;
    return Boolean(mobileCollapsedCards[cardId]);
  }

  function toggleCardCollapsed(cardId: string) {
    if (!isMobileLayout) return;
    setMobileCollapsedCards((previous) => ({
      ...previous,
      [cardId]: !previous[cardId],
    }));
  }

  function renderMobileCollapseButton(cardId: string, label: string) {
    if (!isMobileLayout) return null;
    const collapsed = isCardCollapsed(cardId);
    return (
      <button
        type="button"
        className={`home-card-collapse-btn ${collapsed ? 'collapsed' : ''}`.trim()}
        onClick={() => toggleCardCollapsed(cardId)}
        aria-label={collapsed ? `Expand ${label}` : `Minimize ${label}`}
        aria-controls={cardContentId(cardId)}
        aria-expanded={!collapsed}
      >
        <span className="home-card-collapse-icon" aria-hidden="true">
          {collapsed ? <ChevronDownIcon className="icon-inline" /> : <ChevronDownIcon className="icon-inline icon-rotate-180" />}
        </span>
        <span>{collapsed ? 'Expand' : 'Minimize'}</span>
      </button>
    );
  }

  function isFeedSectionCollapsed(cardId: string): boolean {
    if (isMobileLayout) return isCardCollapsed(cardId);
    return Boolean(desktopCollapsedFeedCards[cardId]);
  }

  function toggleFeedSectionCollapsed(cardId: string) {
    if (isMobileLayout) {
      toggleCardCollapsed(cardId);
      return;
    }
    setDesktopCollapsedFeedCards((previous) => ({
      ...previous,
      [cardId]: !previous[cardId],
    }));
  }

  function renderFeedSectionCollapseButton(cardId: string, label: string) {
    const collapsed = isFeedSectionCollapsed(cardId);
    return (
      <button
        type="button"
        className={`home-card-collapse-btn ${collapsed ? 'collapsed' : ''}`.trim()}
        onClick={() => toggleFeedSectionCollapsed(cardId)}
        aria-label={collapsed ? `Expand ${label}` : `Minimize ${label}`}
        aria-controls={cardContentId(cardId)}
        aria-expanded={!collapsed}
      >
        <span className="home-card-collapse-icon" aria-hidden="true">
          {collapsed ? <ChevronDownIcon className="icon-inline" /> : <ChevronDownIcon className="icon-inline icon-rotate-180" />}
        </span>
        <span>{collapsed ? 'Expand' : 'Minimize'}</span>
      </button>
    );
  }

  function feedSectionSummary(section: HomeFeedSection): string {
    if (section.matches.length === 0) {
      return `No ${homeFilterLabel(activeFilter).toLowerCase()} matches in view.`;
    }
    const nextMatch = section.matches[0];
    const timer = liveTimerLabel(nextMatch.scheduled_time, nowMs);
    return `${section.matches.length}/${section.total_filtered_matches} matches · ${nextMatch.display_name} ${timer.value}`;
  }

  const allEventsCollapsed = isCardCollapsed('all-events');
  const teamsInsightsCollapsed = isCardCollapsed('teams-insights');
  const compareBuilderCollapsed = isCardCollapsed('compare-builder');
  const liveStreamCollapsed = isCardCollapsed('live-stream');
  const leaderboardCollapsed = isCardCollapsed('leaderboard');

  function openMatch(eventKey: string, matchKey: string) {
    navigate(buildMatchCenterPath(eventKey, matchKey));
  }

  function mobileMatchRowKey(eventKey: string, matchKey: string): string {
    return `${eventKey}::${matchKey}`;
  }

  function isMobileMatchExpanded(eventKey: string, matchKey: string): boolean {
    if (!isMobileLayout) return false;
    return Boolean(mobileExpandedMatches[mobileMatchRowKey(eventKey, matchKey)]);
  }

  function expandMobileMatch(eventKey: string, matchKey: string) {
    if (!isMobileLayout) return;
    const rowKey = mobileMatchRowKey(eventKey, matchKey);
    setMobileExpandedMatches((previous) => ({
      ...previous,
      [rowKey]: true,
    }));
  }

  function collapseMobileMatch(eventKey: string, matchKey: string) {
    if (!isMobileLayout) return;
    const rowKey = mobileMatchRowKey(eventKey, matchKey);
    setMobileExpandedMatches((previous) => {
      if (!previous[rowKey]) return previous;
      const next = { ...previous };
      delete next[rowKey];
      return next;
    });
  }

  async function copyMatchDeepLink(eventKey: string, matchKey: string) {
    const path = buildMatchCenterPath(eventKey, matchKey);
    const deepLink = new URL(path, window.location.origin).toString();
    const copied = await copyTextToClipboard(deepLink);
    if (copied) {
      setStatusText(`Copied deep link for ${normalizeMatchKey(matchKey, eventKey).toUpperCase()}.`);
      return;
    }
    setErrorText('Unable to copy match link on this device/browser.');
  }

  function openEventCenter(eventKey: string) {
    const normalized = normalizeEventKey(eventKey);
    if (!normalized) return;
    navigate(`/events?event=${encodeURIComponent(normalized)}&tab=schedule`);
  }

  function openTeamCenter(teamKey: string, eventKey: string = selectedEventKey) {
    const params = new URLSearchParams();
    params.set('team', teamKey);
    if (eventKey) params.set('event', eventKey);
    navigate(`/team-center?${params.toString()}`);
  }

  return (
    <div className="home-fotmob-layout">
      {!isMobileLayout && (
      <aside className="home-fotmob-left">
        <section className={`home-fotmob-card ${allEventsCollapsed ? 'home-card-collapsed' : ''}`.trim()}>
          <header className="home-card-head">
            <h3>Today&apos;s Events</h3>
            {renderMobileCollapseButton('all-events', 'Today Events')}
          </header>
          {allEventsCollapsed ? (
            <p id={cardContentId('all-events')} className="home-card-collapsed-hint">
              {liveEventsSummary}
            </p>
          ) : (
            <>
              <p id={cardContentId('all-events')} className="home-fotmob-note helper-text">
                Events scheduled to run today (using event start/end dates).
              </p>
              {loadingHome ? (
                <div className="center-loading-state">
                  <SkeletonBlock rows={3} compact />
                </div>
              ) : null}
              {!loadingHome && liveEvents.length === 0 ? (
                errorText ? (
                  <EmptyState compact type="offline" title="Connection failed" description="Could not load today's events." />
                ) : (
                  <EmptyState compact title="No events today" description="No events are scheduled to run today in the current feed." />
                )
              ) : null}
              {liveEvents.length > 0 ? (
                <div className="home-fotmob-event-list">
                  {liveEvents.map((item) => (
                    <button
                      key={`home-live-event-${item.event_key}`}
                      type="button"
                      className={`home-event-item home-event-item-rail ${selectedEventKey === item.event_key ? 'active' : ''}`.trim()}
                      onClick={() => selectHomeEventKey(item.event_key, { manual: true })}
                    >
                      <strong className="home-event-item-title">{item.name}</strong>
                      <small className="home-event-item-location">{item.location}</small>
                      <div className="home-event-item-tags">
                        <span className="home-event-item-tag home-event-item-tag-live">
                          <CalendarIcon className="icon-inline" /> Today
                        </span>
                        <span className="home-event-item-tag">
                          {item.status_label}
                          {item.match_count > 0 ? ` · ${item.match_count} matches` : ''}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              ) : null}
            </>
          )}
        </section>
      </aside>
      )}

      <main className="home-fotmob-center">
        <section className="home-fotmob-toolbar">
          <div className="home-fotmob-toolbar-left">
            <div className="home-day-switcher" role="group" aria-label="Change schedule day">
              <button
                type="button"
                className="home-day-nav-btn"
                onClick={() => moveSelectedDay(-1)}
                aria-label="Show yesterday schedule"
                title="Previous day"
              >
                <ChevronLeftIcon className="icon-inline" />
              </button>
              <button
                type="button"
                className="home-day-picker-btn"
                onClick={openSelectedDayPicker}
                title="Pick a day from calendar"
                aria-label={`Pick a day from calendar. Current day: ${selectedDayDateLabel}`}
              >
                <CalendarIcon className="icon-inline" />
                <span className="home-day-picker-title">{selectedDayHeadingLabel}</span>
                <ChevronDownIcon className="icon-inline" />
              </button>
              <button
                type="button"
                className="home-day-nav-btn"
                onClick={() => moveSelectedDay(1)}
                aria-label="Show next day schedule"
                title="Next day"
              >
                <ChevronRightIcon className="icon-inline" />
              </button>
              <input
                ref={dayPickerInputRef}
                type="date"
                value={selectedDayInputValue}
                onChange={(event) => {
                  const parsedDay = fromLocalDateInputValue(event.target.value);
                  if (parsedDay !== null) setSelectedDayMs(parsedDay);
                }}
                className="home-day-picker-input"
                aria-label="Select schedule day"
                tabIndex={-1}
              />
            </div>
            <p>{selectedDayDateLabel}</p>
            {!isMobileLayout && !selectedEventKey && selectedDayPrimaryEventName ? (
              <small className="home-day-default-event">{selectedDayPrimaryEventName}</small>
            ) : null}
          </div>

          {/* ── Expandable event search ─────────────────────────────── */}
          <div
            ref={eventSearchContainerRef}
            className={`home-event-search-widget ${eventSearchOpen ? 'expanded' : ''}`.trim()}
          >
            {!eventSearchOpen ? (
              <button
                type="button"
                className="home-event-search-trigger"
                onClick={() => setEventSearchOpen(true)}
                title="Search for an event"
                aria-label="Search events"
              >
                <SearchIcon className="icon-inline" />
                <span className="home-event-search-trigger-label">Search Events</span>
              </button>
            ) : (
              <div className="home-event-search-expanded">
                <SearchIcon className="icon-inline home-event-search-icon" />
                <input
                  ref={eventSearchInputRef}
                  type="text"
                  className="home-event-search-input"
                  placeholder="Event name, key, or city…"
                  value={eventSearchQuery}
                  onChange={(e) => setEventSearchQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setEventSearchOpen(false);
                    if (e.key === 'Enter' && eventSearchResults.length > 0) {
                      selectHomeEventKey(eventSearchResults[0].event_key, { manual: true });
                      setEventSearchOpen(false);
                    }
                  }}
                  aria-label="Search events"
                />
                <button
                  type="button"
                  className="home-event-search-close"
                  onClick={() => setEventSearchOpen(false)}
                  aria-label="Close search"
                >
                  ✕
                </button>
                {eventSearchQuery.trim() && (
                  <div className="home-event-search-dropdown">
                    {eventSearchBusy ? (
                      <div className="home-event-search-status">Searching…</div>
                    ) : eventSearchResults.length === 0 ? (
                      <div className="home-event-search-status">No events found</div>
                    ) : (
                      eventSearchResults.map((ev) => (
                        <button
                          key={`esearch-${ev.event_key}`}
                          type="button"
                          className={`home-event-search-result ${selectedEventKey === normalizeEventKey(ev.event_key) ? 'active' : ''}`.trim()}
                          onClick={() => {
                            selectHomeEventKey(ev.event_key, { manual: true });
                            setEventSearchOpen(false);
                          }}
                        >
                          <strong>{ev.name}</strong>
                          <span className="home-event-search-result-meta">
                            <small>{ev.event_key}</small>
                            <small>{eventLocationLabel(ev)}</small>
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* What Home is actually for: how long until the next match. The
              countdown already existed inside nextLiveHomeMatch, used only to
              enable an "Open Next Live" button — the number itself was never
              shown. Rendered only when there is a real scheduled time; a match
              with no published time has nothing to count down, and a hero
              reading "Pending publish" would be a label pretending to be a
              value. */}
          {nextHomeMatchHero ? (
            <div className="page-hero home-next-hero">
              <Stat
                size="display"
                label={nextHomeMatchHero.label}
                value={nextHomeMatchHero.value}
                sub={nextHomeMatchHero.sub}
                tone={nextHomeMatchHero.live ? 'danger' : 'default'}
              />
            </div>
          ) : null}

          <div className="home-fotmob-filters" role="tablist" aria-label="Home feed filters">
            {(
              [
                { key: 'all', label: 'All', icon: null },
                { key: 'live', label: 'Live', icon: <LiveDotIcon className="icon-inline icon-status-live icon-live-pulse" /> },
                { key: 'upcoming', label: 'Upcoming', icon: <ClockIcon className="icon-inline icon-status-upcoming" /> },
                { key: 'completed', label: 'Final', icon: <CheckCircleIcon className="icon-inline icon-status-final" /> },
              ] as Array<{ key: HomeFilter; label: string; icon: React.ReactNode }>
            ).map((item) => (
              <button
                key={`home-filter-${item.key}`}
                className={`home-filter-btn ${activeFilter === item.key ? 'active' : ''}`.trim()}
                type="button"
                onClick={() => setActiveFilter(item.key)}
                title={`Show ${item.label.toLowerCase()} matches`}
              >
                {item.icon} {item.label}
                <small>{filterCounts[item.key]}</small>
              </button>
            ))}
          </div>
          <div className="home-fotmob-toolbar-actions">
            <button
              type="button"
              className={`home-mini-tab-btn ${allHomeCardsCollapsed ? 'active' : ''}`.trim()}
              onClick={toggleAllHomeCardsCollapsed}
              disabled={!canToggleAllHomeCards}
              title={allHomeCardsCollapsed ? 'Expand all home sections' : 'Minimize all home sections'}
            >
              <ChevronDownIcon className={`icon-inline ${allHomeCardsCollapsed ? '' : 'icon-rotate-180'}`.trim()} /> {allHomeCardsCollapsed ? 'Expand All' : 'Minimize All'}
            </button>
            {!isMobileLayout || nextLiveHomeMatch ? (
              <button
                type="button"
                className="home-mini-tab-btn"
                disabled={!nextLiveHomeMatch}
                onClick={() => {
                  if (!nextLiveHomeMatch) return;
                  openMatch(nextLiveHomeMatch.eventKey, nextLiveHomeMatch.match.match_key);
                }}
                title={nextLiveHomeMatch ? `Open ${nextLiveHomeMatch.match.display_name}` : 'No live matches right now'}
              >
                <LiveDotIcon className="icon-inline icon-status-live icon-live-pulse" /> {isMobileLayout ? 'Live' : 'Open Next Live'}
              </button>
            ) : null}
            {!isMobileLayout ? (
              <button
                type="button"
                className={`home-mini-tab-btn ${teamsPanelOpen ? 'active' : ''}`.trim()}
                onClick={() => setTeamsPanelOpen((prev) => !prev)}
                title="Toggle EPA rankings table"
              >
                <BarChartIcon className="icon-inline" /> Teams Insights
              </button>
            ) : null}
            {isMobileLayout ? (
              <button
                type="button"
                className={`home-mini-tab-btn ${mobileDrawerOpen ? 'active' : ''}`.trim()}
                onClick={() => {
                  setMobileCalendarOpen(false);
                  setMobileDrawerOpen(true);
                }}
                title="View leaderboard, stream & links"
              >
                <InfoIcon className="icon-inline" /> Info
              </button>
            ) : null}
          </div>
        </section>

        {!isMobileLayout ? (
          <section className="home-fotmob-meta-row">
            <span title="Currently selected event">{selectedEventDisplay}</span>
            <span title="Active filter">{homeFilterLabel(activeFilter)} · {filterCounts[activeFilter]} matches</span>
            <span title="Last data refresh">{relativeFromTimestamp(lastUpdatedAt)}</span>
          </section>
        ) : null}

        {teamsPanelOpen ? (
          <section
            className={`home-mini-panel home-teams-insights-panel ${teamsInsightsCollapsed ? 'home-card-collapsed' : ''}`.trim()}
          >
            <header className="home-mini-panel-head home-card-head">
              <div>
                <h3>Teams Insights</h3>
                <small>Event rankings from TBA/FRC</small>
              </div>
              <div className="home-card-head-actions">
                {renderMobileCollapseButton('teams-insights', 'Teams Insights')}
                <div className="center-actions-row compact">
                  <button type="button" className="center-btn ghost" onClick={() => setTeamsPanelOpen(false)}>
                    Close
                  </button>
                </div>
              </div>
            </header>

            {teamsInsightsCollapsed ? (
              <p id={cardContentId('teams-insights')} className="home-card-collapsed-hint">
                {teamsInsightsSummary}
              </p>
            ) : (
              <>
                <div id={cardContentId('teams-insights')} className="home-teams-controls">
                  <input
                    value={teamsQueryInput}
                    onChange={(event) => setTeamsQueryInput(event.target.value)}
                    placeholder="Search team number, key, or name"
                    aria-label="Search teams insights"
                  />
                  <select
                    value={teamsSortMode}
                    onChange={(event) => setTeamsSortMode(normalizeHomeTeamsSortMode(event.target.value))}
                    aria-label="Teams sort mode"
                  >
                    <option value="rank">Sort: Rank</option>
                    <option value="team">Sort: Team</option>
                  </select>
                </div>

                <div className="center-status-row compact">
                  <span className="center-chip">Page {teamsPage} of {teamsTotalPages} · {teamsPageSummary}</span>
                </div>

                {loadingSelectedContext ? (
                  <div className="center-loading-state">
                    <SkeletonBlock rows={4} compact />
                  </div>
                ) : null}
                {!loadingSelectedContext && sortedTeamsRows.length === 0 ? (
                  <EmptyState compact title="No rankings" description="No ranking rows matched this search." />
                ) : null}

                {pagedTeamsRows.length > 0 ? (
                  <>
                    {/* No chip row here, unlike Match Center and Team Center:
                        this board already has an always-present "Sort: …"
                        select in its controls row above, and the chips were the
                        mobile-only twin of that same state. Two controls for
                        one value is worse than the asymmetry it fixed. */}
                    <Table
                      columns={teamsInsightColumns}
                      rows={pagedTeamsRows}
                      rowKey={(row) => `home-teams-row-${row.team_key}`}
                      cardBreakpoint={MOBILE_LAYOUT_BREAKPOINT}
                    />
                  </>
                ) : null}

                <div className="center-actions-row">
                  <button
                    type="button"
                    className="center-btn ghost"
                    disabled={teamsPage <= 1 || loadingSelectedContext}
                    onClick={() => setTeamsPage((prev) => Math.max(1, prev - 1))}
                  >
                    <ChevronLeftIcon className="icon-inline" /> Prev
                  </button>
                  <button
                    type="button"
                    className="center-btn ghost"
                    disabled={teamsPage >= teamsTotalPages || loadingSelectedContext}
                    onClick={() => setTeamsPage((prev) => prev + 1)}
                  >
                    Next <ChevronRightIcon className="icon-inline" />
                  </button>
                  <label className="center-label" htmlFor="home-teams-per-page">
                    Rows/Page
                  </label>
                  <select
                    id="home-teams-per-page"
                    value={teamsPerPage}
                    onChange={(event) => setTeamsPerPage(Number(event.target.value))}
                    className="center-input home-teams-per-page"
                  >
                    <option value={10}>10</option>
                    <option value={25}>25</option>
                    <option value={50}>50</option>
                  </select>
                </div>
              </>
            )}
          </section>
        ) : null}

        {errorText ? (
          <EmptyState type="offline" title="Sync connection failed" description={errorText} />
        ) : !hasAnySelectedDayMatches ? (
          <EmptyState title="No matches scheduled" description={`No matches scheduled for ${selectedDayDateLabel} in the loaded event set.`} />
        ) : null}
        {hasAnySelectedDayMatches && filterCounts[activeFilter] === 0 ? (
          <p className="center-callout muted">
            No {homeFilterLabel(activeFilter).toLowerCase()} matches found.
          </p>
        ) : null}

        <div className="home-fotmob-feed">
          {feedSections.map((section) => {
            const feedCardKey = `feed-${section.event_key}`;
            const sectionCollapsed = isFeedSectionCollapsed(feedCardKey);
            return (
              <article
                key={`home-section-${section.event_key}`}
                className={`home-event-section ${sectionCollapsed ? 'home-card-collapsed' : ''}`.trim()}
              >
                <header className="home-event-section-head home-card-head">
                  <div>
                    <h3>{section.event_name}</h3>
                    <small>
                      {section.location}
                    </small>
                  </div>
                  <div className="home-card-head-actions">
                    {renderFeedSectionCollapseButton(feedCardKey, section.event_name)}
                    <button
                      type="button"
                      className="home-fotmob-btn subtle"
                      onClick={() => openEventCenter(section.event_key)}
                      title="View full event details"
                    >
                      View Event
                    </button>
                  </div>
                </header>

                {sectionCollapsed ? (
                  isMobileLayout ? null : (
                    <p id={cardContentId(feedCardKey)} className="home-card-collapsed-hint">
                      {feedSectionSummary(section)}
                    </p>
                  )
                ) : (
                  <>
                    {section.matches.length === 0 ? (
                      <p id={cardContentId(feedCardKey)} className="center-callout muted">
                        No matches in this filter for this event yet.
                      </p>
                    ) : (
                      <div id={cardContentId(feedCardKey)} className="home-match-list">
                        {section.matches.map((match) => {
                          const timer = liveTimerLabel(match.scheduled_time, nowMs);
                          const winner = match.winner_alliance || null;
                          const hasScores = hasResolvedMatchScores(match);
                          const effectiveState = resolveHomeMatchState(match, nowMs);
                          const isCompleted = effectiveState === 'ended';

                          // Hide "likely complete" matches — timer expired but no
                          // official result (no scores, no winner, not marked done).
                          if (
                            isCompleted &&
                            !hasScores &&
                            !winner &&
                            !match.is_completed
                          ) {
                            return null;
                          }

                          const headerValue = hasScores ? `${match.red_score}-${match.blue_score}` : timer.value;
                          const winnerClass =
                            winner === 'red'
                              ? 'winner-red'
                              : winner === 'blue'
                                ? 'winner-blue'
                                : winner === 'tie'
                                  ? 'winner-tie'
                                  : '';
                          const winnerText =
                            winner === 'red'
                              ? `Red wins ${match.winning_score ?? match.red_score ?? ''}`.trim()
                              : winner === 'blue'
                                ? `Blue wins ${match.winning_score ?? match.blue_score ?? ''}`.trim()
                                : winner === 'tie'
                                  ? 'Match tied'
                                  : null;
                          const mobileExpanded = isMobileMatchExpanded(section.event_key, match.match_key);
                          const compactRed = compactAllianceLabel(match.red);
                          const compactBlue = compactAllianceLabel(match.blue);
                          return (
                            <article
                              key={`home-match-${section.event_key}-${match.match_key}`}
                              className={`home-match-row ${winnerClass} ${isMobileLayout ? (mobileExpanded ? 'mobile-expanded' : 'mobile-compact') : ''}`.trim()}
                            >
                              <button
                                type="button"
                                className="home-match-main"
                                onClick={() => {
                                  if (!isMobileLayout) {
                                    openMatch(section.event_key, match.match_key);
                                    return;
                                  }
                                  if (!mobileExpanded) {
                                    expandMobileMatch(section.event_key, match.match_key);
                                    return;
                                  }
                                  openMatch(section.event_key, match.match_key);
                                }}
                                title={
                                  isMobileLayout
                                    ? mobileExpanded
                                      ? `Open ${match.display_name} in Match Center`
                                      : `Expand ${match.display_name}`
                                    : `Open ${match.display_name} details`
                                }
                                aria-expanded={isMobileLayout ? mobileExpanded : undefined}
                              >
                                {isMobileLayout ? (
                                  <>
                                    {!mobileExpanded ? (
                                      <div className="home-match-compact-body">
                                        <div className="home-match-compact-head">
                                          <strong>{match.display_name}</strong>
                                          <span className={`home-match-compact-state ${effectiveState}`}>{stateTextLabel(effectiveState)}</span>
                                        </div>
                                        <div className="home-match-compact-line">
                                          <span className="home-match-compact-side red">{compactRed}</span>
                                          <span className="home-match-compact-score">{headerValue}</span>
                                          <span className="home-match-compact-side blue">{compactBlue}</span>
                                        </div>
                                      </div>
                                    ) : (
                                      <>
                                        <div className="fm-match-header-row">
                                          <div className="home-match-center-col">
                                            <strong>{match.display_name}</strong>
                                            <small>{fmtDateShort(match.scheduled_time)}</small>
                                          </div>
                                          <div className="fm-match-status-right">
                                            <span className={`home-match-state ${effectiveState}`}>{stateLabel(effectiveState)}</span>
                                            <span className="fm-match-score-timer">{headerValue}</span>
                                          </div>
                                        </div>
                                        <div className="home-match-alliances">
                                          <div className={`home-alliance-line red-line ${winner === 'red' ? 'winner' : ''}`.trim()}>
                                            <span className="red">Red</span>
                                            <small>{match.red.map((team) => `#${team.team_number}`).join(' · ') || 'TBD'}</small>
                                            <strong className="alliance-score">{hasScores ? match.red_score : '-'}</strong>
                                          </div>
                                          <div className={`home-alliance-line blue-line ${winner === 'blue' ? 'winner' : ''}`.trim()}>
                                            <span className="blue">Blue</span>
                                            <small>{match.blue.map((team) => `#${team.team_number}`).join(' · ') || 'TBD'}</small>
                                            <strong className="alliance-score">{hasScores ? match.blue_score : '-'}</strong>
                                          </div>
                                        </div>
                                        {winnerText ? (
                                          <span className="home-winner-chip">{winnerText}</span>
                                        ) : null}
                                        <small className="home-match-open-hint">Tap again to open Match Center</small>
                                      </>
                                    )}
                                  </>
                                ) : (
                                  <>
                                    <span className={`home-match-state ${effectiveState}`}>{stateLabel(effectiveState)}</span>
                                    <div className="home-match-center-col">
                                      <strong>{match.display_name}</strong>
                                      <small>{fmtDateShort(match.scheduled_time)}</small>
                                    </div>
                                    <div className="home-match-alliances">
                                      <div className={`home-alliance-line red-line ${winner === 'red' ? 'winner' : ''}`.trim()}>
                                        <span className="red">Red</span>
                                        <small>{match.red.map((team) => `#${team.team_number}`).join(' · ') || 'TBD'}</small>
                                        <strong className="alliance-score">{hasScores ? match.red_score : '-'}</strong>
                                      </div>
                                      <div className={`home-alliance-line blue-line ${winner === 'blue' ? 'winner' : ''}`.trim()}>
                                        <span className="blue">Blue</span>
                                        <small>{match.blue.map((team) => `#${team.team_number}`).join(' · ') || 'TBD'}</small>
                                        <strong className="alliance-score">{hasScores ? match.blue_score : '-'}</strong>
                                      </div>
                                    </div>
                                    <div className="home-match-timer-col">
                                      <small>{isCompleted ? 'Final' : timer.label}</small>
                                      <strong>{headerValue}</strong>
                                      {winnerText ? <span className="home-winner-chip">{winnerText}</span> : null}
                                    </div>
                                  </>
                                )}
                              </button>
                              {!isMobileLayout || mobileExpanded ? (
                                <>
                                  <div className="home-match-team-pills" aria-label="Match teams quick links">
                                    {match.red.map((team) => (
                                      <button
                                        key={`home-match-red-${match.match_key}-${team.team_key}`}
                                        type="button"
                                        className="home-team-pill red"
                                        onClick={() => openTeamCenter(team.team_key.toLowerCase(), section.event_key)}
                                        title={`View Team ${team.team_number}`}
                                      >
                                        <TeamAvatar teamKey={team.team_key.toLowerCase()} teamNumber={team.team_number} eventKey={section.event_key} size={18} />
                                        #{team.team_number}
                                      </button>
                                    ))}
                                    {match.blue.map((team) => (
                                      <button
                                        key={`home-match-blue-${match.match_key}-${team.team_key}`}
                                        type="button"
                                        className="home-team-pill blue"
                                        onClick={() => openTeamCenter(team.team_key.toLowerCase(), section.event_key)}
                                        title={`View Team ${team.team_number}`}
                                      >
                                        <TeamAvatar teamKey={team.team_key.toLowerCase()} teamNumber={team.team_number} eventKey={section.event_key} size={18} />
                                        #{team.team_number}
                                      </button>
                                    ))}
                                  </div>
                                  <div className="home-match-actions">
                                    {isMobileLayout ? (
                                      <button
                                        type="button"
                                        className="home-match-link-btn"
                                        onClick={() => collapseMobileMatch(section.event_key, match.match_key)}
                                        title="Collapse match details"
                                      >
                                        <ChevronDownIcon className="icon-inline" /> Collapse
                                      </button>
                                    ) : null}
                                    <button
                                      type="button"
                                      className="home-match-link-btn"
                                      onClick={() => {
                                        void copyMatchDeepLink(section.event_key, match.match_key);
                                      }}
                                      title="Copy shareable match link"
                                    >
                                      <LinkIcon className="icon-inline" /> Copy Link
                                    </button>
                                  </div>
                                </>
                              ) : null}
                            </article>
                          );
                        })}
                      </div>
                    )}

                    {!isMobileLayout ? (
                      <footer className="home-event-section-foot">
                        {section.matches.length} of {section.total_filtered_matches} matches shown
                      </footer>
                    ) : null}
                  </>
                )}
              </article>
            );
          })}
        </div>
      </main>

      {/* Desktop right rail */}
      {!isMobileLayout && (
      <aside className="home-fotmob-right">
        <section className={`home-fotmob-card ${compareBuilderCollapsed ? 'home-card-collapsed' : ''}`.trim()}>
          <header className="home-card-head">
            <div>
              {/* Named for the page it opens. It used to say "Alliance
                  Builder" and land on Compare, which the nav calls Compare and
                  which holds a *second*, hand-rolled builder — so one feature
                  had four names across the app. */}
              <h3>Alliance Advisor</h3>
              <small>Simulate pick-fit lineups</small>
            </div>
            {renderMobileCollapseButton('compare-builder', 'Alliance Advisor')}
          </header>
          {compareBuilderCollapsed ? (
            <p id={cardContentId('compare-builder')} className="home-card-collapsed-hint">
              Build and compare alliance lineups.
            </p>
          ) : (
            <>
              <p id={cardContentId('compare-builder')} className="home-fotmob-note">
                Build and compare 3-team lineups with compatibility scoring.
              </p>
              <Link
                className="home-fotmob-btn"
                to={selectedEventKey
                  ? `/compare/alliance-advisor?event=${selectedEventKey}`
                  : '/compare/alliance-advisor'}
              >
                Open
              </Link>
            </>
          )}
        </section>

        <section className={`home-fotmob-card ${liveStreamCollapsed ? 'home-card-collapsed' : ''}`.trim()}>
          <header className="home-card-head">
            <div>
              <h3>Live Stream</h3>
              <small>{selectedEventDisplay || 'No event selected'}</small>
            </div>
            {renderMobileCollapseButton('live-stream', 'Live Stream')}
          </header>
          {liveStreamCollapsed ? (
            <p id={cardContentId('live-stream')} className="home-card-collapsed-hint">
              {streamCardSummary}
            </p>
          ) : (
            <div id={cardContentId('live-stream')} className="home-card-panel-body">
              {loadingSelectedContext ? <p className="center-callout muted">Loading stream...</p> : null}
              {!loadingSelectedContext && !selectedEventStream ? (
                <p className="center-callout muted">No stream data available.</p>
              ) : null}
              {selectedEventStream && !selectedEventStream.available ? (
                <p className="center-callout muted">
                  {selectedEventStream.detail || 'No webcast available for this event.'}
                </p>
              ) : null}
              {selectedStreamWatchUrl ? (
                <a className="home-fotmob-btn" href={selectedStreamWatchUrl} target="_blank" rel="noreferrer" title="Open live stream in new tab">
                  Watch Live
                </a>
              ) : null}
              {selectedEventStream?.game_day_url ? (
                <a className="home-fotmob-btn subtle" href={selectedEventStream.game_day_url} target="_blank" rel="noreferrer">
                  Open TBA GameDay
                </a>
              ) : null}
              {selectedStreamEmbedUrl ? (
                <div className="home-fotmob-embed-wrap">
                  <iframe
                    title={`Live stream for ${selectedEventKey}`}
                    src={selectedStreamEmbedUrl}
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                    referrerPolicy="strict-origin-when-cross-origin"
                    allowFullScreen
                  />
                </div>
              ) : null}
            </div>
          )}
        </section>

        <section className={`home-fotmob-card ${leaderboardCollapsed ? 'home-card-collapsed' : ''}`.trim()}>
          <header className="home-card-head">
            <div>
              <h3>Leaderboard</h3>
              <small>{selectedEventDisplay}</small>
            </div>
            {renderMobileCollapseButton('leaderboard', 'Leaderboard')}
          </header>
          {leaderboardCollapsed ? (
            <p id={cardContentId('leaderboard')} className="home-card-collapsed-hint">
              {leaderboardSummary}
            </p>
          ) : (
            <div id={cardContentId('leaderboard')} className="home-card-panel-body">
              {loadingSelectedContext ? <p className="center-callout muted">Loading rankings...</p> : null}
              {!loadingSelectedContext && leaderboardRows.length === 0 ? (
                <p className="center-callout muted">No rankings available yet.</p>
              ) : null}
              {leaderboardRows.length > 0 ? (
                <div className="home-ranking-list">
                  {leaderboardRows.map((row) => (
                    <button
                      key={`home-rank-${row.team_key}`}
                      type="button"
                      className="home-ranking-row"
                      onClick={() => openTeamCenter(row.team_key)}
                      title={`View Team ${row.team_number} · Record: ${row.record}`}
                    >
                      <span className="rank">#{row.rank ?? '-'}</span>
                      <TeamAvatar teamKey={row.team_key} teamNumber={row.team_number} size={22} />
                      <span className="team">
                        #{row.team_number}{teamSuffix(row)}
                      </span>
                      <span className="record">{row.record}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          )}
        </section>
      </aside>
      )}

      {/* Mobile calendar drawer */}
      {isMobileLayout && (mobileCalendarOpen || mobileCalendarClosing) ? (
        <>
          <button
            type="button"
            className="home-drawer-backdrop"
            onClick={closeCalendarDrawer}
            aria-label="Close calendar drawer"
          />
          <aside className={`home-drawer home-calendar-drawer${mobileCalendarClosing ? ' home-drawer-closing' : ''}`} aria-label="Upcoming calendar drawer">
            <header className="home-drawer-header">
              <div className="home-drawer-title">
                <strong>Calendar</strong>
                <small>{formatMonthLabel(calendarMonth)}</small>
              </div>
              <button type="button" className="home-drawer-close" onClick={closeCalendarDrawer} aria-label="Close">
                <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 4l8 8M12 4l-8 8" /></svg>
              </button>
            </header>
            <section className="home-drawer-section home-calendar-drawer-section home-calendar-drawer-grid">
              <div className="home-calendar-modal-nav home-calendar-drawer-nav">
                <button
                  type="button"
                  className="center-btn ghost home-calendar-nav-btn"
                  onClick={() => setCalendarMonth((current) => shiftMonthToken(current, -1))}
                  aria-label="Previous month"
                >
                  <ChevronLeftIcon className="icon-inline" />
                </button>
                <div className="home-calendar-drawer-month">
                  <strong>{formatMonthLabel(calendarMonth)}</strong>
                  <small>{visibleCalendarEvents.length} event{visibleCalendarEvents.length === 1 ? '' : 's'}</small>
                </div>
                <button
                  type="button"
                  className="center-btn ghost home-calendar-nav-btn"
                  onClick={() => setCalendarMonth((current) => shiftMonthToken(current, 1))}
                  aria-label="Next month"
                >
                  <ChevronRightIcon className="icon-inline" />
                </button>
              </div>
              <div className="home-calendar-modal-weekdays">
                {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((label, idx) => (
                  <div key={`mobile-weekday-${idx}`} className="home-calendar-modal-weekday">{label}</div>
                ))}
              </div>
              <div className="home-calendar-modal-grid home-calendar-drawer-grid-inner">
                {modalGridDays.map((day) => {
                  const events = modalDayEvents.get(day.token) || [];
                  const isExpanded = expandedCalendarDays.has(day.token);
                  const visible = isExpanded ? events : events.slice(0, 1);
                  const overflow = events.length - visible.length;
                  return (
                    <div
                      key={`mobile-modal-day-${day.token}`}
                      className={`home-calendar-modal-day ${day.inMonth ? '' : 'off-month'} ${isExpanded ? 'expanded' : ''}`.trim()}
                    >
                      <div className="home-calendar-modal-day-num">{day.dayNum}</div>
                      <div className="home-calendar-modal-day-events">
                        {visible.map((event) => {
                          const key = normalizeEventKey(event.event_key);
                          return (
                            <button
                              key={`mobile-modal-chip-${day.token}-${event.event_key}`}
                              type="button"
                              className={`home-calendar-modal-chip ${selectedEventKey === key ? 'active' : ''}`.trim()}
                              onClick={() => {
                                const dayMs = fromDateTokenToLocalDayMs(day.token);
                                if (dayMs !== null) setSelectedDayMs(dayMs);
                                selectHomeEventKey(key, { manual: true });
                                setMobileCalendarOpen(false);
                              }}
                              title={event.name}
                            >
                              {event.name}
                            </button>
                          );
                        })}
                        {overflow > 0 ? (
                          <button
                            type="button"
                            className="home-calendar-modal-more"
                            onClick={() => toggleCalendarDayExpanded(day.token)}
                            aria-expanded={false}
                          >
                            +{overflow}
                          </button>
                        ) : null}
                        {isExpanded && events.length > 1 ? (
                          <button
                            type="button"
                            className="home-calendar-modal-more"
                            onClick={() => toggleCalendarDayExpanded(day.token)}
                            aria-expanded={true}
                          >
                            Less
                          </button>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
              {dateTbaCalendarEvents.length > 0 ? (
                <div className="home-calendar-drawer-tba">
                  <div className="home-calendar-tba-divider">
                    <span>Date TBA</span>
                    <span className="home-calendar-tba-count">{dateTbaCalendarEvents.length}</span>
                  </div>
                  <div className="home-fotmob-event-list home-calendar-event-list">
                    {dateTbaCalendarEvents.map((event) => {
                      const key = normalizeEventKey(event.event_key);
                      return (
                        <button
                          key={`mobile-home-calendar-tba-${event.event_key}`}
                          type="button"
                          className={`home-event-item home-event-item-tba ${selectedEventKey === key ? 'active' : ''}`.trim()}
                          onClick={() => {
                            selectHomeEventKey(key, { manual: true });
                            setMobileCalendarOpen(false);
                          }}
                        >
                          <strong>{event.name}</strong>
                          <small className="home-event-item-location">{eventLocationLabel(event)}</small>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </section>
          </aside>
        </>
      ) : null}

      {/* Mobile slide-out drawer */}
      {isMobileLayout && (mobileDrawerOpen || mobileDrawerClosing) ? (
        <>
          <button
            type="button"
            className="home-drawer-backdrop"
            onClick={closeDrawer}
            aria-label="Close drawer"
          />
          <aside className={`home-drawer${mobileDrawerClosing ? ' home-drawer-closing' : ''}`} aria-label="Event info drawer">
            <header className="home-drawer-header">
              <strong>Event Info</strong>
              <button type="button" className="home-drawer-close" onClick={closeDrawer} aria-label="Close">
                <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 4l8 8M12 4l-8 8" /></svg>
              </button>
            </header>

            {/* Leaderboard */}
            <section className="home-drawer-section">
              <h4><TrophyIcon className="icon-inline" /> Leaderboard</h4>
              <small>{selectedEventDisplay}</small>
              {loadingSelectedContext ? <p className="center-callout muted">Loading...</p> : null}
              {!loadingSelectedContext && leaderboardRows.length === 0 ? (
                <p className="center-callout muted">Rankings not published yet.</p>
              ) : null}
              {leaderboardRows.length > 0 ? (
                <div className="home-ranking-list">
                  {leaderboardRows.map((row) => (
                    <button
                      key={`drawer-rank-${row.team_key}`}
                      type="button"
                      className="home-ranking-row"
                      onClick={() => { openTeamCenter(row.team_key); setMobileDrawerOpen(false); }}
                      title={`View Team ${row.team_number}`}
                    >
                      <span className="rank">#{row.rank ?? '-'}</span>
                      <TeamAvatar teamKey={row.team_key} teamNumber={row.team_number} size={22} />
                      <span className="team">#{row.team_number}{teamSuffix(row)}</span>
                      <span className="record">{row.record}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </section>

            {/* Live Stream */}
            <section className="home-drawer-section">
              <h4><VideoIcon className="icon-inline" /> Live Stream</h4>
              {selectedStreamWatchUrl ? (
                <a className="home-fotmob-btn" href={selectedStreamWatchUrl} target="_blank" rel="noreferrer" title="Open live stream">
                  <VideoIcon className="icon-inline" /> Watch Live
                </a>
              ) : (
                <p className="center-callout muted">{streamCardSummary}</p>
              )}
              {selectedEventStream?.game_day_url ? (
                <a className="home-fotmob-btn subtle" href={selectedEventStream.game_day_url} target="_blank" rel="noreferrer">
                  TBA GameDay
                </a>
              ) : null}
            </section>

            {/* Quick links */}
            <section className="home-drawer-section">
              <h4><LinkIcon className="icon-inline" /> Quick Links</h4>
              <div className="home-drawer-links">
                <Link className="home-fotmob-btn subtle" to={selectedEventKey ? `/compare?event=${selectedEventKey}` : '/compare'} onClick={() => setMobileDrawerOpen(false)} title="Build and compare 3-team lineups">
                  <PuzzleIcon className="icon-inline" /> Alliance Builder
                </Link>
                <Link className="home-fotmob-btn subtle" to="/events" onClick={() => setMobileDrawerOpen(false)} title="Browse all FRC events">
                  <CalendarIcon className="icon-inline" /> All Events
                </Link>
                <Link className="home-fotmob-btn subtle" to={`/team-center${selectedEventKey ? `?event=${selectedEventKey}` : ''}`} onClick={() => setMobileDrawerOpen(false)} title="Detailed team scouting data">
                  <UsersIcon className="icon-inline" /> Team Center
                </Link>
              </div>
            </section>
          </aside>
        </>
      ) : null}

      {!isMobileLayout && calendarModalOpen ? (
        <div
          className="home-calendar-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Event calendar"
          onClick={(event) => {
            if (event.target === event.currentTarget) setCalendarModalOpen(false);
          }}
        >
          <div className="home-calendar-modal">
            <header className="home-calendar-modal-head">
              <div className="home-calendar-modal-title">
                <CalendarIcon className="icon-inline" />
                <h2>{formatMonthLabel(calendarMonth)}</h2>
                <small>{calendarEventRows.filter((row) => monthTokensForRange(row.startMs, row.endMs).includes(calendarMonth)).length} events</small>
              </div>
              <div className="home-calendar-modal-nav">
                <button
                  type="button"
                  className="center-btn ghost home-calendar-nav-btn"
                  onClick={() => setCalendarMonth((current) => shiftMonthToken(current, -1))}
                  aria-label="Previous month"
                >
                  <ChevronLeftIcon className="icon-inline" />
                </button>
                <button
                  type="button"
                  className="center-btn ghost home-calendar-nav-btn"
                  onClick={() => setCalendarMonth((current) => shiftMonthToken(current, 1))}
                  aria-label="Next month"
                >
                  <ChevronRightIcon className="icon-inline" />
                </button>
                <button
                  type="button"
                  className="home-calendar-modal-close"
                  onClick={() => setCalendarModalOpen(false)}
                  aria-label="Close calendar"
                >
                  ×
                </button>
              </div>
            </header>
            <div className="home-calendar-modal-weekdays">
              {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((label) => (
                <div key={`weekday-${label}`} className="home-calendar-modal-weekday">{label}</div>
              ))}
            </div>
            <div className="home-calendar-modal-grid">
              {modalGridDays.map((day) => {
                const events = modalDayEvents.get(day.token) || [];
                const isExpanded = expandedCalendarDays.has(day.token);
                const visible = isExpanded ? events : events.slice(0, 2);
                const overflow = events.length - visible.length;
                return (
                  <div
                    key={`modal-day-${day.token}`}
                    className={`home-calendar-modal-day ${day.inMonth ? '' : 'off-month'} ${isExpanded ? 'expanded' : ''}`.trim()}
                  >
                    <div className="home-calendar-modal-day-num">{day.dayNum}</div>
                    <div className="home-calendar-modal-day-events">
                      {visible.map((event) => {
                        const key = normalizeEventKey(event.event_key);
                        return (
                          <button
                            key={`modal-chip-${day.token}-${event.event_key}`}
                            type="button"
                            className={`home-calendar-modal-chip ${selectedEventKey === key ? 'active' : ''}`.trim()}
                            onClick={() => {
                              const dayMs = fromDateTokenToLocalDayMs(day.token);
                              if (dayMs !== null) setSelectedDayMs(dayMs);
                              selectHomeEventKey(key, { manual: true });
                              setCalendarModalOpen(false);
                            }}
                            title={event.name}
                          >
                            {event.name}
                          </button>
                        );
                      })}
                      {overflow > 0 ? (
                        <button
                          type="button"
                          className="home-calendar-modal-more"
                          onClick={() => toggleCalendarDayExpanded(day.token)}
                          aria-expanded={false}
                        >
                          +{overflow} more
                        </button>
                      ) : null}
                      {isExpanded && events.length > 2 ? (
                        <button
                          type="button"
                          className="home-calendar-modal-more"
                          onClick={() => toggleCalendarDayExpanded(day.token)}
                          aria-expanded={true}
                        >
                          Show less
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
