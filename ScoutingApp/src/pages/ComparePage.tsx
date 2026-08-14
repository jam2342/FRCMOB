import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { PageViewBar } from '../components/PageViewBar';
import { COMPARE_VIEWS } from '../components/pageViewBarConfig';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import {
  getEventTeamsIntel,
  getTeamIntel,
  getTheoreticalAlliance,
  searchTeams,
} from '../api';
import type {
  EventTeamsIntelResponse,
  EventTeamRatingItem,
  TeamBreakdownResponse,
  TeamCompetitionsResponse,
  TheoreticalAllianceResponse,
} from '../api';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
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
const COMPARE_STORAGE_KEYS = {
  event: 'scouting_compare_event_key',
  teams: 'scouting_compare_team_keys',
} as const;

const COMPARE_TABS = ['summary', 'detailed', 'alliance'] as const;
type CompareTab = (typeof COMPARE_TABS)[number];

type CompareTeamBundle = {
  team_key: string;
  event_key: string;
  loading: boolean;
  error: string;
  warnings: string[];
  breakdown: TeamBreakdownResponse | null;
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

function isCompareTab(value: string | null): value is CompareTab {
  return value === 'summary' || value === 'detailed' || value === 'alliance';
}

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

function compareTeamLabel(teamKey: string, breakdown: TeamBreakdownResponse | null): string {
  if (breakdown?.team.team_number) return `#${breakdown.team.team_number}`;
  const teamNumber = teamNumberFromTeamKey(teamKey);
  return teamNumber !== null ? `#${teamNumber}` : teamKey.toUpperCase();
}

function normalizedTheoreticalWeights(
  compatibilityWeight: number,
  prosWeight: number,
  consWeight: number,
): { compatibility: number; pros: number; cons: number } {
  const sum = Math.max(0, compatibilityWeight) + Math.max(0, prosWeight) + Math.max(0, consWeight);
  if (sum <= 0) return { compatibility: 0.6, pros: 0.25, cons: 0.15 };
  return {
    compatibility: Math.max(0, compatibilityWeight) / sum,
    pros: Math.max(0, prosWeight) / sum,
    cons: Math.max(0, consWeight) / sum,
  };
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

function breakdownFromIntel(intel: Record<string, unknown>, teamKey: string): TeamBreakdownResponse | null {
  const analysis = asRecord(intel.analysis);
  const team = asRecord(intel.team);
  if (!analysis || !team) return null;
  const averages = asRecord(analysis.averages);
  const climbSourcesRaw = asRecord(analysis.climb_sources);
  const climbVideoRaw = asRecord(climbSourcesRaw?.video_only);
  const climbOfficialRaw = asRecord(climbSourcesRaw?.official_score_breakdown);
  const climbSources: TeamBreakdownResponse['climb_sources'] | undefined = climbSourcesRaw
    ? {
        overall_climb_success_prob: parseNumber(climbSourcesRaw.overall_climb_success_prob),
        video_only: {
          source: typeof climbVideoRaw?.source === 'string' ? climbVideoRaw.source : 'video_analyzed',
          climb_success_prob: parseNumber(climbVideoRaw?.climb_success_prob),
          matches_considered: Math.max(0, Math.floor(parseNumber(climbVideoRaw?.matches_considered) ?? 0)),
          matches_with_signal: Math.max(0, Math.floor(parseNumber(climbVideoRaw?.matches_with_signal) ?? 0)),
        },
        official_score_breakdown: {
          source:
            typeof climbOfficialRaw?.source === 'string'
              ? climbOfficialRaw.source
              : 'official_score_breakdown',
          climb_success_prob: parseNumber(climbOfficialRaw?.climb_success_prob),
          matches_considered: Math.max(0, Math.floor(parseNumber(climbOfficialRaw?.matches_considered) ?? 0)),
          matches_with_signal: Math.max(0, Math.floor(parseNumber(climbOfficialRaw?.matches_with_signal) ?? 0)),
        },
      }
    : undefined;
  return {
    ok: true,
    team: {
      team_key: String(team.team_key || teamKey).toLowerCase(),
      team_number: parseNumber(team.team_number) ?? teamNumberFromTeamKey(teamKey) ?? 0,
      nickname: typeof team.nickname === 'string' ? team.nickname : null,
    },
    event_key: typeof intel.event_key === 'string' ? intel.event_key : null,
    season_scope: asRecord(analysis.season_scope) as TeamBreakdownResponse['season_scope'],
    data_freshness: asRecord(analysis.data_freshness) as TeamBreakdownResponse['data_freshness'],
    matches_analyzed: parseNumber(analysis.matches_analyzed) ?? 0,
    averages: averages
      ? {
          fuel_scoring_rate: parseNumber(averages.fuel_scoring_rate),
          cycle_time_sec: parseNumber(averages.cycle_time_sec),
          auto_contribution: parseNumber(averages.auto_contribution),
          climb_success_prob: parseNumber(averages.climb_success_prob),
          defensive_engagement_sec: parseNumber(averages.defensive_engagement_sec),
          reliability_score: parseNumber(averages.reliability_score),
        }
      : null,
    climb_sources: climbSources,
    metric_coverage: (asRecord(analysis.metric_coverage) || {}) as TeamBreakdownResponse['metric_coverage'],
    active_perimeter_type:
      analysis.active_perimeter_type === 'welded' || analysis.active_perimeter_type === 'andymark'
        ? analysis.active_perimeter_type
        : null,
    perimeter_types: Array.isArray(analysis.perimeter_types)
      ? analysis.perimeter_types.filter((value): value is 'welded' | 'andymark' => value === 'welded' || value === 'andymark')
      : [],
    perimeter_sources: Array.isArray(analysis.perimeter_sources)
      ? analysis.perimeter_sources.map((value) => String(value))
      : [],
    analysis_versions: Array.isArray(analysis.analysis_versions)
      ? analysis.analysis_versions.map((value) => String(value))
      : [],
    event_type_counts: Array.isArray(analysis.event_type_counts)
      ? (analysis.event_type_counts as TeamBreakdownResponse['event_type_counts'])
      : [],
    zone_time_sec: (asRecord(analysis.zone_time_sec) || {}) as Record<string, number>,
    recent_matches: Array.isArray(analysis.recent_matches)
      ? (analysis.recent_matches as TeamBreakdownResponse['recent_matches'])
      : [],
    recent_events: [],
    recent_track_points: [],
    run_ids: Array.isArray(analysis.run_ids)
      ? analysis.run_ids.map((value) => parseNumber(value)).filter((value): value is number => value !== null)
      : [],
  };
}

function ratingFromIntel(
  intel: Record<string, unknown>,
  teamKey: string,
  teamNumber: number | null,
  nickname: string | null,
): EventTeamRatingItem | null {
  const rating = asRecord(intel.rating);
  if (!rating || !rating.available) return null;
  const subscores = asRecord(rating.subscores) || {};
  return {
    event_key: typeof rating.context_event_key === 'string' ? rating.context_event_key : (intel.event_key as string) || '',
    team_key: teamKey,
    team_number: teamNumber,
    nickname,
    rating_0_100: parseNumber(rating.rating_0_100) ?? 50,
    confidence_0_1: parseNumber(rating.confidence_0_1) ?? 0,
    robot_level_0_100: parseNumber(rating.robot_level_0_100) ?? 50,
    driver_skill_0_100: parseNumber(rating.driver_skill_0_100) ?? 50,
    subscores: {
      results_anchor: parseNumber(subscores.results_anchor) ?? 50,
      throughput: parseNumber(subscores.throughput) ?? 50,
      shift_productivity: parseNumber(subscores.shift_productivity) ?? 50,
      capacity_utilization: parseNumber(subscores.capacity_utilization) ?? 50,
      endgame: parseNumber(subscores.endgame) ?? 50,
      auto_contribution: parseNumber(subscores.auto_contribution),
      manual_points_impact: parseNumber(subscores.manual_points_impact),
      rp_contribution: parseNumber(subscores.rp_contribution),
      defense_presence: parseNumber(subscores.defense_presence),
      consistency: parseNumber(subscores.consistency) ?? 50,
      penalty_discipline: parseNumber(subscores.penalty_discipline),
    },
    pros: Array.isArray(rating.pros) ? (rating.pros as EventTeamRatingItem['pros']) : [],
    cons: Array.isArray(rating.cons) ? (rating.cons as EventTeamRatingItem['cons']) : [],
    evidence: Array.isArray(rating.evidence) ? (rating.evidence as EventTeamRatingItem['evidence']) : [],
    details: asRecord(rating.details) || {},
    model_version: typeof rating.model_version === 'string' ? rating.model_version : 'rating_v5_configured',
    updated_at: typeof rating.updated_at === 'string' ? rating.updated_at : null,
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
  const defaultTab: CompareTab = isCompareTab(tabParam) ? tabParam : 'summary';

  const [selectedEventKey, setSelectedEventKey] = useState(defaultEventKey);
  const [eventInput, setEventInput] = useState(defaultEventKey);
  const [activeTab, setActiveTab] = useState<CompareTab>(defaultTab);

  const [compareInput, setCompareInput] = useState('');
  const [addingTeam, setAddingTeam] = useState(false);
  const [compareTeamKeys, setCompareTeamKeys] = useState<string[]>(() => readStoredCompareTeamKeys());
  const [teamBundles, setTeamBundles] = useState<Record<string, CompareTeamBundle>>({});

  const [eventTeams, setEventTeams] = useState<EventTeamsIntelResponse | null>(null);
  const [loadingEventTeams, setLoadingEventTeams] = useState(false);

  const [theoreticalTeamKeys, setTheoreticalTeamKeys] = useState<[string, string, string]>(['', '', '']);
  const [theoreticalCompatibilityWeight, setTheoreticalCompatibilityWeight] = useState(60);
  const [theoreticalProsWeight, setTheoreticalProsWeight] = useState(25);
  const [theoreticalConsWeight, setTheoreticalConsWeight] = useState(15);
  const [includeSelectionModel, setIncludeSelectionModel] = useState(true);
  const [selectionScale, setSelectionScale] = useState(35);
  const [selectionRankWeight, setSelectionRankWeight] = useState(1);
  const [selectionSimulations, setSelectionSimulations] = useState(400);
  const [theoreticalResult, setTheoreticalResult] = useState<TheoreticalAllianceResponse | null>(null);
  const [loadingTheoretical, setLoadingTheoretical] = useState(false);
  const [refreshingCompare, setRefreshingCompare] = useState(false);

  const [statusText, setStatusText] = useState('Add teams to compare.');
  const [errorText, setErrorText] = useState('');
  const [theoreticalErrorText, setTheoreticalErrorText] = useState('');
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [eventPoolVisibleCount, setEventPoolVisibleCount] = useState(120);
  const [mobileFinderOpen, setMobileFinderOpen] = useState(false);

  useEffect(() => {
    const normalizedEvent = normalizeEventKeyInput(selectedEventKey);
    const next = new URLSearchParams();
    if (normalizedEvent) next.set('event', normalizedEvent);
    if (activeTab !== 'summary') next.set('tab', activeTab);

    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }

    if (normalizedEvent) {
      window.localStorage.setItem(COMPARE_STORAGE_KEYS.event, normalizedEvent);
    } else {
      window.localStorage.removeItem(COMPARE_STORAGE_KEYS.event);
    }

    window.localStorage.setItem(COMPARE_STORAGE_KEYS.teams, JSON.stringify(compareTeamKeys));
    writeCenterContext({ eventKey: normalizedEvent, sourcePath: '/compare' });
  }, [activeTab, compareTeamKeys, searchParams, selectedEventKey, setSearchParams]);

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
      setEventTeams(payload);
      return true;
    } catch (error) {
      setEventTeams(null);
      setErrorText((error as Error).message || 'Unable to load event teams for compare.');
      return false;
    } finally {
      setLoadingEventTeams(false);
    }
  }, [selectedEventKey]);

  useSingleFlightPolling({
    enabled: Boolean(selectedEventKey),
    visible: pageVisible,
    intervalMs: Math.max(10, liveRefreshSec) * 1000,
    run: refreshEventTeams,
    backoffMultiplier: 1.6,
    minBackoffMs: Math.max(10, liveRefreshSec) * 1000,
    maxBackoffMs: 60000,
  });

  const loadCompareBundle = useCallback(
    async (teamKeyInput: string, force = false, forceNetwork = false) => {
      const teamKey = teamKeyInput.trim().toLowerCase();
      if (!teamKey) return;
      const contextEventKey = selectedEventKey || '';

      const existing = teamBundles[teamKey];
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
      } catch (error) {
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
    [selectedEventKey, teamBundles],
  );

  useEffect(() => {
    for (const teamKey of compareTeamKeys) {
      const bundle = teamBundles[teamKey];
      const contextEventKey = selectedEventKey || '';
      const needsLoad = !bundle || bundle.event_key !== contextEventKey || (!bundle.loading && !bundle.breakdown);
      if (needsLoad) {
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
        label: 'Fuel Scoring Rate (per min)',
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

  const theoreticalTeamOptions = useMemo(() => {
    return [...eventTeamPool].sort((a, b) => a.team_number - b.team_number);
  }, [eventTeamPool]);

  const visibleEventTeamPool = useMemo(
    () => eventTeamPool.slice(0, Math.max(1, eventPoolVisibleCount)),
    [eventPoolVisibleCount, eventTeamPool],
  );

  const theoreticalWeightsPreview = useMemo(
    () =>
      normalizedTheoreticalWeights(theoreticalCompatibilityWeight, theoreticalProsWeight, theoreticalConsWeight),
    [theoreticalCompatibilityWeight, theoreticalConsWeight, theoreticalProsWeight],
  );

  function openEventContext() {
    const normalized = normalizeEventKeyInput(eventInput);
    setSelectedEventKey(normalized);
    setEventInput(normalized);
    setTheoreticalResult(null);
    setTheoreticalErrorText('');
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
    setTheoreticalResult(null);
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
    setTheoreticalTeamKeys(['', '', '']);
    setTheoreticalResult(null);
    setTheoreticalErrorText('');
    setStatusText('Cleared.');
  }

  function setTheoreticalSlot(slot: 0 | 1 | 2, value: string) {
    const normalized = value.trim().toLowerCase();
    setTheoreticalTeamKeys((prev) => {
      const next: [string, string, string] = [prev[0], prev[1], prev[2]];
      next[slot] = normalized;
      return next;
    });
    setTheoreticalResult(null);
    setTheoreticalErrorText('');
  }

  function autofillTheoreticalTeams() {
    if (!selectedEventKey || eventTeamPool.length < 3) {
      setTheoreticalErrorText('Pick an event with at least 3 teams to use Theoretical Builder.');
      return;
    }

    const candidates: string[] = [];

    const append = (teamKey: string | null | undefined) => {
      const normalized = (teamKey || '').trim().toLowerCase();
      if (!normalized || candidates.includes(normalized)) return;
      if (!eventTeamKeySet.has(normalized)) return;
      candidates.push(normalized);
    };

    for (const teamKey of compareTeamKeys) append(teamKey);
    for (const team of eventTeamPool) append(team.team_key);

    if (candidates.length < 3) {
      setTheoreticalErrorText('Need 3 unique event teams for Theoretical Builder.');
      return;
    }

    setTheoreticalTeamKeys([candidates[0], candidates[1], candidates[2]]);
    setTheoreticalResult(null);
    setTheoreticalErrorText('');
  }

  async function runTheoreticalBuilder() {
    if (!selectedEventKey || eventTeamPool.length === 0) {
      setTheoreticalErrorText('Select an event before running Theoretical Builder.');
      return;
    }

    const teams = theoreticalTeamKeys.map((teamKey) => teamKey.trim().toLowerCase()).filter(Boolean);
    if (teams.length !== 3 || new Set(teams).size !== 3) {
      setTheoreticalErrorText('Choose 3 unique teams for the theoretical alliance.');
      return;
    }

    const missing = teams.filter((teamKey) => !eventTeamKeySet.has(teamKey));
    if (missing.length > 0) {
      setTheoreticalErrorText(`Teams must come from ${selectedEventKey}. Invalid: ${missing.join(', ')}`);
      return;
    }

    const weights = normalizedTheoreticalWeights(
      theoreticalCompatibilityWeight,
      theoreticalProsWeight,
      theoreticalConsWeight,
    );

    setLoadingTheoretical(true);
    setTheoreticalErrorText('');

    try {
      const payload = await getTheoreticalAlliance(selectedEventKey, {
        team_keys: teams,
        compatibility_weight: weights.compatibility,
        pros_weight: weights.pros,
        cons_weight: weights.cons,
        include_selection_model: includeSelectionModel,
        selection_rank_weight: Math.max(0, selectionRankWeight),
        selection_scale: Math.max(1, selectionScale),
        selection_captains: 8,
        selection_simulations: Math.max(50, Math.min(5000, Math.round(selectionSimulations))),
        selection_rank_source: 'auto',
      });

      setTheoreticalResult(payload);
      setLastUpdatedAt(Date.now());
      setStatusText(`Theoretical scored: ${payload.team_keys.join(', ')}.`);
    } catch (error) {
      setTheoreticalResult(null);
      setTheoreticalErrorText((error as Error).message || 'Theoretical builder failed.');
    } finally {
      setLoadingTheoretical(false);
    }
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
          subtitle="Add teams and set event context."
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
              Event Center
            </Link>
          </div>

          <div className="center-status-row">
            <span className="center-chip">{compareTeamKeys.length}/4 teams</span>
            <span className="center-chip">{eventTeams?.teams_count ?? 0} pool</span>
            <span className="center-chip">{relativeFromTimestamp(lastUpdatedAt)}</span>
          </div>

          {errorText ? <p className="center-callout danger">{errorText}</p> : null}
          <p className="center-callout muted">{statusText}</p>
        </SurfaceCard>

        <SurfaceCard
          title="Event Team Pool"
          subtitle={
            selectedEventKey
              ? 'Theoretical Builder is restricted to this event roster.'
              : 'Pick an event key to load a constrained team pool.'
          }
          right={<span className="center-chip">{loadingEventTeams ? 'Loading...' : `${eventTeamPool.length} teams`}</span>}
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
                        {team.team_key} · analyzed {team.analyzed} · rating {metric(team.rating_0_100, 1)}
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
          subtitle="Compare diagnostics and theoretical alliance builder."
          right={<span className="center-chip">Context: {selectedEventKey || 'none'}</span>}
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
            <div className="compare-chip-row">
              {compareTeamKeys.map((teamKey) => (
                <div key={`compare-chip-${teamKey}`} className="compare-chip">
                  <button type="button" onClick={() => openTeamCenter(teamKey)}>
                    {compareTeamLabel(teamKey, teamBundles[teamKey]?.breakdown || null)}
                  </button>
                  <button type="button" aria-label={`Remove ${teamKey}`} onClick={() => removeCompareTeam(teamKey)}>
                    ×
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="center-callout muted">Add teams to build compare diagnostics.</p>
          )}
        </SurfaceCard>

        {activeTab === 'summary' ? (
          <SurfaceCardGroup groupId="compare-center-summary">
            <SurfaceCard title="Summary" subtitle="Key metrics and model outputs." className="compare-summary-card" compactable>
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

            <div className="center-table-wrap desktop-only">
              <table className="center-table compare-table">
                <thead>
                  <tr>
                    <th>Team</th>
                    <th>Rating</th>
                    <th>Confidence</th>
                    <th>Robot</th>
                    <th>TBA Rank</th>
                    <th>Fuel</th>
                    <th>Cycle</th>
                    <th>Auto</th>
                    <th>Climb</th>
                    <th>Reliability</th>
                    <th>Matches</th>
                    <th>Freshness</th>
                  </tr>
                </thead>
                <tbody>
                  {compareRows.map((row) => (
                    <tr key={`compare-summary-row-${row.team_key}`}>
                      <td>
                        <button type="button" className="center-inline-link" onClick={() => openTeamCenter(row.team_key)}>
                          {row.breakdown?.team.nickname || row.team_key}
                        </button>
                      </td>
                      <td>{metric(row.rating?.rating_0_100, 1)}</td>
                      <td>{pct(row.rating?.confidence_0_1, 1)}</td>
                      <td>{metric(row.rating?.robot_level_0_100, 1)}</td>
                      <td>
                        {(() => {
                          const status = asRecord(row.tba_event_status);
                          const qual = asRecord(status?.qual);
                          const ranking = asRecord(qual?.ranking);
                          const rank = parseNumber(ranking?.rank);
                          return rank !== null ? `#${rank}` : 'N/A';
                        })()}
                      </td>
                      <td>{metric(row.breakdown?.averages?.fuel_scoring_rate, 2)}</td>
                      <td>{metric(row.breakdown?.averages?.cycle_time_sec, 2)}</td>
                      <td>{metric(row.breakdown?.averages?.auto_contribution, 2)}</td>
                      <td>{pct(row.breakdown?.averages?.climb_success_prob, 1)}</td>
                      <td>{pct(row.breakdown?.averages?.reliability_score, 1)}</td>
                      <td>{row.breakdown?.matches_analyzed ?? 'N/A'}</td>
                      <td>{summarizeFreshness(row.breakdown?.data_freshness || null).label}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile summary cards */}
            <div className="compare-mobile-summary-list mobile-only">
              {compareRows.map((row) => {
                const status = asRecord(row.tba_event_status);
                const qual = asRecord(status?.qual);
                const ranking = asRecord(qual?.ranking);
                const rank = parseNumber(ranking?.rank);
                const freshness = summarizeFreshness(row.breakdown?.data_freshness || null);
                return (
                  <div key={`compare-mobile-${row.team_key}`} className="compare-mobile-summary-card">
                    <div className="compare-mobile-summary-head">
                      <button type="button" className="center-link-btn" onClick={() => openTeamCenter(row.team_key)} style={{ fontWeight: 600 }}>
                        {row.breakdown?.team.nickname || row.team_key}
                      </button>
                      <span className={`center-chip freshness ${freshness.state}`} style={{ fontSize: '0.7rem' }}>{freshness.label}</span>
                    </div>
                    <div className="compare-mobile-summary-metrics">
                      <span>Rating <strong>{metric(row.rating?.rating_0_100, 1)}</strong></span>
                      <span>Robot <strong>{metric(row.rating?.robot_level_0_100, 1)}</strong></span>
                      <span>Rank <strong>{rank !== null ? `#${rank}` : '—'}</strong></span>
                      <span>Climb <strong>{pct(row.breakdown?.averages?.climb_success_prob, 1)}</strong></span>
                      <span>Fuel <strong>{metric(row.breakdown?.averages?.fuel_scoring_rate, 2)}</strong></span>
                      <span>Cycle <strong>{metric(row.breakdown?.averages?.cycle_time_sec, 2)}</strong></span>
                      <span>Auto <strong>{metric(row.breakdown?.averages?.auto_contribution, 2)}</strong></span>
                      <span>Rely <strong>{pct(row.breakdown?.averages?.reliability_score, 1)}</strong></span>
                    </div>
                  </div>
                );
              })}
            </div>
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}

        {activeTab === 'detailed' ? (
          <SurfaceCardGroup groupId="compare-center-deep">
            <SurfaceCard
              title="Deep Compare"
              subtitle="Per-team pros/cons and diagnostics."
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
                      <span>Fuel Rate / min</span>
                      <strong>{metric(row.breakdown?.averages?.fuel_scoring_rate, 2)}</strong>
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
                    >
                      Refresh Team
                    </button>
                  </div>
                </article>
              ))}
            </div>
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}

        {activeTab === 'alliance' ? (
          <SurfaceCardGroup groupId="compare-center-theoretical">
            <SurfaceCard
              title="Theoretical Team Builder"
              subtitle="Build a 3-team alliance and score compatibility."
              className="compare-theoretical-card"
              compactable
            >
            {!selectedEventKey ? (
              <p className="center-callout warning">Select an event first.</p>
            ) : null}

            <div className="theoretical-builder-grid">
              <div className="theoretical-team-grid">
                {[0, 1, 2].map((slot) => (
                  <label key={`theoretical-slot-${slot}`} className="center-stack-form">
                    <span className="center-label">Team Slot {slot + 1}</span>
                    <select
                      className="center-input"
                      value={theoreticalTeamKeys[slot]}
                      onChange={(event) => setTheoreticalSlot(slot as 0 | 1 | 2, event.target.value)}
                    >
                      <option value="">Select team...</option>
                      {theoreticalTeamOptions.map((team) => (
                        <option key={`theoretical-option-${slot}-${team.team_key}`} value={team.team_key.toLowerCase()}>
                          #{team.team_number} {team.nickname || team.team_key}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>

              <div className="theoretical-weight-grid">
                <label className="center-stack-form">
                  <span className="center-label">
                    Compatibility Weight ({metric(theoreticalWeightsPreview.compatibility * 100, 1)}%)
                  </span>
                  <input
                    className="center-input"
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    value={theoreticalCompatibilityWeight}
                    onChange={(event) => setTheoreticalCompatibilityWeight(Number(event.target.value))}
                  />
                </label>

                <label className="center-stack-form">
                  <span className="center-label">Pros Weight ({metric(theoreticalWeightsPreview.pros * 100, 1)}%)</span>
                  <input
                    className="center-input"
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    value={theoreticalProsWeight}
                    onChange={(event) => setTheoreticalProsWeight(Number(event.target.value))}
                  />
                </label>

                <label className="center-stack-form">
                  <span className="center-label">Cons Penalty ({metric(theoreticalWeightsPreview.cons * 100, 1)}%)</span>
                  <input
                    className="center-input"
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    value={theoreticalConsWeight}
                    onChange={(event) => setTheoreticalConsWeight(Number(event.target.value))}
                  />
                </label>
              </div>

              <div className="theoretical-advanced-grid">
                <label className="center-stack-form">
                  <span className="center-label">Selection Model</span>
                  <select
                    className="center-input"
                    value={includeSelectionModel ? 'on' : 'off'}
                    onChange={(event) => setIncludeSelectionModel(event.target.value === 'on')}
                  >
                    <option value="on">Enabled</option>
                    <option value="off">Disabled</option>
                  </select>
                </label>

                <label className="center-stack-form">
                  <span className="center-label">Selection Scale</span>
                  <input
                    className="center-input"
                    type="number"
                    min={1}
                    max={200}
                    step={1}
                    value={selectionScale}
                    onChange={(event) => setSelectionScale(Number(event.target.value || 35))}
                  />
                </label>

                <label className="center-stack-form">
                  <span className="center-label">Rank Weight</span>
                  <input
                    className="center-input"
                    type="number"
                    min={0}
                    max={4}
                    step={0.1}
                    value={selectionRankWeight}
                    onChange={(event) => setSelectionRankWeight(Number(event.target.value || 1))}
                  />
                </label>

                <label className="center-stack-form">
                  <span className="center-label">Selection Simulations</span>
                  <input
                    className="center-input"
                    type="number"
                    min={50}
                    max={5000}
                    step={50}
                    value={selectionSimulations}
                    onChange={(event) => setSelectionSimulations(Number(event.target.value || 400))}
                  />
                </label>
              </div>

              <div className="center-actions-row">
                <button type="button" className="center-btn ghost" onClick={autofillTheoreticalTeams}>
                  Autofill Teams
                </button>
                <button type="button" className="center-btn" onClick={() => void runTheoreticalBuilder()} disabled={loadingTheoretical}>
                  {loadingTheoretical ? 'Scoring...' : 'Run Builder'}
                </button>
              </div>
            </div>

            {theoreticalErrorText ? <p className="center-callout warning">{theoreticalErrorText}</p> : null}

            {theoreticalResult ? (
              <div className="center-stack-gap">
                <div className="center-kpi-grid">
                  <div className="center-kpi-card">
                    <span>Weighted Score</span>
                    <strong>{metric(theoreticalResult.weighted_total_score_0_100, 2)} / 100</strong>
                  </div>
                  <div className="center-kpi-card">
                    <span>Compatibility Score</span>
                    <strong>{metric(theoreticalResult.compatibility.compatibility_score_0_100, 2)} / 100</strong>
                  </div>
                  <div className="center-kpi-card">
                    <span>Alliance Synergy Points</span>
                    <strong>{metric(theoreticalResult.compatibility.alliance_synergy_points, 3)}</strong>
                  </div>
                  <div className="center-kpi-card">
                    <span>Compatibility Confidence</span>
                    <strong>{pct(theoreticalResult.compatibility.confidence_0_1, 1)}</strong>
                  </div>
                  <div className="center-kpi-card">
                    <span>Pros Score</span>
                    <strong>{metric(theoreticalResult.pros_cons.alliance_pros_score_0_100, 2)} / 100</strong>
                  </div>
                  <div className="center-kpi-card">
                    <span>Cons Risk</span>
                    <strong>{metric(theoreticalResult.pros_cons.alliance_cons_risk_0_100, 2)} / 100</strong>
                  </div>
                </div>

                <div className="center-table-wrap">
                  <table className="center-table compare-table">
                    <thead>
                      <tr>
                        <th>Team</th>
                        <th>Rating</th>
                        <th>Confidence</th>
                        <th>Compatibility</th>
                        <th>Pros</th>
                        <th>Cons Risk</th>
                        <th>Weighted</th>
                        <th>Pick Prob.</th>
                      </tr>
                    </thead>
                    <tbody>
                      {theoreticalResult.teams.map((team) => (
                        <tr key={`theoretical-team-row-${team.team_key}`}>
                          <td>{team.team_key}</td>
                          <td>{metric(team.rating_0_100, 2)}</td>
                          <td>{pct(team.model_confidence_0_1, 1)}</td>
                          <td>{metric(team.compatibility_score_0_100, 2)}</td>
                          <td>{metric(team.pros_score_0_100, 2)}</td>
                          <td>{metric(team.cons_risk_0_100, 2)}</td>
                          <td>{metric(team.weighted_score_0_100, 2)}</td>
                          <td>{pct(team.selection_pick_probability_0_1, 1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="center-table-wrap">
                  <table className="center-table compare-table">
                    <thead>
                      <tr>
                        <th>Pair</th>
                        <th>Synergy Points</th>
                        <th>Base</th>
                        <th>Role Adj.</th>
                        <th>Complement</th>
                        <th>Risk Penalty</th>
                        <th>Confidence</th>
                        <th>Source</th>
                      </tr>
                    </thead>
                    <tbody>
                      {theoreticalResult.compatibility.pair_breakdown.map((pair, idx) => (
                        <tr key={`theoretical-pair-${idx}`}>
                          <td>
                            {pair.team_key_a} + {pair.team_key_b}
                          </td>
                          <td>{metric(pair.synergy_points, 3)}</td>
                          <td>{metric(pair.base_synergy_points, 3)}</td>
                          <td>{metric(pair.role_adjustment_points, 3)}</td>
                          <td>{metric(pair.complement_bonus_points, 3)}</td>
                          <td>{metric(pair.risk_penalty_points, 3)}</td>
                          <td>{pct(pair.confidence, 1)}</td>
                          <td>{titleizeKey(pair.source)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {theoreticalResult.selection_model ? (
                  <div className="center-table-wrap">
                    <table className="center-table compare-table">
                      <thead>
                        <tr>
                          <th>Selection Team</th>
                          <th>Rank</th>
                          <th>Strength</th>
                          <th>Desirability</th>
                          <th>R1 Pick Prob.</th>
                          <th>R2 Pick Prob.</th>
                        </tr>
                      </thead>
                      <tbody>
                        {theoreticalResult.selection_model.top_desirability.slice(0, 12).map((row) => (
                          <tr key={`selection-desirability-${row.team_key}`}>
                            <td>{row.team_key}</td>
                            <td>{row.rank}</td>
                            <td>{metric(row.strength_score, 2)}</td>
                            <td>{metric(row.selection_desirability, 2)}</td>
                            <td>{pct(row.first_round_pick_probability_0_1, 1)}</td>
                            <td>{pct(row.second_round_pick_probability_0_1, 1)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </div>
            ) : null}
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}
      </section>
    </div>
    </>
  );
}
