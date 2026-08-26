import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  getEventLiveStream,
  getEventSchedule,
  getEventScheduleWithSynergy,
  getEventTeamLiveForm,
  getMatchPhases,
  getMatchTracks,
} from '../api';
import type {
  EventScheduleItem,
  EventTeamLiveFormEntry,
  EventTeamLiveFormResponse,
  MatchPhasesResponse,
  MatchTracksResponse,
  ScheduleWithSynergyMatch,
} from '../api';
import { VideoReplayer } from '../components/cv/VideoReplayer';
import { EventPicker } from '../components/EventPicker';
import { SkeletonBlock } from '../components/ui/SkeletonBlock';
import { EmptyState } from '../components/ui/EmptyState';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { PageViewBar } from '../components/PageViewBar';
import { MATCH_HUB_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useLiveRefreshSetting } from '../hooks/useLiveRefreshSetting';
import { MOBILE_LAYOUT_BREAKPOINT, useMobileLayout } from '../hooks/useMobileLayout';
import { Table, type TableColumn } from '../components/ui/primitives';
import { usePageClock } from '../hooks/usePageClock';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling, type SingleFlightPollReason } from '../hooks/useSingleFlightPolling';
import {
  EyeIcon, PieChartIcon, UsersIcon, ClockIcon,
  ChevronDownIcon, VideoIcon, TrophyIcon,
  CalendarIcon, SignalIcon, ScoreboardIcon, RobotIcon, GamepadIcon,
  FlagIcon, TargetIcon, StarIcon, HandshakeIcon, LinkIcon,
} from '../components/ui/Icons';
import {
  buildMatchCenterPath,
  copyTextToClipboard,
  eventKeyFromMatchKey,
  fmtDateShort,
  fmtUnix,
  liveTimerLabel,
  metric,
  normalizeEventKey,
  normalizeMatchKey,
  relativeFromTimestamp,
  titleizeKey,
} from './centerUtils';
import { readStoredCenterContext, writeCenterContext } from '../layout/centerContext';

const MATCH_CENTER_VIEW_PREFS_STORAGE = 'scouting_match_center_view_prefs_v1';
const MATCH_CENTER_RECENT_STORAGE = 'scouting_match_center_recent_matches_v1';

const MATCH_TABS = ['overview', 'breakdown', 'teams'] as const;
type MatchTab = (typeof MATCH_TABS)[number];

const MATCH_TAB_ICONS: Record<MatchTab, React.ReactNode> = {
  overview: <EyeIcon className="icon-inline" />,
  breakdown: <PieChartIcon className="icon-inline" />,
  teams: <UsersIcon className="icon-inline" />,
};

type MatchTeamRow = {
  team_key: string;
  team_number: number;
  nickname: string | null;
  alliance: 'red' | 'blue';
  station: string | null;
};

type MatchCenterEventViewPrefs = {
  matchFilter?: string;
  summarySortMode?: 'time' | 'status';
  autoJumpLive?: boolean;
};

type MatchCenterRecentMatchesMap = Record<string, string[]>;

function readMatchCenterEventViewPrefs(): Record<string, MatchCenterEventViewPrefs> {
  try {
    const raw = window.localStorage.getItem(MATCH_CENTER_VIEW_PREFS_STORAGE);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return parsed as Record<string, MatchCenterEventViewPrefs>;
  } catch {
    return {};
  }
}

function writeMatchCenterEventViewPrefs(next: Record<string, MatchCenterEventViewPrefs>) {
  window.localStorage.setItem(MATCH_CENTER_VIEW_PREFS_STORAGE, JSON.stringify(next));
}

function readMatchCenterRecentMatches(): MatchCenterRecentMatchesMap {
  try {
    const raw = window.localStorage.getItem(MATCH_CENTER_RECENT_STORAGE);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return parsed as MatchCenterRecentMatchesMap;
  } catch {
    return {};
  }
}

function writeMatchCenterRecentMatches(next: MatchCenterRecentMatchesMap) {
  window.localStorage.setItem(MATCH_CENTER_RECENT_STORAGE, JSON.stringify(next));
}

function matchHasScores(match: EventScheduleItem | null): boolean {
  return (
    typeof match?.red_score === 'number' &&
    typeof match?.blue_score === 'number' &&
    Number.isFinite(match.red_score) &&
    Number.isFinite(match.blue_score) &&
    match.red_score >= 0 &&
    match.blue_score >= 0
  );
}

function inferMatchCompleted(match: EventScheduleItem | null, nowMs: number): boolean {
  if (!match) return false;
  const timer = liveTimerLabel(match.scheduled_time ?? null, nowMs);
  const winner = match.winner_alliance || null;
  return (
    Boolean(match.is_completed) ||
    winner === 'red' ||
    winner === 'blue' ||
    winner === 'tie' ||
    (matchHasScores(match) && timer.state === 'ended')
  );
}

function isMatchTab(value: string | null): value is MatchTab {
  return value === 'overview' || value === 'breakdown' || value === 'teams';
}

function teamFormStrip(entry: EventTeamLiveFormEntry | null) {
  const form = entry?.recent_form || [];
  if (form.length === 0) {
    return <span className="center-form-empty">No recent form</span>;
  }
  return (
    <span className="center-form-strip" aria-label="Last five matches">
      {form.map((result, idx) => (
        <span
          key={`${entry?.team_key || 'team'}-form-${idx}`}
          className={`center-form-pill ${
            result === 'W' ? 'win' : result === 'L' ? 'loss' : 'tie'
          }`.trim()}
          title={`Result ${result}`}
        >
          {result}
        </span>
      ))}
    </span>
  );
}

export function MatchCenterPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobileLayout = useMobileLayout();
  const pageVisible = usePageVisibility();
  const nowMs = usePageClock(pageVisible);
  const liveRefreshSec = useLiveRefreshSetting();
  const storedCenterContext = readStoredCenterContext();

  const defaultEventKey = normalizeEventKey(
    searchParams.get('event') || storedCenterContext.eventKey,
  );
  const defaultMatchKey = normalizeMatchKey(
    searchParams.get('match') || storedCenterContext.matchKey,
    defaultEventKey,
  );
  const tabParam = searchParams.get('tab');
  const defaultTab: MatchTab = isMatchTab(tabParam) ? tabParam : 'overview';

  const [eventInput, setEventInput] = useState(defaultEventKey);
  const [selectedEventKey, setSelectedEventKey] = useState(defaultEventKey);
  const [selectedMatchKey, setSelectedMatchKey] = useState(defaultMatchKey);
  const [activeTab, setActiveTab] = useState<MatchTab>(defaultTab);

  const [scheduleRows, setScheduleRows] = useState<EventScheduleItem[]>([]);
  const [eventName, setEventName] = useState<string | null>(null);
  const [teamLiveForm, setTeamLiveForm] = useState<EventTeamLiveFormResponse | null>(null);
  const [liveStream, setLiveStream] = useState<{
    watch_url: string | null;
    embed_url: string | null;
    game_day_url: string;
    available: boolean;
    detail: string | null;
  } | null>(null);
  const [synergyMatches, setSynergyMatches] = useState<Record<string, ScheduleWithSynergyMatch>>({});
  const [matchPhases, setMatchPhases] = useState<MatchPhasesResponse | null>(null);

  const [matchTracks, setMatchTracks] = useState<MatchTracksResponse | null>(null);
  const [loadingTracks, setLoadingTracks] = useState(false);

  const [loadingEventData, setLoadingEventData] = useState(false);
  const [loadingPhases, setLoadingPhases] = useState(false);
  const [statusText, setStatusText] = useState('Select an event and match.');
  const [eventError, setEventError] = useState('');
  const [phaseError, setPhaseError] = useState('');
  const [matchFilter, setMatchFilter] = useState('');
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [eventHasLiveMatch, setEventHasLiveMatch] = useState(false);
  const [visibleMatchCount, setVisibleMatchCount] = useState(70);
  const [mobileFinderOpen, setMobileFinderOpen] = useState(() => !defaultMatchKey);
  const [pairSortMode, setPairSortMode] = useState<'points' | 'confidence'>('points');
  const [summarySortMode, setSummarySortMode] = useState<'time' | 'status'>('time');
  const [autoJumpLive, setAutoJumpLive] = useState(false);
  const [showFinderAdvanced, setShowFinderAdvanced] = useState(false);
  const [recentMatchesByEvent, setRecentMatchesByEvent] = useState<MatchCenterRecentMatchesMap>(() =>
    readMatchCenterRecentMatches(),
  );
  const lastEventContextRef = useRef('');
  const lastAutoJumpMatchKeyRef = useRef('');

  useEffect(() => {
    if (!isMobileLayout) setMobileFinderOpen(false);
  }, [isMobileLayout]);

  // Keep internal selection state in sync with URL query params for deep links.
  // This runs only when URL params change to avoid state<->URL ping-pong loops.
  useEffect(() => {
    const urlEventKey = normalizeEventKey(searchParams.get('event'));
    const storedEventKey = readStoredCenterContext().eventKey;
    const provisionalEventKey = urlEventKey || storedEventKey;
    const urlMatchKey = normalizeMatchKey(searchParams.get('match'), provisionalEventKey);
    const eventFromMatchKey = eventKeyFromMatchKey(urlMatchKey);
    const resolvedEventKey = eventFromMatchKey || urlEventKey;
    const resolvedMatchKey = normalizeMatchKey(urlMatchKey, resolvedEventKey || provisionalEventKey);
    const urlTab = searchParams.get('tab');

    if (resolvedEventKey) {
      setEventInput((prev) => (prev === resolvedEventKey ? prev : resolvedEventKey));
      setSelectedEventKey((prev) => (prev === resolvedEventKey ? prev : resolvedEventKey));
    }
    if (resolvedMatchKey) {
      setSelectedMatchKey((prev) => (prev === resolvedMatchKey ? prev : resolvedMatchKey));
    }
    if (isMatchTab(urlTab)) {
      setActiveTab((prev) => (prev === urlTab ? prev : urlTab));
    }
  }, [searchParams]);

  useEffect(() => {
    setVisibleMatchCount(70);
  }, [selectedEventKey, matchFilter]);

  useEffect(() => {
    if (!selectedEventKey) return;
    const prefs = readMatchCenterEventViewPrefs();
    const eventPrefs = prefs[selectedEventKey];
    setMatchFilter(eventPrefs?.matchFilter || '');
    setSummarySortMode(eventPrefs?.summarySortMode || 'time');
    setAutoJumpLive(Boolean(eventPrefs?.autoJumpLive));
    lastAutoJumpMatchKeyRef.current = '';
  }, [selectedEventKey]);

  useEffect(() => {
    setShowFinderAdvanced(false);
  }, [selectedEventKey]);

  useEffect(() => {
    setPairSortMode('points');
  }, [selectedMatchKey]);

  useEffect(() => {
    if (!selectedEventKey) return;
    const prefs = readMatchCenterEventViewPrefs();
    const previous = prefs[selectedEventKey] || {};
    prefs[selectedEventKey] = {
      ...previous,
      matchFilter,
      summarySortMode,
      autoJumpLive,
    };
    writeMatchCenterEventViewPrefs(prefs);
  }, [autoJumpLive, matchFilter, selectedEventKey, summarySortMode]);

  useEffect(() => {
    writeMatchCenterRecentMatches(recentMatchesByEvent);
  }, [recentMatchesByEvent]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (selectedEventKey) next.set('event', selectedEventKey);
    if (selectedMatchKey) next.set('match', selectedMatchKey);
    next.set('tab', activeTab);

    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
    writeCenterContext({
      eventKey: selectedEventKey,
      matchKey: selectedMatchKey,
      sourcePath: '/match-center',
    });
  }, [activeTab, searchParams, selectedEventKey, selectedMatchKey, setSearchParams]);

  useEffect(() => {
    if (selectedEventKey) return;
    lastEventContextRef.current = '';
    setScheduleRows([]);
    setEventName(null);
    setTeamLiveForm(null);
    setLiveStream(null);
    setSynergyMatches({});
    setEventHasLiveMatch(false);
    setEventError('');
  }, [selectedEventKey]);

  const loadEventData = useCallback(async (reason: SingleFlightPollReason): Promise<boolean> => {
    if (!selectedEventKey) return true;

    const contextChanged = lastEventContextRef.current !== selectedEventKey;
    if (contextChanged) {
      setScheduleRows([]);
      setEventName(null);
      setTeamLiveForm(null);
      setLiveStream(null);
      setSynergyMatches({});
      setEventHasLiveMatch(false);
    }
    lastEventContextRef.current = selectedEventKey;

    setLoadingEventData(true);
    setEventError('');
    setStatusText(
      contextChanged || reason === 'initial'
        ? `Loading ${selectedEventKey}...`
        : `Refreshing ${selectedEventKey}...`,
    );

    try {
      const shouldRefreshStatic = contextChanged || reason !== 'poll';
      const shouldBypassLiveCache = reason === 'poll' && eventHasLiveMatch;
      const liveRequestOptions = shouldBypassLiveCache
        ? { bypassCache: true, cacheTtlMs: 0, staleWhileRevalidateMs: 0 }
        : undefined;
      const [scheduleResult, liveFormResult, streamResult, synergyResult] = await Promise.allSettled([
        getEventSchedule(selectedEventKey, false, undefined, liveRequestOptions),
        getEventTeamLiveForm(selectedEventKey, { form_window: 5, live_window_sec: 180 }, liveRequestOptions),
        shouldRefreshStatic ? getEventLiveStream(selectedEventKey) : Promise.resolve(null),
        shouldRefreshStatic
          ? getEventScheduleWithSynergy(selectedEventKey, {
              refresh: false,
              include_pair_breakdown: true,
            })
          : Promise.resolve(null),
      ]);

      const errors: string[] = [];

      if (scheduleResult.status === 'fulfilled') {
        const rows = scheduleResult.value.matches || [];
        setScheduleRows(rows);
        const now = Date.now();
        const hasLive = rows.some(
          (row) => !inferMatchCompleted(row, now) && liveTimerLabel(row.scheduled_time ?? null, now).state === 'live',
        );
        setEventHasLiveMatch(hasLive);
        setEventName(scheduleResult.value.event_name || null);
      } else {
        if (contextChanged) {
          setScheduleRows([]);
          setEventHasLiveMatch(false);
        }
        errors.push(`Schedule: ${scheduleResult.reason instanceof Error ? scheduleResult.reason.message : 'failed'}`);
      }

      if (liveFormResult.status === 'fulfilled') {
        setTeamLiveForm(liveFormResult.value);
      } else {
        if (contextChanged) setTeamLiveForm(null);
        errors.push(`Live form: ${liveFormResult.reason instanceof Error ? liveFormResult.reason.message : 'failed'}`);
      }

      if (shouldRefreshStatic && streamResult.status === 'fulfilled' && streamResult.value) {
        const preferred = streamResult.value.preferred_stream;
        const fallbackWatch = streamResult.value.streams.find((item) => Boolean(item.watch_url));
        setLiveStream({
          watch_url: preferred?.watch_url || fallbackWatch?.watch_url || streamResult.value.game_day_url || null,
          embed_url: preferred?.embed_url || null,
          game_day_url: streamResult.value.game_day_url,
          available: streamResult.value.available,
          detail: streamResult.value.detail,
        });
      } else if (shouldRefreshStatic && streamResult.status === 'rejected') {
        if (contextChanged) setLiveStream(null);
        errors.push(`Live stream: ${streamResult.reason instanceof Error ? streamResult.reason.message : 'failed'}`);
      }

      if (shouldRefreshStatic && synergyResult.status === 'fulfilled' && synergyResult.value) {
        const lookup: Record<string, ScheduleWithSynergyMatch> = {};
        for (const row of synergyResult.value.matches || []) {
          lookup[row.match_key.toLowerCase()] = row;
        }
        setSynergyMatches(lookup);
      } else if (shouldRefreshStatic && synergyResult.status === 'rejected') {
        if (contextChanged) setSynergyMatches({});
        errors.push(`Synergy: ${synergyResult.reason instanceof Error ? synergyResult.reason.message : 'failed'}`);
      }

      setEventError(errors.join(' | '));
      setLastUpdatedAt(Date.now());
      setStatusText(
        errors.length > 0
          ? `Partial data for ${selectedEventKey}.`
          : `${selectedEventKey} loaded.`,
      );
      return errors.length === 0;
    } finally {
      setLoadingEventData(false);
    }
  }, [eventHasLiveMatch, selectedEventKey]);

  const refreshSec = eventHasLiveMatch ? 5 : Math.max(10, liveRefreshSec);
  const { triggerNow } = useSingleFlightPolling({
    enabled: Boolean(selectedEventKey),
    visible: pageVisible,
    intervalMs: refreshSec * 1000,
    run: loadEventData,
    backoffMultiplier: 1.6,
    minBackoffMs: refreshSec * 1000,
    maxBackoffMs: 60000,
  });

  useEffect(() => {
    if (scheduleRows.length === 0) {
      // Preserve URL-selected match while schedule is loading so deep links
      // don't get reset to the first row (QM1) before data arrives.
      if (!selectedEventKey && selectedMatchKey) setSelectedMatchKey('');
      return;
    }

    const normalizedSelectedMatchKey = normalizeMatchKey(selectedMatchKey, selectedEventKey);
    const hasSelected = scheduleRows.some(
      (row) => normalizeMatchKey(row.match_key, selectedEventKey) === normalizedSelectedMatchKey,
    );
    if (!hasSelected && normalizedSelectedMatchKey) {
      const selectedSuffix = normalizedSelectedMatchKey.includes('_')
        ? normalizedSelectedMatchKey.split('_').slice(1).join('_')
        : normalizedSelectedMatchKey;
      const suffixMatch = scheduleRows.find((row) => {
        const normalizedRowMatchKey = normalizeMatchKey(row.match_key, selectedEventKey);
        const rowSuffix = normalizedRowMatchKey.includes('_')
          ? normalizedRowMatchKey.split('_').slice(1).join('_')
          : normalizedRowMatchKey;
        return rowSuffix === selectedSuffix;
      });
      if (suffixMatch) {
        setSelectedMatchKey(normalizeMatchKey(suffixMatch.match_key, selectedEventKey));
        return;
      }
    }
    if (!selectedMatchKey || !hasSelected) {
      setSelectedMatchKey(normalizeMatchKey(scheduleRows[0].match_key, selectedEventKey));
    }
  }, [scheduleRows, selectedEventKey, selectedMatchKey]);

  useEffect(() => {
    if (!selectedMatchKey) {
      setMatchPhases(null);
      setPhaseError('');
      return;
    }

    let cancelled = false;

    async function run() {
      setLoadingPhases(true);
      setPhaseError('');
      try {
        const payload = await getMatchPhases(selectedMatchKey);
        if (cancelled) return;
        setMatchPhases(payload);
      } catch (error) {
        if (cancelled) return;
        setMatchPhases(null);
        setPhaseError((error as Error).message || 'Unable to load phase windows.');
      } finally {
        if (!cancelled) setLoadingPhases(false);
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, [selectedMatchKey]);

  /* ── lazy-load robot tracks for the selected match ──────────── */
  useEffect(() => {
    if (!selectedMatchKey) {
      setMatchTracks(null);
      return;
    }
    let cancelled = false;
    setLoadingTracks(true);
    getMatchTracks(selectedMatchKey)
      .then((res) => { if (!cancelled) setMatchTracks(res); })
      .catch(() => { if (!cancelled) setMatchTracks(null); })
      .finally(() => { if (!cancelled) setLoadingTracks(false); });
    return () => { cancelled = true; };
  }, [selectedMatchKey]);

  const filteredMatches = useMemo(() => {
    const query = matchFilter.trim().toLowerCase();
    if (!query) return scheduleRows;

    return scheduleRows.filter((match) => {
      const teamTokens = [...match.red, ...match.blue].flatMap((team) => [
        team.team_key.toLowerCase(),
        String(team.team_number),
        (team.nickname || '').toLowerCase(),
      ]);
      return (
        match.match_key.toLowerCase().includes(query) ||
        match.display_name.toLowerCase().includes(query) ||
        teamTokens.some((token) => token.includes(query))
      );
    });
  }, [matchFilter, scheduleRows]);

  const sortedFilteredMatches = useMemo(() => {
    const rows = [...filteredMatches];
    if (summarySortMode === 'status') {
      rows.sort((a, b) => {
        const aTimer = liveTimerLabel(a.scheduled_time, nowMs);
        const bTimer = liveTimerLabel(b.scheduled_time, nowMs);
        const aCompleted = inferMatchCompleted(a, nowMs);
        const bCompleted = inferMatchCompleted(b, nowMs);
        const score = (completed: boolean, state: string) => {
          if (completed) return 0;
          if (state === 'live') return 1;
          return 2;
        };
        const delta = score(aCompleted, aTimer.state) - score(bCompleted, bTimer.state);
        if (delta !== 0) return delta;
        const aTime = a.scheduled_time || Number.MAX_SAFE_INTEGER;
        const bTime = b.scheduled_time || Number.MAX_SAFE_INTEGER;
        if (aTime !== bTime) return aTime - bTime;
        return a.match_key.localeCompare(b.match_key);
      });
      return rows;
    }
    rows.sort((a, b) => {
      const aTime = a.scheduled_time || Number.MAX_SAFE_INTEGER;
      const bTime = b.scheduled_time || Number.MAX_SAFE_INTEGER;
      if (aTime !== bTime) return aTime - bTime;
      return a.match_key.localeCompare(b.match_key);
    });
    return rows;
  }, [filteredMatches, nowMs, summarySortMode]);

  const visibleFilteredMatches = useMemo(
    () => sortedFilteredMatches.slice(0, Math.max(1, visibleMatchCount)),
    [sortedFilteredMatches, visibleMatchCount],
  );

  const liveStatusByTeam = useMemo(() => teamLiveForm?.team_statuses || {}, [teamLiveForm]);
  const normalizedSelectedMatchKey = useMemo(
    () => normalizeMatchKey(selectedMatchKey, selectedEventKey),
    [selectedEventKey, selectedMatchKey],
  );

  useEffect(() => {
    if (!selectedEventKey || !normalizedSelectedMatchKey) return;
    setRecentMatchesByEvent((previous) => {
      const current = previous[selectedEventKey] || [];
      const deduped = [normalizedSelectedMatchKey, ...current.filter((item) => item !== normalizedSelectedMatchKey)].slice(0, 12);
      if (deduped.join(',') === current.join(',')) return previous;
      return {
        ...previous,
        [selectedEventKey]: deduped,
      };
    });
  }, [normalizedSelectedMatchKey, selectedEventKey]);

  const selectedMatch = useMemo(() => {
    return (
      scheduleRows.find(
        (row) => normalizeMatchKey(row.match_key, selectedEventKey) === normalizedSelectedMatchKey,
      ) || null
    );
  }, [normalizedSelectedMatchKey, scheduleRows, selectedEventKey]);

  const selectedMatchTimer = useMemo(
    () => liveTimerLabel(selectedMatch?.scheduled_time ?? null, nowMs),
    [nowMs, selectedMatch?.scheduled_time],
  );
  const selectedMatchProgress = useMemo(() => {
    if (!selectedMatch?.scheduled_time) return null;
    const startSec = selectedMatch.scheduled_time;
    const elapsedSec = Math.max(0, Math.floor(nowMs / 1000) - startSec);
    return Math.max(0, Math.min(100, (elapsedSec / 150) * 100));
  }, [nowMs, selectedMatch?.scheduled_time]);
  const selectedWinner = selectedMatch?.winner_alliance || null;
  const selectedHasScores = matchHasScores(selectedMatch);
  const selectedCompleted = inferMatchCompleted(selectedMatch, nowMs);
  const selectedIsLive = selectedMatchTimer.state === 'live' && !selectedCompleted;
  const resolvedWinner = useMemo<'red' | 'blue' | 'tie' | null>(() => {
    if (selectedWinner === 'red' || selectedWinner === 'blue' || selectedWinner === 'tie') return selectedWinner;
    if (!selectedCompleted || !selectedHasScores) return null;
    const red = Number(selectedMatch?.red_score ?? 0);
    const blue = Number(selectedMatch?.blue_score ?? 0);
    if (red > blue) return 'red';
    if (blue > red) return 'blue';
    return 'tie';
  }, [selectedWinner, selectedCompleted, selectedHasScores, selectedMatch?.red_score, selectedMatch?.blue_score]);

  const selectedSynergy = useMemo(() => {
    if (!normalizedSelectedMatchKey) return null;
    return synergyMatches[normalizedSelectedMatchKey] || null;
  }, [normalizedSelectedMatchKey, synergyMatches]);
  const selectedRedSynergy = selectedSynergy?.red?.synergy ?? null;
  const selectedBlueSynergy = selectedSynergy?.blue?.synergy ?? null;
  const selectedPairBreakdown = useMemo(() => {
    const redPairs = (selectedRedSynergy?.pair_breakdown || []).map((pair) => ({ ...pair, alliance: 'Red' as const }));
    const bluePairs = (selectedBlueSynergy?.pair_breakdown || []).map((pair) => ({ ...pair, alliance: 'Blue' as const }));
    const rows = [...redPairs, ...bluePairs];
    if (pairSortMode === 'confidence') {
      rows.sort((a, b) => b.confidence - a.confidence);
      return rows;
    }
    rows.sort((a, b) => b.synergy_points - a.synergy_points);
    return rows;
  }, [pairSortMode, selectedBlueSynergy?.pair_breakdown, selectedRedSynergy?.pair_breakdown]);

  /* The pair board's alliance is a fact about the row, so it stays a real
     alliance pill rather than the plain "Red"/"Blue" text the desktop table
     used while the phone list showed the pill. */
  const pairBreakdownColumns: TableColumn<(typeof selectedPairBreakdown)[number]>[] = [
    {
      key: 'pair',
      label: 'Pair',
      render: (pair) => `${pair.team_key_a} + ${pair.team_key_b}`,
    },
    {
      key: 'alliance',
      label: 'Alliance',
      render: (pair) => (
        <span className={`center-alliance-pill ${pair.alliance.toLowerCase()}`}>{pair.alliance}</span>
      ),
    },
    { key: 'points', label: 'Points', numeric: true, render: (pair) => metric(pair.synergy_points, 2) },
    { key: 'confidence', label: 'Confidence', numeric: true, render: (pair) => metric(pair.confidence, 2) },
    { key: 'source', label: 'Source', render: (pair) => titleizeKey(pair.source) },
  ];

  const nextLiveMatch = useMemo(() => {
    const rows = [...scheduleRows];
    rows.sort((a, b) => {
      const aTime = a.scheduled_time || Number.MAX_SAFE_INTEGER;
      const bTime = b.scheduled_time || Number.MAX_SAFE_INTEGER;
      if (aTime !== bTime) return aTime - bTime;
      return a.match_key.localeCompare(b.match_key);
    });
    const nowSec = Math.floor(nowMs / 1000);
    const scheduleSec = (match: EventScheduleItem): number | null => {
      if (typeof match.scheduled_time !== 'number' || !Number.isFinite(match.scheduled_time)) return null;
      return Math.floor(match.scheduled_time);
    };

    const strictLive = rows.find(
      (row) => !inferMatchCompleted(row, nowMs) && liveTimerLabel(row.scheduled_time ?? null, nowMs).state === 'live',
    );
    if (strictLive) return strictLive;

    // Fallback for delayed events where schedule time has passed but official score publish is late.
    const delayedLiveLike = rows
      .filter((row) => !inferMatchCompleted(row, nowMs))
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
    if (delayedLiveLike) return delayedLiveLike;

    const upcoming = rows.find((row) => {
      if (inferMatchCompleted(row, nowMs)) return false;
      const scheduled = scheduleSec(row);
      return scheduled !== null && scheduled > nowSec;
    });
    if (upcoming) return upcoming;

    return rows.find((row) => !inferMatchCompleted(row, nowMs)) || null;
  }, [nowMs, scheduleRows]);

  const recentMatchesForEvent = useMemo(() => {
    if (!selectedEventKey) return [];
    const recent = recentMatchesByEvent[selectedEventKey] || [];
    return recent
      .filter((key) => key !== normalizedSelectedMatchKey)
      .slice(0, 3)
      .map((matchKey) => {
        const scheduleRow =
          scheduleRows.find((row) => normalizeMatchKey(row.match_key, selectedEventKey) === matchKey) || null;
        const label = scheduleRow?.display_name || matchKey.split('_').slice(1).join('_').toUpperCase() || matchKey.toUpperCase();
        return { matchKey, label };
      });
  }, [normalizedSelectedMatchKey, recentMatchesByEvent, scheduleRows, selectedEventKey]);

  useEffect(() => {
    if (!autoJumpLive || !nextLiveMatch || !selectedEventKey) return;
    const nextLiveKey = normalizeMatchKey(nextLiveMatch.match_key, selectedEventKey);
    if (!nextLiveKey || nextLiveKey === normalizedSelectedMatchKey) return;
    if (lastAutoJumpMatchKeyRef.current === nextLiveKey) return;
    lastAutoJumpMatchKeyRef.current = nextLiveKey;
    setSelectedMatchKey(nextLiveKey);
  }, [autoJumpLive, nextLiveMatch, normalizedSelectedMatchKey, selectedEventKey]);

  const synergyAvailable =
    Boolean(selectedRedSynergy?.available) || Boolean(selectedBlueSynergy?.available) || selectedPairBreakdown.length > 0;

  const selectedTeams = useMemo(() => {
    if (!selectedMatch) return [];
    const redTeams: MatchTeamRow[] = selectedMatch.red.map((team) => ({
      ...team,
      alliance: 'red',
    }));
    const blueTeams: MatchTeamRow[] = selectedMatch.blue.map((team) => ({
      ...team,
      alliance: 'blue',
    }));
    return [...redTeams, ...blueTeams];
  }, [selectedMatch]);

  const selectedRedLineupLabel = useMemo(
    () => (selectedMatch ? selectedMatch.red.map((team) => `#${team.team_number}`).join(' · ') || 'TBD' : 'TBD'),
    [selectedMatch],
  );
  const selectedBlueLineupLabel = useMemo(
    () => (selectedMatch ? selectedMatch.blue.map((team) => `#${team.team_number}`).join(' · ') || 'TBD' : 'TBD'),
    [selectedMatch],
  );
  const effectiveRefreshSec = eventHasLiveMatch ? 5 : Math.max(10, liveRefreshSec);

  function openTeamCenter(teamKey: string) {
    if (!selectedEventKey) return;
    navigate(`/team-center?event=${selectedEventKey}&team=${teamKey.toLowerCase()}`);
  }

  async function copyMatchDeepLink(matchKey: string, eventKey: string = selectedEventKey) {
    const path = buildMatchCenterPath(eventKey, matchKey);
    const deepLink = new URL(path, window.location.origin).toString();
    const copied = await copyTextToClipboard(deepLink);
    if (copied) {
      setStatusText(`Copied deep link for ${normalizeMatchKey(matchKey, eventKey).toUpperCase()}.`);
      return;
    }
    setEventError('Unable to copy match link on this device/browser.');
  }

  function openNextLiveMatch() {
    if (!nextLiveMatch || !selectedEventKey) return;
    setSelectedMatchKey(normalizeMatchKey(nextLiveMatch.match_key, selectedEventKey));
    if (isMobileLayout) setMobileFinderOpen(false);
  }

  function renderLiveStreamCard(title = 'Live Stream', subtitle = 'Event stream embed and links.') {
    return (
      <SurfaceCard title={title} subtitle={subtitle}>
        {!liveStream ? <p className="center-callout muted">Stream unavailable.</p> : null}
        {liveStream && !liveStream.available ? (
          <p className="center-callout muted">{liveStream.detail || 'No webcast published.'}</p>
        ) : null}
        {liveStream?.watch_url ? (
          <div className="center-actions-row">
            <a href={liveStream.watch_url} target="_blank" rel="noreferrer" className="center-btn" title="Watch live stream">
              <VideoIcon className="icon-inline" /> Watch
            </a>
            {liveStream.game_day_url ? (
              <a href={liveStream.game_day_url} target="_blank" rel="noreferrer" className="center-btn ghost" title="Open TBA GameDay">
                <CalendarIcon className="icon-inline" /> TBA GameDay
              </a>
            ) : null}
          </div>
        ) : null}
        {liveStream?.embed_url ? (
          <div className="center-stream-embed-wrap">
            <iframe
              title={`Live stream for ${selectedEventKey}`}
              src={liveStream.embed_url}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              referrerPolicy="strict-origin-when-cross-origin"
              allowFullScreen
            />
          </div>
        ) : (
          <p className="center-callout muted">No stream embed available.</p>
        )}
      </SurfaceCard>
    );
  }

  const eventTitle = eventName || selectedEventKey || 'Match Center';

  return (
    <>
    <PageViewBar items={MATCH_HUB_VIEWS} />
    <div className="match-center-page">
    <div className={`center-layout center-layout-match mobile-finder-layout scouting-layout-grid ${isMobileLayout && mobileFinderOpen ? 'mobile-finder-open' : ''}`.trim()}>
      {isMobileLayout ? (
        <SegmentedTabs
          className="mobile-view-toggle"
          itemClassName="mobile-view-toggle-btn"
          ariaLabel="Match mobile view switch"
          value={mobileFinderOpen ? 'finder' : 'center'}
          onChange={(next) => setMobileFinderOpen(next === 'finder')}
          items={[
            { value: 'finder', label: 'Match Finder' },
            { value: 'center', label: 'Match Center', disabled: !selectedMatch },
          ]}
        />
      ) : null}
      <aside className="center-sidebar">
        <SurfaceCard title="Match Finder" compactable>
          <EventPicker
            value={selectedEventKey}
            onSelect={(key) => {
              const nextEventKey = normalizeEventKey(key);
              setEventInput(nextEventKey);
              setSelectedEventKey(nextEventKey);
              // Force re-fetch even if same key
              setTimeout(() => triggerNow('manual'), 0);
              if (isMobileLayout) setMobileFinderOpen(false);
            }}
            inputValue={eventInput}
            onInputChange={setEventInput}
            onSubmit={() => {
              const next = normalizeEventKey(eventInput);
              if (!next) return;
              setSelectedEventKey(next);
              setTimeout(() => triggerNow('manual'), 0);
              if (isMobileLayout) setMobileFinderOpen(false);
            }}
          />
          <div className="center-status-row compact">
            <span className="center-chip">{statusText}</span>
            <span className="center-chip">{relativeFromTimestamp(lastUpdatedAt)} · {effectiveRefreshSec}s</span>
          </div>
          {eventError ? <p className="center-callout warning">{eventError}</p> : null}
          <div className="center-actions-row primary-actions">
            <Link className="center-btn ghost" to={selectedEventKey ? `/events?event=${selectedEventKey}&tab=schedule` : '/events'} title="Go to Events">
              <CalendarIcon className="icon-inline" /> This event
            </Link>
            <button
              type="button"
              className="center-btn ghost"
              disabled={!nextLiveMatch}
              onClick={openNextLiveMatch}
              title={nextLiveMatch ? `Open ${nextLiveMatch.display_name}` : 'No live matches right now'}
            >
              <SignalIcon className="icon-inline" /> Open Next Live
            </button>
          </div>

          <div className="center-actions-row center-advanced-toggle">
            <button
              type="button"
              className={`center-btn ghost ${showFinderAdvanced ? 'active' : ''}`.trim()}
              onClick={() => setShowFinderAdvanced((current) => !current)}
              aria-expanded={showFinderAdvanced}
            >
              {showFinderAdvanced ? 'Hide More' : 'More Filters'}
            </button>
          </div>

          {showFinderAdvanced ? (
            <div className="center-advanced-panel">
              <div className="center-divider" />

              <label className="center-label" htmlFor="match-filter-input">
                Filter schedule
              </label>
              <input
                id="match-filter-input"
                value={matchFilter}
                onChange={(event) => setMatchFilter(event.target.value)}
                placeholder="Match key, display, or team"
                className="center-input"
              />

              <div className="center-sort-chip-row" role="toolbar" aria-label="Match finder sorting">
                <button
                  type="button"
                  className={`center-sort-chip ${summarySortMode === 'time' ? 'active' : ''}`.trim()}
                  onClick={() => setSummarySortMode('time')}
                >
                  <ClockIcon className="icon-inline" /> By Time
                </button>
                <button
                  type="button"
                  className={`center-sort-chip ${summarySortMode === 'status' ? 'active' : ''}`.trim()}
                  onClick={() => setSummarySortMode('status')}
                >
                  <SignalIcon className="icon-inline" /> By Status
                </button>
              </div>

              <div className="center-actions-row compact">
                <button
                  type="button"
                  className={`center-btn ghost ${autoJumpLive ? 'active' : ''}`.trim()}
                  onClick={() => setAutoJumpLive((current) => !current)}
                  title="Automatically jump to a match when it becomes live"
                >
                  {autoJumpLive ? 'Auto-Jump Live: ON' : 'Auto-Jump Live: OFF'}
                </button>
              </div>

              {recentMatchesForEvent.length > 0 ? (
                <div className="center-sort-chip-row center-recent-match-row" role="toolbar" aria-label="Recently viewed matches">
                  <span className="center-chip">Last Viewed</span>
                  {recentMatchesForEvent.map((item) => (
                    <button
                      key={`recent-match-${item.matchKey}`}
                      type="button"
                      className="center-sort-chip"
                      onClick={() => {
                        setSelectedMatchKey(item.matchKey);
                        if (isMobileLayout) setMobileFinderOpen(false);
                      }}
                      title={`Open ${item.label}`}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="center-list-scroll">
            {loadingEventData ? (
              <div className="center-loading-state">
                <SkeletonBlock rows={4} compact />
              </div>
            ) : null}
            {!loadingEventData && filteredMatches.length === 0 ? (
              <EmptyState compact title="No matches found" description="No matches found for this filter." />
            ) : null}
            {visibleFilteredMatches.map((match) => {
              const timer = liveTimerLabel(match.scheduled_time, nowMs);
              const winner = match.winner_alliance || null;
              const hasScores = matchHasScores(match);
              const isCompleted =
                Boolean(match.is_completed) ||
                winner === 'red' ||
                winner === 'blue' ||
                winner === 'tie' ||
                (hasScores && timer.state === 'ended');
              const effectiveState = isCompleted ? 'ended' : timer.state;
              const normalizedMatchKey = normalizeMatchKey(match.match_key, selectedEventKey);
              const isSelected = normalizedMatchKey === normalizedSelectedMatchKey;
              const winnerClass =
                winner === 'red'
                  ? 'winner-red'
                  : winner === 'blue'
                    ? 'winner-blue'
                    : winner === 'tie'
                      ? 'winner-tie'
                      : '';
              return (
                <div key={`match-list-${match.match_key}`} className="match-picker-row">
                  <button
                    type="button"
                    className={`event-picker-item match-picker-item ${winnerClass} ${isSelected ? 'active' : ''}`.trim()}
                    onClick={() => {
                      setSelectedMatchKey(normalizedMatchKey);
                      if (isMobileLayout) setMobileFinderOpen(false);
                    }}
                  >
                    <strong>{match.display_name}</strong>
                    <small>{fmtDateShort(match.scheduled_time)}</small>
                    <span className={`center-status-pill ${effectiveState}`}>
                      {hasScores ? `${match.red_score}-${match.blue_score}` : timer.value}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="match-picker-copy-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      void copyMatchDeepLink(normalizedMatchKey, selectedEventKey);
                    }}
                    title={`Copy deep link for ${match.display_name}`}
                    aria-label={`Copy link for ${match.display_name}`}
                  >
                    <LinkIcon size={13} />
                  </button>
                </div>
              );
            })}
          </div>
          {filteredMatches.length > visibleFilteredMatches.length ? (
            <div className="center-actions-row">
              <button
                type="button"
                className="center-btn ghost"
                onClick={() => setVisibleMatchCount((prev) => prev + 70)}
              >
                <ChevronDownIcon className="icon-inline" /> Show More Matches ({visibleFilteredMatches.length}/{filteredMatches.length})
              </button>
            </div>
          ) : null}
        </SurfaceCard>
      </aside>

      <section className="center-main">
        {/* ── Mobile: FotMob-style score hero ── */}
        {isMobileLayout && selectedMatch ? (
          <>
            <div className="fm-score-hero">
              <span className="fm-match-label">{selectedMatch.display_name}</span>
              <span className="fm-event-label">{eventTitle}</span>
              <div className="fm-score-row">
                <div className="fm-alliance fm-red">
                  <strong>RED</strong>
                  <span>{selectedRedLineupLabel}</span>
                </div>
                <div className="fm-score-center">
                  <strong className="fm-score-value">
                    {selectedHasScores
                      ? `${metric(selectedMatch.red_score, 0)} - ${metric(selectedMatch.blue_score, 0)}`
                      : '-- : --'}
                  </strong>
                  <span className={`fm-status-badge ${selectedCompleted ? 'ended' : selectedMatchTimer.state}`}>
                    {selectedCompleted ? 'FT' : `${selectedMatchTimer.label}: ${selectedMatchTimer.value}`}
                  </span>
                </div>
                <div className="fm-alliance fm-blue">
                  <strong>BLUE</strong>
                  <span>{selectedBlueLineupLabel}</span>
                </div>
              </div>
              {selectedCompleted && selectedWinner && selectedWinner !== 'tie' ? (
                <span className="fm-winner-badge">
                  {titleizeKey(selectedWinner)} alliance wins{selectedMatch.winning_score != null ? ` · ${selectedMatch.winning_score} pts` : ''}
                </span>
              ) : null}
              {selectedMatchProgress !== null ? (
                <div className="fm-progress-bar">
                  <div
                    className={`fm-progress-fill ${selectedMatchTimer.state}`}
                    style={{ width: `${selectedCompleted ? 100 : selectedMatchProgress}%` }}
                  />
                </div>
              ) : null}
            </div>
            <SegmentedTabs
              className="fm-tab-bar"
              itemClassName="fm-tab"
              ariaLabel="Match center tabs"
              value={activeTab}
              onChange={setActiveTab}
              items={MATCH_TABS.map((tab) => ({
                value: tab,
                label: titleizeKey(tab),
              }))}
            />
          </>
        ) : null}

        {/* ── Desktop: standard SurfaceCard header ── */}
        {!isMobileLayout ? (
        <SurfaceCard
          title={eventTitle}
          subtitle={selectedMatch ? selectedMatch.display_name : 'Select a match'}
          right={
            selectedMatch ? (
              <span className={`center-chip timer ${selectedMatchTimer.state}`}>
                {selectedCompleted
                  ? `Final: ${selectedHasScores ? `${selectedMatch?.red_score}-${selectedMatch?.blue_score}` : selectedMatchTimer.value}`
                  : `${selectedMatchTimer.label}: ${selectedMatchTimer.value}`}
              </span>
            ) : null
          }
        >
          <div className="center-tabs-header">
            <SegmentedTabs
              className="center-tabs"
              itemClassName="center-tab-btn"
              ariaLabel="Match center tabs"
              value={activeTab}
              onChange={setActiveTab}
              items={MATCH_TABS.map((tab) => ({
                value: tab,
                label: titleizeKey(tab),
                icon: MATCH_TAB_ICONS[tab],
              }))}
            />
          </div>
          {selectedMatch && selectedMatchProgress !== null ? (
            <div className="center-timer-progress" aria-label="Estimated match progress">
              <span className={`center-status-pill ${selectedMatchTimer.state}`}>
                {selectedCompleted ? 'Final' : `${Math.round(selectedMatchProgress)}% elapsed`}
              </span>
              <div className="center-timer-progress-track">
                <div
                  className={`center-timer-progress-fill ${selectedMatchTimer.state}`}
                  style={{ width: `${selectedCompleted ? 100 : selectedMatchProgress}%` }}
                />
              </div>
            </div>
          ) : null}
        </SurfaceCard>
        ) : null}

        {selectedEventKey && loadingEventData && scheduleRows.length === 0 ? (
          <SurfaceCard title="Loading" compactable>
            <SkeletonBlock rows={6} />
          </SurfaceCard>
        ) : null}

        {!selectedMatch ? (
          <SurfaceCard title="No Match Selected" subtitle="Choose a match from the Finder.">
            <EmptyState compact title="No match selected" description="Pick a match to load live data, stream, synergy, and form." />
          </SurfaceCard>
        ) : null}

        {selectedMatch && activeTab === 'overview' ? (
          <SurfaceCardGroup groupId="match-center-overview">
            {/* ── Desktop: Match hero card ── */}
            {!isMobileLayout ? (
            <SurfaceCard
              title={selectedIsLive ? 'Live Match View' : 'Match Snapshot'}
              subtitle={selectedIsLive ? 'Realtime score + timer.' : 'Match status summary.'}
              compactable
            >
              <div className="match-live-hero">
                <div className={`match-live-alliance red ${resolvedWinner === 'red' ? 'winner' : ''}`.trim()}>
                  <span>Red Alliance</span>
                  <strong>{selectedRedLineupLabel}</strong>
                </div>
                <div className="match-live-center">
                  <strong className="match-live-score">
                    {selectedHasScores
                      ? `${metric(selectedMatch.red_score, 0)} - ${metric(selectedMatch.blue_score, 0)}`
                      : '-- : --'}
                  </strong>
                  <span className={`center-chip timer ${selectedCompleted ? 'ended' : selectedMatchTimer.state}`}>
                    {selectedCompleted ? 'Final' : `${selectedMatchTimer.label}: ${selectedMatchTimer.value}`}
                  </span>
                  <small>Realtime polling: {effectiveRefreshSec}s</small>
                </div>
                <div className={`match-live-alliance blue ${resolvedWinner === 'blue' ? 'winner' : ''}`.trim()}>
                  <span>Blue Alliance</span>
                  <strong>{selectedBlueLineupLabel}</strong>
                </div>
              </div>
              {/* Both controls that used to sit here were duplicates of things
                  already on screen: "Watch" pointed at the same
                  liveStream.watch_url as the Live Stream card below, and
                  "Teams" ran the same setActiveTab('teams') as the Teams tab
                  directly above. Four controls, two actions, one screen.
                  The mobile action row keeps its copies — the tab strip is not
                  rendered there, so they are the only way to reach either. */}
            </SurfaceCard>
            ) : null}

            {/* ── Mobile: FotMob-style action row ── */}
            {isMobileLayout ? (
              <div className="fm-actions-row">
                {liveStream?.watch_url ? (
                  <a href={liveStream.watch_url} target="_blank" rel="noreferrer" className="center-btn" title="Watch live broadcast">
                    <VideoIcon className="icon-inline" /> Watch
                  </a>
                ) : null}
                <button type="button" className="center-btn ghost" onClick={() => setActiveTab('teams')}>
                  <UsersIcon className="icon-inline" /> Teams
                </button>
              </div>
            ) : null}

            {/* ── Mobile: FotMob-style content stack ── */}
            {isMobileLayout ? (
              <div className="fm-content-stack">
                {/* Video/Stream card */}
                <div className="fm-video-card">
                  <div className="fm-video-card-header">
                    <h4><VideoIcon className="icon-inline" /> {selectedIsLive ? 'Live Broadcast' : 'Match Stream'}</h4>
                  </div>
                  {liveStream?.embed_url ? (
                    <iframe
                      className="fm-video-embed"
                      src={liveStream.embed_url}
                      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                      referrerPolicy="strict-origin-when-cross-origin"
                      allowFullScreen
                      title="Match broadcast"
                    />
                  ) : liveStream?.watch_url ? (
                    <a href={liveStream.watch_url} target="_blank" rel="noreferrer" className="fm-video-link">
                      <VideoIcon className="icon-inline" /> Open stream
                    </a>
                  ) : (
                    <p className="fm-video-no-stream">No stream available for this match.</p>
                  )}
                </div>

                {/* Alliances card */}
                <div className="fm-alliance-card">
                  <div className="fm-alliance-card-header">
                    <h4><UsersIcon className="icon-inline" /> Alliances</h4>
                  </div>
                  <div className="fm-alliance-section">
                    <span className="fm-alliance-section-label red">
                      Red Alliance
                      {selectedHasScores ? <span className="fm-section-score">· {selectedMatch.red_score}</span> : null}
                    </span>
                    {selectedMatch.red.map((team) => {
                      const entry = liveStatusByTeam[team.team_key.toLowerCase()] || null;
                      return (
                        <button
                          key={`fm-red-${team.team_key}`}
                          type="button"
                          className="fm-team-row"
                          onClick={() => openTeamCenter(team.team_key)}
                          title={`View Team ${team.team_number}`}
                        >
                          <span className="fm-team-name">#{team.team_number} {team.nickname || team.team_key}</span>
                          <span className="fm-team-form">
                            {entry?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                            {teamFormStrip(entry)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  <div className="fm-alliance-section">
                    <span className="fm-alliance-section-label blue">
                      Blue Alliance
                      {selectedHasScores ? <span className="fm-section-score">· {selectedMatch.blue_score}</span> : null}
                    </span>
                    {selectedMatch.blue.map((team) => {
                      const entry = liveStatusByTeam[team.team_key.toLowerCase()] || null;
                      return (
                        <button
                          key={`fm-blue-${team.team_key}`}
                          type="button"
                          className="fm-team-row"
                          onClick={() => openTeamCenter(team.team_key)}
                          title={`View Team ${team.team_number}`}
                        >
                          <span className="fm-team-name">#{team.team_number} {team.nickname || team.team_key}</span>
                          <span className="fm-team-form">
                            {entry?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                            {teamFormStrip(entry)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Phase Windows card */}
                <div className="fm-phase-card">
                  <div className="fm-phase-card-header">
                    <h4>Match Phases</h4>
                  </div>
                  {loadingPhases ? (
                    <div className="center-loading-state">
                      <SkeletonBlock rows={3} compact />
                    </div>
                  ) : null}
                  {phaseError ? <p className="center-callout warning">{phaseError}</p> : null}
                  {!loadingPhases && !phaseError && !matchPhases ? (
                    <EmptyState compact title="Phase data unavailable" description="Phase data is unavailable for this match." />
                  ) : null}
                  {matchPhases ? (
                    <div className="fm-phase-grid">
                      <div className="fm-phase-item">
                        <span><RobotIcon className="icon-inline" /> Auto</span>
                        <strong>{metric(matchPhases.windows.auto.duration_sec, 0)}s</strong>
                      </div>
                      <div className="fm-phase-item">
                        <span><GamepadIcon className="icon-inline" /> Teleop</span>
                        <strong>{metric(matchPhases.windows.teleop.duration_sec, 0)}s</strong>
                      </div>
                      <div className="fm-phase-item">
                        <span><TargetIcon className="icon-inline" /> Scoring</span>
                        <strong>{metric(matchPhases.windows.teleop_scoring.duration_sec, 0)}s</strong>
                      </div>
                      <div className="fm-phase-item">
                        <span><FlagIcon className="icon-inline" /> Endgame</span>
                        <strong>{metric(matchPhases.windows.endgame.duration_sec, 0)}s</strong>
                      </div>
                    </div>
                  ) : null}
                </div>

                {/* CV Video Replayer */}
                {loadingTracks ? (
                  <div style={{ minHeight: 200 }}>
                    <SkeletonBlock rows={6} compact />
                  </div>
                ) : matchTracks && matchTracks.total_rows > 0 ? (
                  <SurfaceCard title="CV Video Replayer" collapsible>
                    <VideoReplayer
                      key={matchTracks.match_key}
                      data={matchTracks}
                      videoUrl={matchTracks.local_video_url}
                    />
                  </SurfaceCard>
                ) : null}
              </div>
            ) : null}

            {/* ── Desktop: original content grid ── */}
            {!isMobileLayout ? (
            <div className="center-content-grid">
              {selectedIsLive ? (
                renderLiveStreamCard('Live Broadcast', 'Stream replaces lineup while match is live.')
              ) : (
                <SurfaceCard title="Alliances" compactable>
                  <div className="center-alliance-split">
                    <div className={`center-alliance-col red ${resolvedWinner === 'red' ? 'winner' : ''}`.trim()}>
                      <label>
                        Red Alliance
                        {selectedHasScores ? ` · ${selectedMatch.red_score}` : ''}
                      </label>
                      {selectedMatch.red.map((team) => {
                        const entry = liveStatusByTeam[team.team_key.toLowerCase()] || null;
                        return (
                          <button
                            key={`selected-red-${team.team_key}`}
                            type="button"
                            className="center-team-chip"
                            onClick={() => openTeamCenter(team.team_key)}
                            title={`View Team ${team.team_number}`}
                          >
                            <span>
                              #{team.team_number} {team.nickname || team.team_key}
                            </span>
                            <span className="center-chip-inline">
                              {entry?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                              {teamFormStrip(entry)}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <div className={`center-alliance-col blue ${resolvedWinner === 'blue' ? 'winner' : ''}`.trim()}>
                      <label>
                        Blue Alliance
                        {selectedHasScores ? ` · ${selectedMatch.blue_score}` : ''}
                      </label>
                      {selectedMatch.blue.map((team) => {
                        const entry = liveStatusByTeam[team.team_key.toLowerCase()] || null;
                        return (
                          <button
                            key={`selected-blue-${team.team_key}`}
                            type="button"
                            className="center-team-chip"
                            onClick={() => openTeamCenter(team.team_key)}
                            title={`View Team ${team.team_number}`}
                          >
                            <span>
                              #{team.team_number} {team.nickname || team.team_key}
                            </span>
                            <span className="center-chip-inline">
                              {entry?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                              {teamFormStrip(entry)}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  {selectedCompleted && selectedWinner && (
                    <p className="center-callout muted">
                      {selectedWinner === 'tie'
                        ? 'Match ended in a tie.'
                        : `${titleizeKey(selectedWinner)} alliance won with ${selectedMatch.winning_score ?? 'N/A'} points.`}
                    </p>
                  )}
                </SurfaceCard>
              )}

              {selectedIsLive ? (
                <SurfaceCard title="Lineups" subtitle="Team lineups available in Teams tab." compactable>
                  <p className="center-callout muted">
                    Broadcast shown during live play. Use Teams tab for lineups.
                  </p>
                  <div className="center-actions-row">
                    <button type="button" className="center-btn ghost" onClick={() => setActiveTab('teams')}>
                      <UsersIcon className="icon-inline" /> Teams
                    </button>
                  </div>
                </SurfaceCard>
              ) : (
                renderLiveStreamCard()
              )}

              <SurfaceCard title="Match Phase Windows" compactable>
                {loadingPhases ? (
                  <div className="center-loading-state">
                    <SkeletonBlock rows={3} compact />
                  </div>
                ) : null}
                {phaseError ? <p className="center-callout warning">{phaseError}</p> : null}
                {!loadingPhases && !phaseError && !matchPhases ? (
                  <EmptyState compact title="Phase data unavailable" description="Phase data is unavailable for this match." />
                ) : null}
                {matchPhases ? (
                  <div className="center-phase-grid">
                    <article className="center-kpi-card">
                      <span><RobotIcon className="icon-inline" /> Auto</span>
                      <strong>
                        {metric(matchPhases.windows.auto.duration_sec, 0)}s ({metric(matchPhases.windows.auto.confidence_0_1 ?? null, 2)})
                      </strong>
                    </article>
                    <article className="center-kpi-card">
                      <span><GamepadIcon className="icon-inline" /> Teleop</span>
                      <strong>{metric(matchPhases.windows.teleop.duration_sec, 0)}s</strong>
                    </article>
                    <article className="center-kpi-card">
                      <span><TargetIcon className="icon-inline" /> Teleop Scoring</span>
                      <strong>{metric(matchPhases.windows.teleop_scoring.duration_sec, 0)}s</strong>
                    </article>
                    <article className="center-kpi-card">
                      <span><FlagIcon className="icon-inline" /> Endgame</span>
                      <strong>{metric(matchPhases.windows.endgame.duration_sec, 0)}s</strong>
                    </article>
                  </div>
                ) : null}
              </SurfaceCard>

              {/* ── CV Video Replayer ────────────────────────── */}
              {loadingTracks ? (
                <SurfaceCard title="CV Video Replayer" subtitle="Loading tracking data..." compactable>
                  <div style={{ minHeight: 300 }}>
                    <SkeletonBlock rows={8} />
                  </div>
                </SurfaceCard>
              ) : matchTracks && matchTracks.total_rows > 0 ? (
                <SurfaceCard title="CV Video Replayer" collapsible compactable>
                  <VideoReplayer
                    key={matchTracks.match_key}
                    data={matchTracks}
                    videoUrl={matchTracks.local_video_url}
                  />
                </SurfaceCard>
              ) : null}
            </div>
            ) : null}
          </SurfaceCardGroup>
        ) : null}

        {selectedMatch && activeTab === 'breakdown' ? (
          <SurfaceCardGroup groupId="match-center-breakdown">
            <SurfaceCard title="Match Breakdown" compactable>
            <div className="center-kpi-grid">
              <article className="center-kpi-card">
                <span><SignalIcon className="icon-inline" /> Status</span>
                <strong>{selectedCompleted ? 'Final' : titleizeKey(selectedMatchTimer.state)}</strong>
              </article>
              <article className="center-kpi-card">
                <span><ScoreboardIcon className="icon-inline" /> Score</span>
                <strong>
                  {selectedHasScores
                    ? `${metric(selectedMatch.red_score, 0)} - ${metric(selectedMatch.blue_score, 0)}`
                    : 'N/A'}
                </strong>
              </article>
              <article className="center-kpi-card">
                <span><TrophyIcon className="icon-inline" /> Winner</span>
                <strong>
                  {resolvedWinner === 'tie'
                    ? 'Tie'
                    : resolvedWinner
                      ? `${titleizeKey(resolvedWinner)} Alliance`
                      : 'Pending'}
                </strong>
              </article>
              <article className="center-kpi-card">
                <span><CalendarIcon className="icon-inline" /> Scheduled</span>
                <strong>{fmtDateShort(selectedMatch.scheduled_time)}</strong>
              </article>
            </div>

            {!synergyAvailable ? (
              <p className="center-callout muted">
                Synergy unavailable for this match.
              </p>
            ) : (
              <>
                <div className="center-kpi-grid">
                  <article className="center-kpi-card tone-red">
                    <span><HandshakeIcon className="icon-inline" /> Red Synergy</span>
                    <strong>{metric(selectedRedSynergy?.alliance_synergy_score_0_100 ?? null, 1)} / 100</strong>
                    <small>
                      {metric(selectedRedSynergy?.alliance_synergy_points ?? null, 1)} pts ·{' '}
                      {titleizeKey(selectedRedSynergy?.source_label || 'projection')}
                    </small>
                  </article>
                  <article className="center-kpi-card tone-blue">
                    <span><HandshakeIcon className="icon-inline" /> Blue Synergy</span>
                    <strong>{metric(selectedBlueSynergy?.alliance_synergy_score_0_100 ?? null, 1)} / 100</strong>
                    <small>
                      {metric(selectedBlueSynergy?.alliance_synergy_points ?? null, 1)} pts ·{' '}
                      {titleizeKey(selectedBlueSynergy?.source_label || 'projection')}
                    </small>
                  </article>
                </div>

                {/* One render at every width. The sort chips used to be inside
                    the mobile branch, so a desktop visitor could not sort the
                    pairs at all. */}
                <div className="center-sort-chip-row" role="toolbar" aria-label="Pair breakdown sorting">
                  <button
                    type="button"
                    className={`center-sort-chip ${pairSortMode === 'points' ? 'active' : ''}`.trim()}
                    onClick={() => setPairSortMode('points')}
                  >
                    <ScoreboardIcon className="icon-inline" /> By Points
                  </button>
                  <button
                    type="button"
                    className={`center-sort-chip ${pairSortMode === 'confidence' ? 'active' : ''}`.trim()}
                    onClick={() => setPairSortMode('confidence')}
                  >
                    <StarIcon className="icon-inline" /> By Confidence
                  </button>
                </div>
                <Table
                  columns={pairBreakdownColumns}
                  rows={selectedPairBreakdown}
                  rowKey={(pair, index) => `pair-${pair.alliance}-${index}`}
                  cardBreakpoint={MOBILE_LAYOUT_BREAKPOINT}
                  empty="No pair synergy data."
                />
              </>
            )}
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}

        {selectedMatch && activeTab === 'teams' ? (
          <SurfaceCardGroup groupId="match-center-teams">
            <SurfaceCard title="Team Context" subtitle="Live form for all six teams." compactable>
            <div className="center-team-card-grid">
              {selectedTeams.map((team) => {
                const entry = liveStatusByTeam[team.team_key.toLowerCase()] || null;
                return (
                  <article key={`match-team-${team.team_key}-${team.station || ''}`} className="center-team-card">
                    <header>
                      <span className={`center-alliance-pill ${team.alliance}`}>{team.alliance.toUpperCase()}</span>
                      {entry?.is_live ? (
                        <span className="center-live-chip">
                          <i className="center-live-dot" aria-hidden="true" />
                          Live
                        </span>
                      ) : null}
                    </header>
                    <strong>
                      #{team.team_number} {team.nickname || team.team_key}
                    </strong>
                    <small>{team.station ? `Station ${team.station}` : 'Station N/A'}</small>
                    <div>{teamFormStrip(entry)}</div>
                    <button type="button" className="center-btn ghost" onClick={() => openTeamCenter(team.team_key)} title={`View Team ${team.team_number}`}>
                      <EyeIcon className="icon-inline" /> Team Details
                    </button>
                  </article>
                );
              })}
            </div>
            <p className="center-callout muted">Match scheduled: {fmtUnix(selectedMatch.scheduled_time)}</p>
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}
      </section>
    </div>
    </div>
    </>
  );
}
