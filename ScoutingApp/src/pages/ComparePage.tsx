import { breakdownFromIntel, ratingFromIntel, type TeamIntelBreakdown } from './teamIntel';
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { PageViewBar } from '../components/PageViewBar';
import { COMPARE_VIEWS } from '../components/pageViewBarConfig';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import {
  getEventTeamsIntel,
  getTeamIntel,
  searchTeams,
} from '../api';
import type {
  EventTeamsIntelResponse,
  EventTeamRatingItem,
  TeamCompetitionsResponse,
} from '../api';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { Chip, Table, renderCell, type TableColumn } from '../components/ui/primitives';
import styles from './ComparePage.module.css';
import { useExternalSearchSync } from '../hooks/useExternalSearchSync';
import { useLiveRefreshSetting } from '../hooks/useLiveRefreshSetting';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling } from '../hooks/useSingleFlightPolling';
import {
  asRecord,
  CURRENT_SEASON_YEAR,
  metric,
  metricUnit,
  normalizeTeamKeyInput,
  parseNumber,
  pct,
  relativeFromTimestamp,
  summarizeFreshness,
  teamNumberFromTeamKey,
  titleizeKey,
} from './centerUtils';
import { readStoredCenterContext, writeCenterContext } from '../layout/centerContext';
import { resolveTab } from './tabUtils';
const COMPARE_STORAGE_KEYS = {
  event: 'scouting_compare_event_key',
  teams: 'scouting_compare_team_keys',
} as const;

// The Alliance tab moved to Alliance Advisor (one builder, one place); an old
// ?tab=alliance link is forwarded there with the compared teams.
const COMPARE_TABS = ['summary', 'detailed'] as const;
type CompareTab = (typeof COMPARE_TABS)[number];

/* Compare puts twelve figures beside each other, so it stays a table for far
   longer than a five-column board does. useMobileLayout's 1120 is the width the
   surrounding page already switches at, and matching it keeps the two in step. */
const COMPARE_CARD_BREAKPOINT = 1120;

type CompareTeamBundle = {
  team_key: string;
  event_key: string;
  loading: boolean;
  error: string;
  warnings: string[];
  breakdown: TeamIntelBreakdown | null;
  rating: EventTeamRatingItem | null;
  competitions: TeamCompetitionsResponse | null;
  tba_event_status: Record<string, unknown> | null;
  tba_awards_year_count: number | null;
  tba_event_awards_count: number | null;
  last_updated_at: number | null;
};

type CompareMetricHighlight = {
  id: string;
  label: string;
  best_team: string;
  worst_team: string;
  best_value: string;
  worst_value: string;
  spread: string;
};

function normalizeEventKeyInput(raw: string): string {
  return raw.trim().toLowerCase();
}

function readStoredCompareTeamKeys(): string[] {
  const raw = window.localStorage.getItem(COMPARE_STORAGE_KEYS.teams);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((value) => String(value || '').trim().toLowerCase())
      .filter((value, idx, array) => /^frc\d+$/.test(value) && array.indexOf(value) === idx)
      .slice(0, 4);
  } catch {
    return [];
  }
}

function compareTeamLabel(teamKey: string, breakdown: TeamIntelBreakdown | null): string {
  if (breakdown?.team.team_number) return `#${breakdown.team.team_number}`;
  const teamNumber = teamNumberFromTeamKey(teamKey);
  return teamNumber !== null ? `#${teamNumber}` : teamKey.toUpperCase();
}

function emptyBundle(teamKey: string, eventKey: string): CompareTeamBundle {
  return {
    team_key: teamKey,
    event_key: eventKey,
    loading: false,
    error: '',
    warnings: [],
    breakdown: null,
    rating: null,
    competitions: null,
    tba_event_status: null,
    tba_awards_year_count: null,
    tba_event_awards_count: null,
    last_updated_at: null,
  };
}



function competitionsFromIntel(intel: Record<string, unknown>, teamKey: string): TeamCompetitionsResponse | null {
  const competitions = asRecord(intel.competitions);
  if (!competitions) return null;
  const registered = Array.isArray(competitions.registered_events) ? competitions.registered_events : [];
  return {
    ok: true,
    team_key: teamKey,
    event_key: typeof intel.event_key === 'string' ? intel.event_key : null,
    registration_year: parseNumber(competitions.registration_year),
    registered_events_count: parseNumber(competitions.registered_events_count) ?? registered.length,
    registered_events_source:
      typeof competitions.registered_events_source === 'string' ? competitions.registered_events_source : 'intel',
    registered_events: registered as TeamCompetitionsResponse['registered_events'],
  };
}

export function ComparePage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobileLayout = useMobileLayout();
  const pageVisible = usePageVisibility();
  const liveRefreshSec = useLiveRefreshSetting();

  const defaultEventKey = normalizeEventKeyInput(
    searchParams.get('event') ||
      window.localStorage.getItem(COMPARE_STORAGE_KEYS.event) ||
      readStoredCenterContext().eventKey ||
      '',
  );
  const tabParam = searchParams.get('tab');
  const defaultTab = resolveTab(tabParam, COMPARE_TABS, 'summary');

  const [selectedEventKey, setSelectedEventKey] = useState(defaultEventKey);
  const [eventInput, setEventInput] = useState(defaultEventKey);
  const [activeTab, setActiveTab] = useState<CompareTab>(defaultTab);

  const [compareInput, setCompareInput] = useState('');
  const [addingTeam, setAddingTeam] = useState(false);
  const [compareTeamKeys, setCompareTeamKeys] = useState<string[]>(() => readStoredCompareTeamKeys());
  const [teamBundles, setTeamBundles] = useState<Record<string, CompareTeamBundle>>({});
  const teamBundlesRef = useRef(teamBundles);
  const failedContextsRef = useRef(new Set<string>());
  teamBundlesRef.current = teamBundles;

  const [eventTeams, setEventTeams] = useState<EventTeamsIntelResponse | null>(null);
  const [loadingEventTeams, setLoadingEventTeams] = useState(false);

  const [refreshingCompare, setRefreshingCompare] = useState(false);

  const [statusText, setStatusText] = useState('Add teams to compare.');
  const [errorText, setErrorText] = useState('');
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [eventPoolVisibleCount, setEventPoolVisibleCount] = useState(120);
  const [mobileFinderOpen, setMobileFinderOpen] = useState(false);

  // A link here from elsewhere in the app (global search, back/forward)
  // changes the URL under a mounted page.
  const allianceAdvisorPath = useMemo(() => {
    const params = new URLSearchParams();
    if (selectedEventKey) params.set('event', selectedEventKey);
    if (compareTeamKeys.length > 0) params.set('teams', compareTeamKeys.slice(0, 3).join(','));
    const query = params.toString();
    return `/compare/alliance-advisor${query ? `?${query}` : ''}`;
  }, [compareTeamKeys, selectedEventKey]);

  // Old links and bookmarks to Compare's Alliance tab land in Alliance Advisor.
  useEffect(() => {
    if (searchParams.get('tab') === 'alliance') navigate(allianceAdvisorPath, { replace: true });
  }, [allianceAdvisorPath, navigate, searchParams]);

  const urlSync = useExternalSearchSync(searchParams, (params) => {
    const urlEvent = normalizeEventKeyInput(params.get('event') || '');
    const urlTab = params.get('tab');
    if (urlEvent && urlEvent !== selectedEventKey) {
      setSelectedEventKey(urlEvent);
      setEventInput(urlEvent);
    }
    setActiveTab(resolveTab(urlTab, COMPARE_TABS, 'summary'));
  });

  useEffect(() => {
    if (!urlSync.shouldWrite()) return;
    const normalizedEvent = normalizeEventKeyInput(selectedEventKey);
    const next = new URLSearchParams();
    if (normalizedEvent) next.set('event', normalizedEvent);
    if (activeTab !== 'summary') next.set('tab', activeTab);

    if (next.toString() !== searchParams.toString()) {
      urlSync.markWritten(next.toString());
      setSearchParams(next, { replace: true });
    }

    if (normalizedEvent) {
      window.localStorage.setItem(COMPARE_STORAGE_KEYS.event, normalizedEvent);
    } else {
      window.localStorage.removeItem(COMPARE_STORAGE_KEYS.event);
    }

    window.localStorage.setItem(COMPARE_STORAGE_KEYS.teams, JSON.stringify(compareTeamKeys));
    writeCenterContext({ eventKey: normalizedEvent, sourcePath: '/compare' });
  }, [activeTab, compareTeamKeys, searchParams, selectedEventKey, setSearchParams, urlSync]);

  useEffect(() => {
    setEventPoolVisibleCount(120);
  }, [selectedEventKey]);

  useEffect(() => {
    if (!isMobileLayout) setMobileFinderOpen(false);
  }, [isMobileLayout]);

  useEffect(() => {
    setTeamBundles((prev) => {
      const keys = new Set(compareTeamKeys);
      const next: Record<string, CompareTeamBundle> = {};
      for (const key of Object.keys(prev)) {
        if (keys.has(key)) next[key] = prev[key];
      }
      return next;
    });
  }, [compareTeamKeys]);

  useEffect(() => {
    if (selectedEventKey) return;
    setEventTeams(null);
    setLoadingEventTeams(false);
  }, [selectedEventKey]);

  const currentEventKeyRef = useRef(selectedEventKey);
  currentEventKeyRef.current = selectedEventKey;

  const refreshEventTeams = useCallback(async (): Promise<boolean> => {
    if (!selectedEventKey) return true;
    setLoadingEventTeams(true);
    try {
      const payload = await getEventTeamsIntel(selectedEventKey, {
        include_tba: true,
        include_statbotics: false,
        include_season_fallback: true,
        include_rating_details: false,
        include_rating_signals: false,
        auto_heal_ratings: true,
      });
      // Another event was picked meanwhile; don't show this one's teams under it.
      if (currentEventKeyRef.current !== selectedEventKey) return true;
      setEventTeams(payload);
      return true;
    } catch (error) {
      if (currentEventKeyRef.current !== selectedEventKey) return true;
      setEventTeams(null);
      setErrorText((error as Error).message || 'Unable to load event teams for compare.');
      return false;
    } finally {
      setLoadingEventTeams(false);
    }
  }, [selectedEventKey]);

  const { triggerNow: reloadEventTeams } = useSingleFlightPolling({
    enabled: Boolean(selectedEventKey),
    visible: pageVisible,
    intervalMs: Math.max(10, liveRefreshSec) * 1000,
    run: refreshEventTeams,
    backoffMultiplier: 1.6,
    minBackoffMs: Math.max(10, liveRefreshSec) * 1000,
    maxBackoffMs: 60000,
  });

  // The poller keeps its timer when the event changes, so a new event's team pool used to
  // wait for the next tick (up to a minute) while the old pool stayed on screen.
  const lastPolledEventRef = useRef(selectedEventKey);
  useEffect(() => {
    if (lastPolledEventRef.current === selectedEventKey) return;
    lastPolledEventRef.current = selectedEventKey;
    setEventTeams(null);
    if (selectedEventKey) reloadEventTeams('manual');
  }, [reloadEventTeams, selectedEventKey]);

  const loadCompareBundle = useCallback(
    async (teamKeyInput: string, force = false, forceNetwork = false) => {
      const teamKey = teamKeyInput.trim().toLowerCase();
      if (!teamKey) return;
      const contextEventKey = selectedEventKey || '';

      const contextKey = `${contextEventKey}:${teamKey}`;
      const existing = teamBundlesRef.current[teamKey];
      if (!force && existing && existing.last_updated_at && existing.event_key === contextEventKey && !existing.error) {
        return;
      }

      setTeamBundles((prev) => ({
        ...prev,
        [teamKey]: {
          ...(prev[teamKey] || emptyBundle(teamKey, contextEventKey)),
          team_key: teamKey,
          event_key: contextEventKey,
          loading: true,
          error: '',
          warnings: [],
        },
      }));

      const warnings: string[] = [];

      try {
        const intel = await getTeamIntel(
          teamKey,
          {
            event_key: contextEventKey || undefined,
            preferred_year: CURRENT_SEASON_YEAR,
            fallback_year: CURRENT_SEASON_YEAR - 1,
            include_tba: true,
            include_statbotics: false,
            allow_season_fallback: true,
            auto_heal_ratings: true,
            refresh: forceNetwork,
          },
          { bypassCache: forceNetwork },
        );
        const team = asRecord(intel.team);
        const teamNumber = parseNumber(team?.team_number) ?? teamNumberFromTeamKey(teamKey) ?? 0;
        const nickname = typeof team?.nickname === 'string' ? team.nickname : null;
        const breakdown = breakdownFromIntel(intel as unknown as Record<string, unknown>, teamKey);
        const rating = ratingFromIntel(intel as unknown as Record<string, unknown>, teamKey, teamNumber, nickname);
        const competitions = competitionsFromIntel(intel as unknown as Record<string, unknown>, teamKey);
        const tba = asRecord(intel.tba);
        const tbaEventStatus = asRecord(tba?.event_status);
        const tbaAwards = Array.isArray(tba?.awards) ? tba.awards.map((row) => asRecord(row)).filter(Boolean) : [];
        const intelWarnings = Array.isArray(intel.warnings)
          ? intel.warnings.map((warning) => String(warning || '').trim()).filter(Boolean)
          : [];
        warnings.push(...intelWarnings);

        const tbaAwardsYearCount = tbaAwards.length;
        const tbaEventAwardsCount = contextEventKey
          ? tbaAwards.filter((award) => typeof award?.event_key === 'string' && award.event_key.toLowerCase() === contextEventKey).length
          : null;

        setTeamBundles((prev) => ({
          ...prev,
          [teamKey]: {
            team_key: teamKey,
            event_key: contextEventKey,
            loading: false,
            error: '',
            warnings: Array.from(new Set(warnings)),
            breakdown,
            rating,
            competitions,
            tba_event_status: tbaEventStatus,
            tba_awards_year_count: tbaAwardsYearCount,
            tba_event_awards_count: tbaEventAwardsCount,
            last_updated_at: Date.now(),
          },
        }));
        failedContextsRef.current.delete(contextKey);
      } catch (error) {
        failedContextsRef.current.add(contextKey);
        setTeamBundles((prev) => ({
          ...prev,
          [teamKey]: {
            ...(prev[teamKey] || emptyBundle(teamKey, contextEventKey)),
            team_key: teamKey,
            event_key: contextEventKey,
            loading: false,
            error: (error as Error).message || 'Compare load failed.',
            last_updated_at: Date.now(),
          },
        }));
      }
    },
    [selectedEventKey],
  );

  useEffect(() => {
    for (const teamKey of compareTeamKeys) {
      const bundle = teamBundles[teamKey];
      const contextEventKey = selectedEventKey || '';
      const contextKey = `${contextEventKey}:${teamKey}`;
      const needsLoad = !bundle || bundle.event_key !== contextEventKey || (!bundle.loading && !bundle.breakdown);
      // A failed context waits for the row's explicit Retry action instead of
      // retrying in a tight loop on bad wifi.
      if (needsLoad && !bundle?.loading && !failedContextsRef.current.has(contextKey)) {
        void loadCompareBundle(teamKey, true);
      }
    }
  }, [compareTeamKeys, loadCompareBundle, selectedEventKey, teamBundles]);

  const eventTeamPool = useMemo(() => {
    const teams = Array.isArray(eventTeams?.teams) ? eventTeams.teams : [];
    return teams
      .map((entry) => {
        const row = asRecord(entry);
        const analysis = asRecord(row?.analysis);
        const rating = asRecord(row?.rating);
        return {
          team_key: String(row?.team_key || '').toLowerCase(),
          team_number: parseNumber(row?.team_number) ?? 0,
          nickname: typeof row?.nickname === 'string' ? row.nickname : null,
          analyzed: parseNumber(analysis?.event_matches_analyzed) ?? 0,
          rating_0_100: parseNumber(rating?.rating_0_100),
        };
      })
      .filter((row) => row.team_key.length > 0)
      .sort((a, b) => {
        if (b.analyzed !== a.analyzed) return b.analyzed - a.analyzed;
        return a.team_number - b.team_number;
      });
  }, [eventTeams]);

  const compareEventOptions = useMemo(() => {
    const byKey = new Map<string, { event_key: string; name: string; start_date: string | null }>();
    const currentEvent = normalizeEventKeyInput(selectedEventKey);
    if (currentEvent) {
      byKey.set(currentEvent, {
        event_key: currentEvent,
        name: eventTeams?.event_name || currentEvent,
        start_date: null,
      });
    }

    for (const bundle of Object.values(teamBundles)) {
      const events = bundle.competitions?.registered_events || [];
      for (const event of events) {
        const eventKey = normalizeEventKeyInput(String(event.event_key || ''));
        if (!eventKey) continue;
        if (!byKey.has(eventKey)) {
          byKey.set(eventKey, {
            event_key: eventKey,
            name: event.name || eventKey,
            start_date: event.start_date || null,
          });
        }
      }
    }

    return [...byKey.values()].sort((a, b) => {
      const aDate = a.start_date || '';
      const bDate = b.start_date || '';
      if (aDate !== bDate) return aDate.localeCompare(bDate);
      return a.event_key.localeCompare(b.event_key);
    });
  }, [eventTeams?.event_name, selectedEventKey, teamBundles]);

  useEffect(() => {
    if (compareEventOptions.length === 0) return;
    const normalized = normalizeEventKeyInput(selectedEventKey);
    const valid = compareEventOptions.some((item) => item.event_key === normalized);
    if (!valid) {
      const fallback = compareEventOptions[0]?.event_key || '';
      setSelectedEventKey(fallback);
      setEventInput(fallback);
    }
  }, [compareEventOptions, selectedEventKey]);

  const eventTeamKeySet = useMemo(() => {
    const set = new Set<string>();
    for (const team of eventTeamPool) {
      set.add(team.team_key.toLowerCase());
    }
    return set;
  }, [eventTeamPool]);

  const compareRows = useMemo(() => {
    return compareTeamKeys.map((teamKey) => {
      const bundle = teamBundles[teamKey] || emptyBundle(teamKey, selectedEventKey || '');
      return {
        team_key: teamKey,
        loading: bundle.loading,
        error: bundle.error,
        warnings: bundle.warnings,
        breakdown: bundle.breakdown,
        rating: bundle.rating,
        competitions: bundle.competitions,
        tba_event_status: bundle.tba_event_status,
        tba_awards_year_count: bundle.tba_awards_year_count,
        tba_event_awards_count: bundle.tba_event_awards_count,
        last_updated_at: bundle.last_updated_at,
      };
    });
  }, [compareTeamKeys, selectedEventKey, teamBundles]);

  type CompareRow = (typeof compareRows)[number];

  const summaryColumns: TableColumn<CompareRow>[] = [
    {
      key: 'team',
      // Eleven numeric columns will happily squeeze this one to nothing, and a
      // nickname broken across five lines stops identifying the team.
      width: '11rem',
      label: 'Team',
      render: (row) => (
        <button type="button" className="center-inline-link" onClick={() => openTeamCenter(row.team_key)}>
          {row.breakdown?.team.nickname || row.team_key}
        </button>
      ),
    },
    { key: 'rating', label: 'Rating', numeric: true, render: (row) => metric(row.rating?.rating_0_100, 1) },
    { key: 'confidence', label: 'Confidence', numeric: true, render: (row) => pct(row.rating?.confidence_0_1, 1) },
    { key: 'robot', label: 'Robot', numeric: true, render: (row) => metric(row.rating?.robot_level_0_100, 1) },
    {
      key: 'rank',
      label: 'TBA Rank',
      numeric: true,
      render: (row) => {
        const rank = parseNumber(asRecord(asRecord(asRecord(row.tba_event_status)?.qual)?.ranking)?.rank);
        return rank !== null ? `#${rank}` : 'N/A';
      },
    },
    { key: 'fuel', label: 'Fuel/active min', numeric: true, render: (row) => metric(row.breakdown?.averages?.fuel_scoring_rate, 1) },
    { key: 'cycle', label: 'Cycle', numeric: true, render: (row) => metric(row.breakdown?.averages?.cycle_time_sec, 2) },
    { key: 'auto', label: 'Auto', numeric: true, render: (row) => metric(row.breakdown?.averages?.auto_contribution, 2) },
    { key: 'climb', label: 'Climb', numeric: true, render: (row) => pct(row.breakdown?.averages?.climb_success_prob, 1) },
    { key: 'reliability', label: 'Reliability', numeric: true, render: (row) => pct(row.breakdown?.averages?.reliability_score, 1) },
    { key: 'matches', label: 'Matches', numeric: true, render: (row) => row.breakdown?.matches_analyzed ?? 'N/A' },
    { key: 'freshness', label: 'Freshness', render: (row) => summarizeFreshness(row.breakdown?.data_freshness || null).label },
  ];

  /* Below the breakpoint a compare row becomes a compact metric grid rather
     than one stacked card per figure: eight short numbers fit on a scouting
     phone, twelve labelled lines do not. The cells still come out of the
     columns above through renderCell, so there is one definition of how a
     compare figure is formatted — the second copy that used to live here is
     what let the mobile view quietly drift two metrics behind the desktop one. */
  const CARD_METRIC_KEYS = ['rating', 'robot', 'rank', 'climb', 'fuel', 'cycle', 'auto', 'reliability'];
  const renderSummaryCards = (rows: CompareRow[]) => (
    <div className={styles.mobileList}>
      {rows.map((row) => {
        const freshness = summarizeFreshness(row.breakdown?.data_freshness || null);
        return (
          <article key={`compare-mobile-${row.team_key}`} className={styles.mobileCard}>
            <div className={styles.mobileHead}>
              {renderCell(summaryColumns[0], row)}
              <Chip size="sm" tone={freshness.state === 'stale' ? 'warn' : 'neutral'}>
                {freshness.label}
              </Chip>
            </div>
            <div className={styles.mobileMetrics}>
              {summaryColumns
                .filter((column) => CARD_METRIC_KEYS.includes(column.key))
                .map((column) => (
                  <span key={column.key}>
                    {column.label} <strong>{renderCell(column, row)}</strong>
                  </span>
                ))}
            </div>
          </article>
        );
      })}
    </div>
  );

  const metricHighlights = useMemo(() => {
    const definitions = [
      {
        id: 'rating',
        label: 'Overall Rating',
        better: 'high' as const,
        getValue: (row: (typeof compareRows)[number]) => row.rating?.rating_0_100 ?? null,
        format: (value: number) => metric(value, 1),
      },
      {
        id: 'fuel',
        label: 'Fuel per active-hub min',
        better: 'high' as const,
        getValue: (row: (typeof compareRows)[number]) => row.breakdown?.averages?.fuel_scoring_rate ?? null,
        format: (value: number) => metric(value, 2),
      },
      {
        id: 'cycle',
        label: 'Cycle Time (sec)',
        better: 'low' as const,
        getValue: (row: (typeof compareRows)[number]) => row.breakdown?.averages?.cycle_time_sec ?? null,
        format: (value: number) => metric(value, 2),
      },
      {
        id: 'climb',
        label: 'Climb Success',
        better: 'high' as const,
        getValue: (row: (typeof compareRows)[number]) => {
          const value = row.breakdown?.averages?.climb_success_prob;
          return value === null || value === undefined ? null : value * 100;
        },
        format: (value: number) => `${metric(value, 1)}%`,
      },
      {
        id: 'reliability',
        label: 'Reliability',
        better: 'high' as const,
        getValue: (row: (typeof compareRows)[number]) => {
          const value = row.breakdown?.averages?.reliability_score;
          return value === null || value === undefined ? null : value * 100;
        },
        format: (value: number) => `${metric(value, 1)}%`,
      },
    ];

    const highlights: CompareMetricHighlight[] = [];

    for (const definition of definitions) {
      const sampled = compareRows
        .map((row) => ({ row, value: definition.getValue(row) }))
        .filter((item): item is { row: (typeof compareRows)[number]; value: number } => item.value !== null);

      if (sampled.length < 2) continue;

      const ordered = [...sampled].sort((a, b) =>
        definition.better === 'high' ? b.value - a.value : a.value - b.value,
      );

      const best = ordered[0];
      const worst = ordered[ordered.length - 1];
      if (!best || !worst || best.value === worst.value) continue;

      highlights.push({
        id: definition.id,
        label: definition.label,
        best_team: compareTeamLabel(best.row.team_key, best.row.breakdown),
        worst_team: compareTeamLabel(worst.row.team_key, worst.row.breakdown),
        best_value: definition.format(best.value),
        worst_value: definition.format(worst.value),
        spread: definition.format(Math.abs(best.value - worst.value)),
      });
    }

    return highlights;
  }, [compareRows]);

  const visibleEventTeamPool = useMemo(
    () => eventTeamPool.slice(0, Math.max(1, eventPoolVisibleCount)),
    [eventPoolVisibleCount, eventTeamPool],
  );

  function openEventContext() {
    const normalized = normalizeEventKeyInput(eventInput);
    setSelectedEventKey(normalized);
    setEventInput(normalized);
    setErrorText('');
    setStatusText(normalized ? `Event: ${normalized}.` : 'Event cleared.');
    if (isMobileLayout) setMobileFinderOpen(false);
  }

  function useActiveEventContext() {
    const active = readStoredCenterContext().eventKey;
    setEventInput(active);
    setSelectedEventKey(active);
    setStatusText(active ? `Event: ${active}.` : 'No active event found.');
    if (active && isMobileLayout) setMobileFinderOpen(false);
  }

  function removeCompareTeam(teamKey: string) {
    setCompareTeamKeys((prev) => prev.filter((value) => value !== teamKey));
  }

  function addCompareTeam(teamKeyInput: string): boolean {
    const normalized = normalizeTeamKeyInput(teamKeyInput || '');
    if (!normalized) {
      setErrorText('Enter a valid team key or number (example: frc118 or 118).');
      return false;
    }

    if (compareTeamKeys.includes(normalized)) {
      setStatusText(`${normalized} already added.`);
      return false;
    }

    if (compareTeamKeys.length >= 4) {
      setErrorText('Compare supports up to 4 teams at once.');
      return false;
    }

    setCompareTeamKeys((prev) => [...prev, normalized]);
    setStatusText(`Added ${normalized}.`);
    setErrorText('');
    setActiveTab('summary');

    if (selectedEventKey && eventTeams && !eventTeamKeySet.has(normalized)) {
      setStatusText(`Added ${normalized} (not in ${selectedEventKey} pool).`);
    }

    return true;
  }

  async function handleAddTeam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = compareInput.trim();
    if (!query || addingTeam) return;

    setAddingTeam(true);
    setErrorText('');

    try {
      if (addCompareTeam(query)) {
        setCompareInput('');
        return;
      }

      const payload = await searchTeams(query, 40);
      const pool = payload.teams || [];
      const filtered = selectedEventKey
        ? pool.filter((team) => eventTeamKeySet.has(team.team_key.toLowerCase()))
        : pool;
      const selected = (filtered[0] || pool[0])?.team_key?.toLowerCase();

      if (!selected) {
        setErrorText(`No team found for "${query}".`);
        return;
      }

      if (addCompareTeam(selected)) {
        setCompareInput('');
      }
    } catch (error) {
      setErrorText((error as Error).message || 'Team search failed.');
    } finally {
      setAddingTeam(false);
    }
  }

  async function refreshCompare() {
    if (refreshingCompare) return;
    if (compareTeamKeys.length === 0) {
      setStatusText('Add a team first.');
      return;
    }

    setRefreshingCompare(true);
    setStatusText('Refreshing...');
    try {
      await Promise.all(compareTeamKeys.map((teamKey) => loadCompareBundle(teamKey, true, true)));
      setLastUpdatedAt(Date.now());
      setStatusText(`Refreshed ${compareTeamKeys.length} team(s).`);
    } catch (error) {
      setErrorText((error as Error).message || 'Compare refresh failed.');
      setStatusText('Refresh failed.');
    } finally {
      setRefreshingCompare(false);
    }
  }

  function clearCompare() {
    setCompareTeamKeys([]);
    setTeamBundles({});
    setCompareInput('');
    setStatusText('Cleared.');
  }

  function openTeamCenter(teamKey: string) {
    const params = new URLSearchParams();
    params.set('team', teamKey.toLowerCase());
    if (selectedEventKey) params.set('event', selectedEventKey);
    navigate(`/team-center?${params.toString()}`);
  }


  return (
    <>
    <PageViewBar items={COMPARE_VIEWS} />
    <div className={`compare-layout-grid mobile-finder-layout ${isMobileLayout && mobileFinderOpen ? 'mobile-finder-open' : ''}`.trim()}>
      {isMobileLayout ? (
        <SegmentedTabs
          className="mobile-view-toggle"
          itemClassName="mobile-view-toggle-btn"
          ariaLabel="Compare mobile view switch"
          value={mobileFinderOpen ? 'controls' : 'compare'}
          onChange={(next) => setMobileFinderOpen(next === 'controls')}
          items={[
            { value: 'controls', label: 'Controls' },
            { value: 'compare', label: 'Compare View' },
          ]}
        />
      ) : null}
      <aside className="center-sidebar">
        <SurfaceCard
          title="Compare Controls"
          className="compare-controls-card"
        >
          <label className="center-label" htmlFor="compare-event-input">
            Event Context
          </label>
          <div className="center-input-row">
            <select
              id="compare-event-input"
              value={eventInput}
              onChange={(event) => setEventInput(normalizeEventKeyInput(event.target.value))}
              className="center-input"
            >
              <option value="">Auto-select from compared team events</option>
              {compareEventOptions.map((event) => (
                <option key={`compare-event-option-${event.event_key}`} value={event.event_key}>
                  {event.name} ({event.event_key})
                </option>
              ))}
            </select>
            <button type="button" className="center-btn" onClick={openEventContext}>
              Open
            </button>
          </div>
          <div className="center-actions-row">
            <button type="button" className="center-btn ghost" onClick={useActiveEventContext}>
              Use Active Event
            </button>
          </div>

          <form className="center-input-row" onSubmit={handleAddTeam}>
            <input
              value={compareInput}
              onChange={(event) => setCompareInput(event.target.value)}
              placeholder="Add team to compare"
              aria-label="Add compare team"
            />
            <button type="submit" className="center-btn" disabled={addingTeam}>
              {addingTeam ? 'Adding...' : 'Add'}
            </button>
          </form>

          <div className="center-actions-row">
            <button
              type="button"
              className="center-btn ghost"
              onClick={() => void refreshCompare()}
              disabled={refreshingCompare}
            >
              {refreshingCompare ? 'Refreshing...' : 'Refresh Compare'}
            </button>
            <button type="button" className="center-btn ghost" onClick={clearCompare}>
              Clear
            </button>
            <Link className="center-btn ghost" to={selectedEventKey ? `/events?event=${selectedEventKey}` : '/events'}>
              Events
            </Link>
          </div>

          <div className="center-status-row">
            <span className="center-chip">{compareTeamKeys.length}/4 teams</span>
            <span className="center-chip">{eventTeams?.teams_count ?? 0} pool</span>
            <span className="center-chip">Updated {relativeFromTimestamp(lastUpdatedAt)}</span>
          </div>

          {errorText ? <p className="center-callout danger">{errorText}</p> : null}
          <p className="center-callout muted">{statusText}</p>
        </SurfaceCard>

        <SurfaceCard
          title="Event Team Pool"
          subtitle={
            selectedEventKey
              ? 'Teams at this event. Tap one to add it.'
              : 'Pick an event key to load a constrained team pool.'
          }
          right={<span className="center-chip">{loadingEventTeams ? 'Loading...' : `${eventTeamPool.length} team${eventTeamPool.length === 1 ? '' : 's'}`}</span>}
          className="compare-pool-card"
        >
          {!selectedEventKey ? (
            <p className="center-callout muted">No event selected yet.</p>
          ) : (
            <div className="center-list-scroll" role="list" aria-label="Event team pool">
              {eventTeamPool.length === 0 && !loadingEventTeams ? (
                <p className="center-callout muted">No teams loaded for this event yet.</p>
              ) : null}
              {visibleEventTeamPool.map((team) => {
                const normalized = team.team_key.toLowerCase();
                const selected = compareTeamKeys.includes(normalized);
                return (
                  <button
                    type="button"
                    key={`compare-pool-${team.team_key}`}
                    className={`event-picker-item ${selected ? 'active' : ''}`.trim()}
                    onClick={() => {
                      if (selected) {
                        removeCompareTeam(normalized);
                        return;
                      }
                      addCompareTeam(normalized);
                    }}
                    >
                      <strong>
                        #{team.team_number} {team.nickname || team.team_key}
                      </strong>
                      <small>
                        {team.team_key} · scouted {team.analyzed} · rating {metric(team.rating_0_100, 1)}
                      </small>
                    </button>
                  );
                })}
            </div>
          )}
          {eventTeamPool.length > visibleEventTeamPool.length ? (
            <div className="center-actions-row">
              <button
                type="button"
                className="center-btn ghost"
                onClick={() => setEventPoolVisibleCount((prev) => prev + 120)}
              >
                Show More Event Teams ({visibleEventTeamPool.length}/{eventTeamPool.length})
              </button>
            </div>
          ) : null}
        </SurfaceCard>
      </aside>

      <section className="center-main">
        <SurfaceCard
          title="Compare Center"
          className="compare-header-card"
        >
          <div className="center-tabs-header">
            <SegmentedTabs
              className="center-tabs"
              itemClassName="center-tab-btn"
              ariaLabel="Compare tabs"
              value={activeTab}
              onChange={setActiveTab}
              items={COMPARE_TABS.map((tab) => ({
                value: tab,
                label: titleizeKey(tab),
              }))}
            />
          </div>

          {compareTeamKeys.length > 0 ? (
            <div className={styles.chipRow}>
              {compareTeamKeys.map((teamKey) => (
                <Chip
                  key={`compare-chip-${teamKey}`}
                  onRemove={() => removeCompareTeam(teamKey)}
                  removeLabel={`Remove ${teamKey}`}
                >
                  {/* The accessible name here is the team's nickname, which is
                      the right name for a person to hear and the wrong one for
                      a snapshot to pin. */}
                  <button
                    type="button"
                    className={styles.chipButton}
                    data-guard-data-label=""
                    onClick={() => openTeamCenter(teamKey)}
                  >
                    {compareTeamLabel(teamKey, teamBundles[teamKey]?.breakdown || null)}
                  </button>
                </Chip>
              ))}
            </div>
          ) : (
            <div className="center-callout muted">
              <p>Pick two or more teams to compare them side by side.</p>
              {isMobileLayout ? (
                <button type="button" className="center-btn" onClick={() => setMobileFinderOpen(true)}>
                  Choose teams
                </button>
              ) : null}
            </div>
          )}
          {compareTeamKeys.length >= 2 && selectedEventKey ? (
            <p className="center-callout muted">
              Try these teams as an alliance in <Link to={allianceAdvisorPath}>Alliance Advisor</Link>.
            </p>
          ) : null}
        </SurfaceCard>

        {activeTab === 'summary' && compareTeamKeys.length > 0 ? (
            <SurfaceCard title="Summary" className="compare-summary-card" compactable>
            {metricHighlights.length > 0 ? (
              <div className="compare-highlights-grid">
                {metricHighlights.map((highlight) => (
                  <article
                    key={`compare-highlight-${highlight.id}`}
                    className={`compare-highlight-card tone-${highlight.id}`.trim()}
                  >
                    <h4>{highlight.label}</h4>
                    <p>
                      <strong>{highlight.best_team}</strong> ({highlight.best_value})
                    </p>
                    <p>
                      <strong>{highlight.worst_team}</strong> ({highlight.worst_value})
                    </p>
                    <small>Spread: {highlight.spread}</small>
                  </article>
                ))}
              </div>
            ) : (
              <p className="center-callout muted">Add at least 2 teams for highlights.</p>
            )}

            <Table
              columns={summaryColumns}
              rows={compareRows}
              rowKey={(row) => `compare-summary-row-${row.team_key}`}
              cardBreakpoint={COMPARE_CARD_BREAKPOINT}
              renderCards={renderSummaryCards}
              empty="Pick teams to compare."
            />
            </SurfaceCard>

        ) : null}

        {activeTab === 'detailed' ? (
            <SurfaceCard
              title="Deep Compare"
              className="compare-deep-shell"
              compactable
            >
            <div className="compare-deep-grid">
              {compareRows.map((row) => (
                <article key={`compare-deep-${row.team_key}`} className="compare-deep-card">
                  {(() => {
                    const freshness = summarizeFreshness(row.breakdown?.data_freshness || null);
                    return (
                      <div className="center-status-row compact">
                        <span className={`center-chip freshness ${freshness.state}`}>Data: {freshness.label}</span>
                        <span className="center-chip">Matches: {row.breakdown?.matches_analyzed ?? 0}</span>
                      </div>
                    );
                  })()}
                  <header>
                    <h4>{row.breakdown?.team.nickname || row.team_key}</h4>
                    <small>Updated {relativeFromTimestamp(row.last_updated_at)}</small>
                  </header>

                  {row.loading ? <p className="center-callout muted">Loading bundle...</p> : null}
                  {row.error ? <p className="center-callout danger">{row.error}</p> : null}

                  <div className="center-kpi-grid">
                    <div className="center-kpi-card">
                      <span>Overall Rating</span>
                      <strong>{metric(row.rating?.rating_0_100, 1)}</strong>
                    </div>
                    <div className="center-kpi-card">
                      <span>Model Confidence</span>
                      <strong>{pct(row.rating?.confidence_0_1, 1)}</strong>
                    </div>
                    <div className="center-kpi-card">
                      <span>Fuel / active min</span>
                      <strong>{metric(row.breakdown?.averages?.fuel_scoring_rate, 1)}</strong>
                    </div>
                    <div className="center-kpi-card">
                      <span>Cycle Time</span>
                      <strong>{metricUnit(row.breakdown?.averages?.cycle_time_sec, 2, 's')}</strong>
                    </div>
                    <div className="center-kpi-card">
                      <span>Auto Contribution</span>
                      <strong>{metric(row.breakdown?.averages?.auto_contribution, 2)}</strong>
                    </div>
                    <div className="center-kpi-card">
                      <span>Climb Success</span>
                      <strong>{pct(row.breakdown?.averages?.climb_success_prob, 1)}</strong>
                    </div>
                  </div>

                  <div className="compare-pros-cons-grid">
                    <div className="compare-signal-list">
                      <h5>Top Pros</h5>
                      {(row.rating?.pros || []).slice(0, 4).map((signal, idx) => (
                        <p key={`compare-pro-${row.team_key}-${idx}`}>
                          {signal.label} ({metric(signal.metric_value, 2)} · {metric(signal.percentile, 1)}%)
                        </p>
                      ))}
                      {(row.rating?.pros || []).length === 0 ? <p className="center-muted">No pros loaded.</p> : null}
                    </div>

                    <div className="compare-signal-list">
                      <h5>Top Cons</h5>
                      {(row.rating?.cons || []).slice(0, 4).map((signal, idx) => (
                        <p key={`compare-con-${row.team_key}-${idx}`}>
                          {signal.label} ({metric(signal.metric_value, 2)} · {metric(signal.percentile, 1)}%)
                        </p>
                      ))}
                      {(row.rating?.cons || []).length === 0 ? <p className="center-muted">No cons loaded.</p> : null}
                    </div>
                  </div>

                  {row.warnings.length > 0 ? (
                    <div className="center-stack-gap">
                      {row.warnings.map((warning, idx) => (
                        <p key={`compare-warning-${row.team_key}-${idx}`} className="center-callout warning">
                          {warning}
                        </p>
                      ))}
                    </div>
                  ) : null}

                  {(() => {
                    const freshness = summarizeFreshness(row.breakdown?.data_freshness || null);
                    if (!freshness.detail) return null;
                    return (
                      <p className={`center-callout ${freshness.state === 'stale' ? 'warning' : 'muted'}`}>
                        {freshness.detail}
                      </p>
                    );
                  })()}

                  <p className="center-callout muted">
                    Model signals: robot {metric(row.rating?.robot_level_0_100, 1)} · driver{' '}
                    {metric(row.rating?.driver_skill_0_100, 1)}
                  </p>

                  <div className="center-kpi-grid">
                    <div className="center-kpi-card">
                      <span>TBA Rank</span>
                      <strong>
                        {(() => {
                          const status = asRecord(row.tba_event_status);
                          const qual = asRecord(status?.qual);
                          const ranking = asRecord(qual?.ranking);
                          const rank = parseNumber(ranking?.rank);
                          return rank !== null ? `#${rank}` : 'N/A';
                        })()}
                      </strong>
                    </div>
                    <div className="center-kpi-card">
                      <span>TBA Record</span>
                      <strong>
                        {(() => {
                          const status = asRecord(row.tba_event_status);
                          const qual = asRecord(status?.qual);
                          const ranking = asRecord(qual?.ranking);
                          const record = asRecord(ranking?.record);
                          if (!record) return 'N/A';
                          const wins = parseNumber(record.wins) ?? 0;
                          const losses = parseNumber(record.losses) ?? 0;
                          const ties = parseNumber(record.ties) ?? 0;
                          return `${wins}-${losses}-${ties}`;
                        })()}
                      </strong>
                    </div>
                    <div className="center-kpi-card">
                      <span>TBA Event Awards</span>
                      <strong>{row.tba_event_awards_count ?? 'N/A'}</strong>
                    </div>
                    <div className="center-kpi-card">
                      <span>TBA Season Awards</span>
                      <strong>{row.tba_awards_year_count ?? 'N/A'}</strong>
                    </div>
                  </div>



                  <div className="center-actions-row">
                    <button type="button" className="center-btn ghost" title={`Open Team Center for ${row.team_key}`} onClick={() => openTeamCenter(row.team_key)}>
                      Team Details
                    </button>
                    <button
                      type="button"
                      className="center-btn ghost"
                      onClick={() => void loadCompareBundle(row.team_key, true, true)}
                      disabled={row.loading}
                    >
                      {row.error ? 'Retry' : 'Refresh Team'}
                    </button>
                  </div>
                </article>
              ))}
            </div>
            </SurfaceCard>

        ) : null}

      </section>
    </div>
    </>
  );
}
