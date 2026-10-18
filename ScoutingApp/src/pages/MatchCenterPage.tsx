import { matchLabelFromKey } from '../utils/matchLabel';
import { formatDayTime } from '../utils/formatTime';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { shareableAppUrl } from '../platform/runtime';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  getEventLiveStream,
  getEventRatings,
  getEventSchedule,
  getEventTeamLiveForm,
  getMatchPhases,
} from '../api';
import { SAVED_COPY_MAX_AGE_MS } from '../config/savedCopy';
import { embeddableUrl } from '../config/embedHosts';
import type {
  EventScheduleItem,
  EventTeamLiveFormResponse,
  EventTeamRatingItem,
  MatchPhasesResponse,
  PredictedScheduleMatch,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { SkeletonBlock } from '../components/ui/SkeletonBlock';
import { EmptyState } from '../components/ui/EmptyState';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { PageViewBar } from '../components/PageViewBar';
import { MATCH_HUB_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { useExternalSearchSync } from '../hooks/useExternalSearchSync';
import { useLiveRefreshSetting } from '../hooks/useLiveRefreshSetting';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { usePageClock } from '../hooks/usePageClock';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling, type SingleFlightPollReason } from '../hooks/useSingleFlightPolling';
import {
  EyeIcon, PieChartIcon, UsersIcon, ClockIcon,
  ChevronDownIcon, ChevronLeftIcon, VideoIcon, TrophyIcon,
  CalendarIcon, SignalIcon, ScoreboardIcon, RobotIcon, GamepadIcon,
  FlagIcon, TargetIcon, LinkIcon,
} from '../components/ui/Icons';
import {
  buildMatchCenterPath,
  copyTextToClipboard,
  eventKeyFromMatchKey,
  fmtDateShort,
  liveTimerLabel, matchStartTime,
  metric,
  normalizeEventKey,
  normalizeMatchKey,
  relativeFromTimestamp,
  titleizeKey,
} from './centerUtils';
import { readStoredCenterContext, writeCenterContext } from '../layout/centerContext';
import { SEASON } from '../config/season';
import { LiteStreamEmbed } from '../components/LiteStreamEmbed';
import { pickDefaultMatchKey } from './homeFeed';
import { inferMatchCompleted, matchHasScores } from './matchStatus';
import { TeamFormStrip } from '../components/TeamFormStrip';
import { resolveTab } from './tabUtils';
import { loadMatchCenterPredictions } from '../features/prefetch/pageRequests';
import { MatchClock } from '../features/matchResult/MatchClock';
import { MatchPredictionNote } from '../features/predictions/MatchPredictionNote';
import { RollingScore } from '../features/matchResult/RollingScore';
import resultStyles from '../features/matchResult/MatchResult.module.css';
import { ShareMatchDialog } from '../features/shareCard/ShareMatchDialog';
import { eventDisplayName } from '../utils/eventSearchIndex';
import { storageGet, storageSet } from '../platform/safeStorage';

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
    const raw = storageGet(MATCH_CENTER_VIEW_PREFS_STORAGE);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return parsed as Record<string, MatchCenterEventViewPrefs>;
  } catch {
    return {};
  }
}

function writeMatchCenterEventViewPrefs(next: Record<string, MatchCenterEventViewPrefs>) {
  storageSet(MATCH_CENTER_VIEW_PREFS_STORAGE, JSON.stringify(next));
}

function readMatchCenterRecentMatches(): MatchCenterRecentMatchesMap {
  try {
    const raw = storageGet(MATCH_CENTER_RECENT_STORAGE);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return parsed as MatchCenterRecentMatchesMap;
  } catch {
    return {};
  }
}

function writeMatchCenterRecentMatches(next: MatchCenterRecentMatchesMap) {
  storageSet(MATCH_CENTER_RECENT_STORAGE, JSON.stringify(next));
}

function ShareIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" className="icon-inline" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v12M7 8l5-5 5 5M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5" />
    </svg>
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
  const defaultTab = resolveTab(tabParam, MATCH_TABS, 'overview');

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
  const [predictedMatches, setPredictedMatches] = useState<Record<string, PredictedScheduleMatch>>({});
  const [matchPhases, setMatchPhases] = useState<MatchPhasesResponse | null>(null);


  const [loadingEventData, setLoadingEventData] = useState(false);
  // Placeholders only while an event loads for the first time. Every live poll also sets
  // loadingEventData, and showing skeletons then flashed grey lines above the list every 5 s.
  const [eventLoadedKey, setEventLoadedKey] = useState('');
  const firstEventLoad = loadingEventData && eventLoadedKey !== selectedEventKey;
  const [loadingPhases, setLoadingPhases] = useState(false);
  const [statusText, setStatusText] = useState('Select an event and match.');
  const [eventError, setEventError] = useState('');
  const [phaseError, setPhaseError] = useState('');
  const [matchFilter, setMatchFilter] = useState('');
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [eventHasLiveMatch, setEventHasLiveMatch] = useState(false);
  const [visibleMatchCount, setVisibleMatchCount] = useState(70);
  const [mobileFinderOpen, setMobileFinderOpen] = useState(() => !defaultMatchKey);
  const [summarySortMode, setSummarySortMode] = useState<'time' | 'status'>('time');
  const [autoJumpLive, setAutoJumpLive] = useState(false);
  const [showFinderAdvanced, setShowFinderAdvanced] = useState(false);
  const [recentMatchesByEvent, setRecentMatchesByEvent] = useState<MatchCenterRecentMatchesMap>(() =>
    readMatchCenterRecentMatches(),
  );
  const lastEventContextRef = useRef('');
  const staticFailedRef = useRef(false);
  const currentEventKeyRef = useRef(selectedEventKey);
  currentEventKeyRef.current = selectedEventKey;
  const lastAutoJumpMatchKeyRef = useRef('');

  useEffect(() => {
    if (!isMobileLayout) setMobileFinderOpen(false);
  }, [isMobileLayout]);

  // A link to another match (or back/forward) changes the URL under the page;
  // bring the selection over to it. See useExternalSearchSync for why the
  // writer below has to sit this round out.
  const urlSync = useExternalSearchSync(searchParams, (params) => {
    const urlEventKey = normalizeEventKey(params.get('event'));
    const storedEventKey = readStoredCenterContext().eventKey;
    const provisionalEventKey = urlEventKey || storedEventKey;
    const urlMatchKey = normalizeMatchKey(params.get('match'), provisionalEventKey);
    const eventFromMatchKey = eventKeyFromMatchKey(urlMatchKey);
    const resolvedEventKey = eventFromMatchKey || urlEventKey;
    const resolvedMatchKey = normalizeMatchKey(urlMatchKey, resolvedEventKey || provisionalEventKey);
    const urlTab = params.get('tab');

    if (resolvedEventKey) {
      setEventInput((prev) => (prev === resolvedEventKey ? prev : resolvedEventKey));
      setSelectedEventKey((prev) => (prev === resolvedEventKey ? prev : resolvedEventKey));
    }
    if (resolvedMatchKey) {
      setSelectedMatchKey((prev) => (prev === resolvedMatchKey ? prev : resolvedMatchKey));
    }
    const nextTab = resolveTab(urlTab, MATCH_TABS, 'overview');
    setActiveTab((prev) => (prev === nextTab ? prev : nextTab));
  });

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
    if (!urlSync.shouldWrite()) return;
    const next = new URLSearchParams();
    if (selectedEventKey) next.set('event', selectedEventKey);
    if (selectedMatchKey) next.set('match', selectedMatchKey);
    next.set('tab', activeTab);

    if (next.toString() !== searchParams.toString()) {
      urlSync.markWritten(next.toString());
      setSearchParams(next, { replace: true });
    }
    writeCenterContext({
      eventKey: selectedEventKey,
      matchKey: selectedMatchKey,
      sourcePath: '/match-center',
    });
  }, [activeTab, searchParams, selectedEventKey, selectedMatchKey, setSearchParams, urlSync]);

  useEffect(() => {
    if (selectedEventKey) return;
    lastEventContextRef.current = '';
    setScheduleRows([]);
    setEventName(null);
    setTeamLiveForm(null);
    setLiveStream(null);
    setPredictedMatches({});
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
      setPredictedMatches({});
      setEventHasLiveMatch(false);
    }
    lastEventContextRef.current = selectedEventKey;

    setLoadingEventData(true);
    setEventError('');
    setStatusText(
      contextChanged || reason === 'initial'
        ? 'Loading event…'
        : 'Refreshing…',
    );

    try {
      // A failed stream or prediction load retries on the next poll.
      const shouldRefreshStatic = contextChanged || reason !== 'poll' || staticFailedRef.current;
      const shouldBypassLiveCache = reason === 'poll' && eventHasLiveMatch;
      const liveRequestOptions = shouldBypassLiveCache
        ? { bypassCache: true, cacheTtlMs: 0, staleWhileRevalidateMs: 0 }
        : undefined;
      const [scheduleResult, liveFormResult, streamResult, predictionResult] = await Promise.allSettled([
        getEventSchedule(
          selectedEventKey,
          false,
          undefined,
          liveRequestOptions ?? { showSavedMaxAgeMs: SAVED_COPY_MAX_AGE_MS.schedule },
        ),
        getEventTeamLiveForm(selectedEventKey, { form_window: 5, live_window_sec: 180 }, liveRequestOptions),
        shouldRefreshStatic ? getEventLiveStream(selectedEventKey) : Promise.resolve(null),
        shouldRefreshStatic
          ? loadMatchCenterPredictions(selectedEventKey)
          : Promise.resolve(null),
      ]);
      // Another event was picked while this was loading; its own load follows right away.
      if (currentEventKeyRef.current !== selectedEventKey) return true;

      const errors: string[] = [];

      if (scheduleResult.status === 'fulfilled') {
        const rows = scheduleResult.value.matches || [];
        setScheduleRows(rows);
        const now = Date.now();
        const hasLive = rows.some(
          (row) => !inferMatchCompleted(row, now) && liveTimerLabel(matchStartTime(row), now).state === 'live',
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
          embed_url: embeddableUrl(preferred?.embed_url),
          game_day_url: streamResult.value.game_day_url,
          available: streamResult.value.available,
          detail: streamResult.value.detail,
        });
      } else if (shouldRefreshStatic && streamResult.status === 'rejected') {
        if (contextChanged) setLiveStream(null);
        errors.push(`Live stream: ${streamResult.reason instanceof Error ? streamResult.reason.message : 'failed'}`);
      }

      if (shouldRefreshStatic && predictionResult.status === 'fulfilled' && predictionResult.value) {
        const lookup: Record<string, PredictedScheduleMatch> = {};
        for (const row of predictionResult.value.matches || []) {
          lookup[row.match_key.toLowerCase()] = row;
        }
        setPredictedMatches(lookup);
      } else if (shouldRefreshStatic && predictionResult.status === 'rejected') {
        if (contextChanged) setPredictedMatches({});
        errors.push(`Predictions: ${predictionResult.reason instanceof Error ? predictionResult.reason.message : 'failed'}`);
      }

      if (shouldRefreshStatic) {
        staticFailedRef.current = streamResult.status === 'rejected' || predictionResult.status === 'rejected';
      }
      setEventError(errors.join(' | '));
      setLastUpdatedAt(Date.now());
      setStatusText(
        errors.length > 0
          ? 'Some data is missing'
          : 'Up to date',
      );
      return errors.length === 0;
    } finally {
      setLoadingEventData(false);
      if (currentEventKeyRef.current === selectedEventKey) setEventLoadedKey(selectedEventKey);
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
      const fallbackKey = pickDefaultMatchKey(scheduleRows, Date.now()) || scheduleRows[0].match_key;
      setSelectedMatchKey(normalizeMatchKey(fallbackKey, selectedEventKey));
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
        const aTimer = liveTimerLabel(matchStartTime(a), nowMs);
        const bTimer = liveTimerLabel(matchStartTime(b), nowMs);
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

  const selectedMatchStart = matchStartTime(selectedMatch);
  const selectedMatchTimer = useMemo(
    () => liveTimerLabel(selectedMatchStart, nowMs),
    [nowMs, selectedMatchStart],
  );
  // From the same start the timer uses (actual, else TBA's prediction, else the schedule), so a
  // field running late doesn't show a match half over before it starts.
  const selectedMatchProgress = useMemo(() => {
    if (!selectedMatchStart) return null;
    const elapsedSec = Math.max(0, Math.floor(nowMs / 1000) - selectedMatchStart);
    return Math.max(0, Math.min(100, (elapsedSec / SEASON.matchSec) * 100));
  }, [nowMs, selectedMatchStart]);
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

  // The result arriving while you watch: scores appear on a match that had none a moment ago.
  // That gets the moment (score rolls in, the winner lights up); opening a finished match doesn't.
  const [revealMatchKey, setRevealMatchKey] = useState('');
  const lastScoreSeenRef = useRef({ key: '', hadScores: false });
  const selectedMatchKeyForReveal = selectedMatch?.match_key || '';
  useEffect(() => {
    const previous = lastScoreSeenRef.current;
    lastScoreSeenRef.current = { key: selectedMatchKeyForReveal, hadScores: selectedHasScores };
    if (!selectedMatchKeyForReveal || previous.key !== selectedMatchKeyForReveal || previous.hadScores || !selectedHasScores) return undefined;
    setRevealMatchKey(selectedMatchKeyForReveal);
    const timer = window.setTimeout(() => setRevealMatchKey(''), 4500);
    return () => window.clearTimeout(timer);
  }, [selectedMatchKeyForReveal, selectedHasScores]);
  const revealing = Boolean(revealMatchKey) && revealMatchKey === selectedMatchKeyForReveal;
  const revealClass = (side: 'red' | 'blue') => {
    if (!revealing || !resolvedWinner || resolvedWinner === 'tie') return '';
    if (resolvedWinner !== side) return resultStyles.lost;
    return side === 'red' ? resultStyles.wonRed : resultStyles.wonBlue;
  };

  // A finished match can be shared as a picture.
  const [shareOpen, setShareOpen] = useState(false);
  const shareable = Boolean(selectedMatch && selectedHasScores && selectedCompleted);

  const selectedPrediction = useMemo(() => {
    if (!normalizedSelectedMatchKey) return null;
    return predictedMatches[normalizedSelectedMatchKey] || null;
  }, [normalizedSelectedMatchKey, predictedMatches]);
  // Each team's FRCMOB rating for the Teams tab (the cached summary other pages read).
  const [teamRatings, setTeamRatings] = useState<{ eventKey: string; byTeam: Record<string, EventTeamRatingItem> } | null>(null);
  useEffect(() => {
    if (!selectedEventKey || activeTab !== 'teams' || teamRatings?.eventKey === selectedEventKey) return;
    let live = true;
    getEventRatings(selectedEventKey)
      .then((response) => {
        if (!live) return;
        const byTeam: Record<string, EventTeamRatingItem> = {};
        for (const row of response.ratings || []) byTeam[row.team_key.toLowerCase()] = row;
        setTeamRatings({ eventKey: selectedEventKey, byTeam });
      })
      .catch(() => {
        // The cards still show form and links; the rating line is left out.
      });
    return () => { live = false; };
  }, [selectedEventKey, activeTab, teamRatings?.eventKey]);
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
      (row) => !inferMatchCompleted(row, nowMs) && liveTimerLabel(matchStartTime(row), nowMs).state === 'live',
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
    const deepLink = shareableAppUrl(path);
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

  function renderLiveStreamCard(title = 'Live Stream', subtitle?: string) {
    return (
      // Fullscreen and minimize remount the card's content, which stopped a playing video.
      <SurfaceCard title={title} subtitle={subtitle} expandable={false} mobileCollapsible={false}>
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
            <LiteStreamEmbed key={liveStream.embed_url} title={`Live stream for ${selectedEventKey}`} src={liveStream.embed_url} />
          </div>
        ) : (
          <p className="center-callout muted">No stream embed available.</p>
        )}
      </SurfaceCard>
    );
  }

  const eventTitle = eventName || (selectedEventKey ? eventDisplayName(selectedEventKey) : 'Match Center');
  // While the schedule loads, the match being opened is already known from the address: say so
  // instead of "Select a match".
  const pendingMatchLabel = !selectedMatch && normalizedSelectedMatchKey ? matchLabelFromKey(normalizedSelectedMatchKey) : null;

  return (
    <>
    <PageViewBar items={MATCH_HUB_VIEWS} />
    <div className="match-center-page">
    <div className={`center-layout center-layout-match mobile-finder-layout scouting-layout-grid ${isMobileLayout && mobileFinderOpen ? 'mobile-finder-open' : ''}`.trim()}>
      {/* Phones used to get a Match Finder / Match Center toggle here, the
          second of four stacked tab rows before any content. The finder now
          opens from "Change match" in the score hero, and closes by picking
          a match or with the button below. */}
      <aside className="center-sidebar">
        {isMobileLayout && selectedMatch ? (
          <button type="button" className="center-btn ghost mc-back-to-match" onClick={() => setMobileFinderOpen(false)}>
            <ChevronLeftIcon className="icon-inline" /> Back to {selectedMatch.display_name}
          </button>
        ) : null}
        <SurfaceCard title="Match Finder" compactable>
          <EventPicker
            label="Event"
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
            {statusText !== 'Up to date' ? <span className="center-chip">{statusText}</span> : null}
            <span className="center-chip" title={`Refreshes every ${effectiveRefreshSec}s`}>Updated {relativeFromTimestamp(lastUpdatedAt)}</span>
          </div>
          {eventError ? <p className="center-callout warning" title={eventError}>Some of this event's data didn't load. It retries on its own.</p> : null}
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
            {firstEventLoad ? (
              <div className="center-loading-state">
                <SkeletonBlock rows={4} compact />
              </div>
            ) : null}
            {!firstEventLoad && filteredMatches.length === 0 ? (
              matchFilter.trim() ? (
                <EmptyState compact title="Nothing fits this filter" description="Try another filter, or clear the search." />
              ) : (
                <EmptyState compact title="No match schedule yet" description="Matches show here once the event publishes its schedule." />
              )
            ) : null}
            {visibleFilteredMatches.map((match) => {
              const timer = liveTimerLabel(matchStartTime(match), nowMs);
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
                    <small>{fmtDateShort(matchStartTime(match))}</small>
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
            <div className={`fm-score-hero ${revealing ? resultStyles.reveal : ''}`.trim()}>
              <span className="fm-match-label">{selectedMatch.display_name}</span>
              <span className="fm-event-label">{eventTitle}</span>
              <div className="fm-score-row">
                <div className={`fm-alliance fm-red ${revealClass('red')}`.trim()}>
                  <strong>RED</strong>
                  <span>{selectedRedLineupLabel}</span>
                </div>
                <div className="fm-score-center">
                  <strong className="fm-score-value">
                    {selectedHasScores
                      ? <RollingScore red={Number(selectedMatch.red_score ?? 0)} blue={Number(selectedMatch.blue_score ?? 0)} roll={revealing} />
                      : '-- : --'}
                  </strong>
                  <span className={`fm-status-badge ${selectedCompleted ? 'ended' : selectedMatchTimer.state}`}>
                    {selectedCompleted ? 'Final' : `${selectedMatchTimer.label}: ${selectedMatchTimer.value}`}
                  </span>
                </div>
                <div className={`fm-alliance fm-blue ${revealClass('blue')}`.trim()}>
                  <strong>BLUE</strong>
                  <span>{selectedBlueLineupLabel}</span>
                </div>
              </div>
              {selectedCompleted && selectedWinner && selectedWinner !== 'tie' ? (
                <span className={`fm-winner-badge ${selectedWinner}`}>
                  {titleizeKey(selectedWinner)} alliance wins{selectedMatch.winning_score != null ? ` · ${selectedMatch.winning_score} pts` : ''}
                </span>
              ) : null}
              <MatchPredictionNote match={selectedPrediction} completed={selectedCompleted} winner={resolvedWinner} />
              {selectedMatchProgress !== null && !selectedCompleted ? (
                <MatchClock progress={selectedMatchProgress} className="fm-match-clock" />
              ) : null}
              <div className={resultStyles.heroActions}>
                <button
                  type="button"
                  className="center-btn ghost mc-change-match"
                  onClick={() => {
                    setMobileFinderOpen(true);
                    window.scrollTo({ top: 0 });
                  }}
                >
                  Change match
                </button>
                {shareable ? (
                  <button type="button" className="center-btn ghost" onClick={() => setShareOpen(true)}>
                    <ShareIcon /> Share
                  </button>
                ) : null}
              </div>
            </div>
            <SegmentedTabs
              className="fm-tab-bar"
              motion
              swipe
              ink
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
          className="mc-hero-card"
          title={eventTitle}
          subtitle={selectedMatch ? selectedMatch.display_name : pendingMatchLabel ?? 'Select a match'}
          right={
            selectedMatch ? (
              <span className={resultStyles.heroActions}>
                <span className={`center-chip timer ${selectedMatchTimer.state}`}>
                  {selectedCompleted
                    ? `Final: ${selectedHasScores ? `${selectedMatch?.red_score}-${selectedMatch?.blue_score}` : selectedMatchTimer.value}`
                    : `${selectedMatchTimer.label}: ${selectedMatchTimer.value}`}
                </span>
                {shareable ? (
                  <button type="button" className="center-btn ghost" onClick={() => setShareOpen(true)}>
                    <ShareIcon /> Share
                  </button>
                ) : null}
              </span>
            ) : null
          }
        >
          <div className="center-tabs-header">
            <SegmentedTabs
              className="center-tabs"
              motion
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
          {/* A finished match already says "Final: score" in the header chip. */}
          {selectedMatch && selectedMatchProgress !== null && !selectedCompleted ? (
            <div className="center-timer-progress" aria-label="Estimated match progress">
              <span className={`center-status-pill ${selectedMatchTimer.state}`}>
                {selectedCompleted ? 'Final' : `${Math.round(selectedMatchProgress)}% elapsed`}
              </span>
              <MatchClock progress={selectedMatchProgress} />
            </div>
          ) : null}
        </SurfaceCard>
        ) : null}

        {/* Shaped like the Match Snapshot card that replaces it, so the page doesn't jump. */}
        {selectedEventKey && loadingEventData && scheduleRows.length === 0 ? (
          <SurfaceCard title={pendingMatchLabel ? 'Match Snapshot' : 'Loading'} compactable>
            <SkeletonBlock rows={4} />
          </SurfaceCard>
        ) : null}

        {/* Not while the schedule is still coming: "No match selected" flashed under the
            loading card on every opened match. */}
        {/* A match was asked for but the schedule didn't load: say that, not "pick a match". */}
        {!selectedMatch && pendingMatchLabel && scheduleRows.length === 0 && eventError && !loadingEventData ? (
          <SurfaceCard title={pendingMatchLabel}>
            <EmptyState
              compact
              title="This match didn't load"
              description="The schedule couldn't be reached. It retries on its own."
            />
          </SurfaceCard>
        ) : null}
        {!selectedMatch && !(selectedEventKey && loadingEventData && scheduleRows.length === 0)
          && !(pendingMatchLabel && scheduleRows.length === 0 && eventError) ? (
          <SurfaceCard title="No Match Selected" subtitle="Choose a match from the Finder.">
            <EmptyState
              compact
              mascot
              title="No match selected"
              description={isMobileLayout
                ? 'Pick a match to see its score, stream, teams and forecast.'
                : 'Pick a match in the Finder to see its score, stream, teams and forecast.'}
              action={isMobileLayout ? (
                <button type="button" className="center-btn" onClick={() => setMobileFinderOpen(true)}>Pick a match</button>
              ) : undefined}
            />
          </SurfaceCard>
        ) : null}

        {selectedMatch && activeTab === 'overview' ? (<>
            {/* ── Desktop: Match hero card ── */}
            {!isMobileLayout ? (
            <SurfaceCard
              title={selectedIsLive ? 'Live Match View' : 'Match Snapshot'}
              subtitle={selectedIsLive ? `Updates every ${effectiveRefreshSec}s.` : undefined}
              compactable
            >
              <div className={`match-live-hero ${revealing ? resultStyles.reveal : ''}`.trim()}>
                <div className={`match-live-alliance red ${resolvedWinner === 'red' ? 'winner' : ''} ${revealClass('red')}`.trim()}>
                  <span>Red Alliance</span>
                  <strong>{selectedRedLineupLabel}</strong>
                </div>
                <div className="match-live-center">
                  <strong className="match-live-score">
                    {selectedHasScores
                      ? <RollingScore red={Number(selectedMatch.red_score ?? 0)} blue={Number(selectedMatch.blue_score ?? 0)} roll={revealing} />
                      : '-- : --'}
                  </strong>
                  {selectedCompleted ? null : (
                    <span className={`center-chip timer ${selectedMatchTimer.state}`}>
                      {`${selectedMatchTimer.label}: ${selectedMatchTimer.value}`}
                    </span>
                  )}
                  <MatchPredictionNote match={selectedPrediction} completed={selectedCompleted} winner={resolvedWinner} />
                </div>
                <div className={`match-live-alliance blue ${resolvedWinner === 'blue' ? 'winner' : ''} ${revealClass('blue')}`.trim()}>
                  <span>Blue Alliance</span>
                  <strong>{selectedBlueLineupLabel}</strong>
                </div>
              </div>
              {/* Both controls that used to sit here were duplicates of things
                  already on screen: "Watch" pointed at the same
                  liveStream.watch_url as the Live Stream card below, and
                  "Teams" ran the same setActiveTab('teams') as the Teams tab
                  directly above. Four controls, two actions, one screen.
                  Phones had the same pair again as an action row; the mobile
                  hero renders its own tab strip and the stream card sits right
                  below, so that row went too. */}
            </SurfaceCard>
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
                    <LiteStreamEmbed key={liveStream.embed_url} className="fm-video-embed" title="Match broadcast" src={liveStream.embed_url} />
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
                          data-team-peek={team.team_key.toLowerCase()} data-team-peek-event={selectedEventKey} onClick={() => openTeamCenter(team.team_key)}
                          title={`View Team ${team.team_number}`}
                        >
                          <span className="fm-team-name">#{team.team_number} {team.nickname || team.team_key}</span>
                          <span className="fm-team-form">
                            {entry?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                            <TeamFormStrip entry={entry} />
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
                          data-team-peek={team.team_key.toLowerCase()} data-team-peek-event={selectedEventKey} onClick={() => openTeamCenter(team.team_key)}
                          title={`View Team ${team.team_number}`}
                        >
                          <span className="fm-team-name">#{team.team_number} {team.nickname || team.team_key}</span>
                          <span className="fm-team-form">
                            {entry?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                            <TeamFormStrip entry={entry} />
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
                            data-team-peek={team.team_key.toLowerCase()} data-team-peek-event={selectedEventKey} onClick={() => openTeamCenter(team.team_key)}
                            title={`View Team ${team.team_number}`}
                          >
                            <span>
                              #{team.team_number} {team.nickname || team.team_key}
                            </span>
                            <span className="center-chip-inline">
                              {entry?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                              <TeamFormStrip entry={entry} />
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
                            data-team-peek={team.team_key.toLowerCase()} data-team-peek-event={selectedEventKey} onClick={() => openTeamCenter(team.team_key)}
                            title={`View Team ${team.team_number}`}
                          >
                            <span>
                              #{team.team_number} {team.nickname || team.team_key}
                            </span>
                            <span className="center-chip-inline">
                              {entry?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                              <TeamFormStrip entry={entry} />
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
                        {metric(matchPhases.windows.auto.duration_sec, 0)}s
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

            </div>
            ) : null}
          </>
        ) : null}

        {selectedMatch && activeTab === 'breakdown' ? (
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
                <strong>{fmtDateShort(matchStartTime(selectedMatch))}</strong>
              </article>
            </div>

            </SurfaceCard>

        ) : null}

        {selectedMatch && activeTab === 'teams' ? (
            <SurfaceCard title="Team Context" subtitle="Rating and recent form for all six teams." compactable>
            <div className="center-team-card-grid">
              {selectedTeams.map((team) => {
                const entry = liveStatusByTeam[team.team_key.toLowerCase()] || null;
                const rating = teamRatings?.eventKey === selectedEventKey ? teamRatings.byTeam[team.team_key.toLowerCase()] || null : null;
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
                    {rating ? (
                      <small>
                        Rating {metric(rating.rating_0_100, 1)}
                        {rating.expected_points ? ` · ${metric(rating.expected_points.total, 0)} pts/match` : ''}
                      </small>
                    ) : null}
                    <div><TeamFormStrip entry={entry} /></div>
                    <button type="button" className="center-btn ghost" data-team-peek={team.team_key.toLowerCase()} data-team-peek-event={selectedEventKey} onClick={() => openTeamCenter(team.team_key)} title={`View Team ${team.team_number}`}>
                      <EyeIcon className="icon-inline" /> Team Details
                    </button>
                  </article>
                );
              })}
            </div>
            <p className="center-callout muted">Match scheduled: {selectedMatch.scheduled_time ? formatDayTime(selectedMatch.scheduled_time * 1000) : 'Unknown'}</p>
            </SurfaceCard>

        ) : null}
      </section>
    </div>
    </div>
    {shareable && selectedMatch ? (
      <ShareMatchDialog
        open={shareOpen}
        onClose={() => setShareOpen(false)}
        route={buildMatchCenterPath(selectedEventKey, selectedMatch.match_key)}
        filename={`${selectedMatch.match_key}.png`}
        match={{
          eventName: eventTitle,
          matchLabel: selectedMatch.display_name,
          redScore: Number(selectedMatch.red_score ?? 0),
          blueScore: Number(selectedMatch.blue_score ?? 0),
          red: selectedMatch.red.map((team) => team.team_number),
          blue: selectedMatch.blue.map((team) => team.team_number),
          winner: resolvedWinner,
        }}
      />
    ) : null}
    </>
  );
}
