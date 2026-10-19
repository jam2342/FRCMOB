import { TEAM_NUMBER_PLACEHOLDER } from './centerUtils';
import { breakdownFromIntel, previousSeasonFromIntel, previousSeasonText, ratingFromIntel, type PreviousSeasonRating, type TeamIntelBreakdown } from './teamIntel';
import { RatingHistoryCard } from '../features/seasons/RatingHistoryCard';
import { EmptyState } from '../components/ui/EmptyState';
import { CountUp } from '../components/ui/CountUp';
import { TeamStickyBar } from '../features/teamHeader/TeamStickyBar';
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  getEventSchedule,
  getEventTeamLiveForm,
  getEventTeamsIntel,
  getTeamHeatmap,
  getTeamShiftPlay,
  getTeamRobotImage,
  isClientAdminModeEnabled,
} from '../api';
import type {
  AutoScoutProfile,
  AutoScoutProfileField,
  ClimbLevelCapability,
  EventTeamsIntelResponse,
  EventScheduleItem,
  EventTeamLiveFormResponse,
  EventTeamRatingItem,
  TeamCompetitionsResponse,
  TeamHeatmapResponse,
  TeamShiftPlayResponse,
  TeamLogoResponse,
  TeamRobotImageResponse,
} from '../api';
import { FieldHeatmap } from '../components/cv/FieldHeatmap';
import { SkeletonBlock } from '../components/ui/SkeletonBlock';
import { MediaImage } from '../components/ui/MediaImage';
import { eventDisplayName } from '../utils/eventSearchIndex';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { MobileViewActions } from '../components/ui/MobileViewActions';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { useExternalSearchSync } from '../hooks/useExternalSearchSync';
import { useLiveRefreshSetting } from '../hooks/useLiveRefreshSetting';
import { MOBILE_LAYOUT_BREAKPOINT, useMobileLayout } from '../hooks/useMobileLayout';
import { Chip, Stat, Table, type TableColumn } from '../components/ui/primitives';
import { SEASON_BENCHMARKS } from '../config/season';
import { isRobotSignal, officialMetricsNote, plainSignalLabel, ratingRangeLabel, scoreAgainst, signalRankLabel, signedChange } from './teamCenterOfficial';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling, type SingleFlightPollReason } from '../hooks/useSingleFlightPolling';
import {
  asRecord,
  clampNumber,
  CURRENT_SEASON_YEAR,
  fmtDateShort,
  matchStartTime,
  meanNumber,
  metric,
  normalizeMatchKey,
  normalizeTeamKeyInput,
  isTransientAbortLikeError,
  parseNumber,
  pct,
  relativeFromTimestamp,
  teamNumberFromTeamKey,
  titleizeKey,
} from './centerUtils';
import {
  GridIcon, BarChartIcon, CalendarIcon, ImageIcon, CodeIcon,
  FlameIcon, StopwatchIcon, RobotIcon, MountainIcon, GaugeIcon,
  TrophyIcon, HashIcon,
  ClockIcon, CheckCircleIcon, ExternalLinkIcon, ChevronDownIcon, ChevronRightIcon,
  SearchIcon, ScoreboardIcon, EyeIcon, AwardIcon,
} from '../components/ui/Icons';
import { readStoredCenterContext, writeCenterContext } from '../layout/centerContext';
import { ordinal } from '../utils/ordinal';
import { matchLabelFromKey } from '../utils/matchLabel';
import { cancelIdleWork, scheduleIdleWork } from '../utils/idle';
import { TeamFormStrip } from '../components/TeamFormStrip';
import { resolveTab } from './tabUtils';
import { loadTeamCenterIntel, loadTeamCenterLogo } from '../features/prefetch/pageRequests';
import { useWorkspace } from '../features/workspace/useWorkspace';
import { MISSING } from '../utils/formatValue';

const BASE_TEAM_TABS = ['overview', 'performance', 'events', 'media'] as const;
/* titleizeKey turns 'events' into "Events", which is also the name of a
   top-level nav section. Two visible controls reading "Events" on one screen,
   meaning different things — all events, and this team's events. */
const TEAM_TAB_LABELS: Record<string, string> = { events: 'Event history' };
function teamTabLabel(tab: string): string {
  return TEAM_TAB_LABELS[tab] ?? titleizeKey(tab);
}
const ALL_TEAM_TABS = ['overview', 'performance', 'events', 'media', 'advanced'] as const;
type TeamTab = (typeof ALL_TEAM_TABS)[number];

const TEAM_TAB_ICONS: Record<TeamTab, React.ReactNode> = {
  overview: <GridIcon className="icon-inline" />,
  performance: <BarChartIcon className="icon-inline" />,
  events: <CalendarIcon className="icon-inline" />,
  media: <ImageIcon className="icon-inline" />,
  advanced: <CodeIcon className="icon-inline" />,
};

type DifficultyTier = 'very-hard' | 'hard' | 'medium' | 'favorable';
type TeamScheduleSortMode = 'time' | 'hardest' | 'easiest';

type TeamScheduleDifficultyRow = {
  match_key: string;
  display_name: string;
  scheduled_time: number | null;
  alliance: 'RED' | 'BLUE';
  station: string | null;
  partners: string[];
  opponents: string[];
  partner_team_keys: string[];
  opponent_team_keys: string[];
  opponent_epa_avg: number;
  opponent_epa_min: number;
  opponent_epa_max: number;
  schedule_difficulty_0_10: number;
  difficulty_tier: DifficultyTier;
};

type ClimbCapabilityLevel = 'level1' | 'level2' | 'level3';

type ClimbCapabilitySummary = {
  best_level: ClimbCapabilityLevel | null;
  best_level_label: string;
  best_level_score_0_100: number | null;
  level_counts: Record<ClimbCapabilityLevel, number>;
  matches_with_level: number;
  matches_with_success_no_level: number;
  matches_considered: number;
};

const CLIMB_LEVEL_LABELS: Record<ClimbCapabilityLevel, string> = {
  level1: 'Level 1',
  level2: 'Level 2',
  level3: 'Level 3',
};

const TEAM_SCHEDULE_INITIAL_VISIBLE_COUNT = 30;
const TEAM_SCHEDULE_AUTO_CHUNK_SIZE = 30;
const TEAM_SCHEDULE_AUTO_VISIBLE_TARGET = 120;

function eventKeyFromMatchKey(matchKey: string): string | null {
  const normalized = matchKey.trim().toLowerCase();
  if (!normalized.includes('_')) return null;
  const [eventKey] = normalized.split('_');
  if (!/^\d{4}[a-z0-9]+$/.test(eventKey)) return null;
  return eventKey;
}

function stripHtmlTags(value: string): string {
  return value.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
}

function normalizeClimbLevelToken(value: unknown): ClimbCapabilityLevel | null {
  const numeric = parseNumber(value);
  if (numeric !== null) {
    if (numeric >= 2.75) return 'level3';
    if (numeric >= 1.75) return 'level2';
    if (numeric > 0) return 'level1';
    return null;
  }

  const token = String(value || '').trim().toLowerCase();
  if (!token) return null;
  const compact = token.replace(/[\s_-]+/g, '');
  if (
    compact === 'none' ||
    compact === 'no' ||
    compact === 'unknown' ||
    compact === 'null' ||
    compact === 'park' ||
    compact === 'parked'
  ) {
    return null;
  }
  if (
    compact.includes('level3') ||
    compact === 'l3' ||
    compact.includes('deepcage') ||
    compact.includes('travers') ||
    compact === 'high'
  ) {
    return 'level3';
  }
  if (
    compact.includes('level2') ||
    compact === 'l2' ||
    compact.includes('mid') ||
    compact.includes('centercage') ||
    compact === 'medium'
  ) {
    return 'level2';
  }
  if (
    compact.includes('level1') ||
    compact === 'l1' ||
    compact.includes('low') ||
    compact.includes('shallowcage')
  ) {
    return 'level1';
  }
  return null;
}

function extractClimbStatusToken(summary: Record<string, unknown> | null): unknown {
  if (!summary) return null;
  const status = asRecord(summary.status);
  if (status) {
    if (status.endgame_tower !== undefined) return status.endgame_tower;
    if (status.endgameTower !== undefined) return status.endgameTower;
    if (status.endgame !== undefined) return status.endgame;
  }
  const meta = asRecord(summary.meta);
  const metaStatus = asRecord(meta?.status);
  if (metaStatus) {
    if (metaStatus.endgame_tower !== undefined) return metaStatus.endgame_tower;
    if (metaStatus.endgameTower !== undefined) return metaStatus.endgameTower;
    if (metaStatus.endgame !== undefined) return metaStatus.endgame;
  }
  if (summary.endgame_tower !== undefined) return summary.endgame_tower;
  if (summary.endgameTower !== undefined) return summary.endgameTower;
  if (summary.endgame !== undefined) return summary.endgame;
  return null;
}

function summarizeClimbCapability(
  recentMatches: TeamIntelBreakdown['recent_matches'] | null | undefined,
): ClimbCapabilitySummary {
  const levelCounts: Record<ClimbCapabilityLevel, number> = { level1: 0, level2: 0, level3: 0 };
  const rows = recentMatches || [];
  let matchesWithLevel = 0;
  let matchesWithSuccessNoLevel = 0;

  for (const row of rows) {
    const summary = asRecord(row.summary);
    const parsedLevel = normalizeClimbLevelToken(extractClimbStatusToken(summary));
    if (parsedLevel) {
      levelCounts[parsedLevel] += 1;
      matchesWithLevel += 1;
      continue;
    }
    if (typeof row.climb_success_prob === 'number' && row.climb_success_prob >= 0.8) {
      matchesWithSuccessNoLevel += 1;
    }
  }

  const bestLevel: ClimbCapabilityLevel | null =
    levelCounts.level3 > 0 ? 'level3' : levelCounts.level2 > 0 ? 'level2' : levelCounts.level1 > 0 ? 'level1' : null;
  const bestLevelLabel = bestLevel
    ? CLIMB_LEVEL_LABELS[bestLevel]
    : matchesWithSuccessNoLevel > 0
      ? 'Success (Level Unknown)'
      : 'No Level Data';
  const bestLevelScore = bestLevel
    ? bestLevel === 'level3'
      ? 100
      : bestLevel === 'level2'
        ? 68
        : 38
    : matchesWithSuccessNoLevel > 0
      ? 34
      : null;

  return {
    best_level: bestLevel,
    best_level_label: bestLevelLabel,
    best_level_score_0_100: bestLevelScore,
    level_counts: levelCounts,
    matches_with_level: matchesWithLevel,
    matches_with_success_no_level: matchesWithSuccessNoLevel,
    matches_considered: rows.length,
  };
}



function competitionsFromIntel(intel: Record<string, unknown>, teamKey: string): TeamCompetitionsResponse {
  const competitions = asRecord(intel.competitions);
  const registered = Array.isArray(competitions?.registered_events) ? competitions.registered_events : [];
  return {
    ok: true,
    team_key: teamKey,
    event_key: typeof intel.event_key === 'string' ? intel.event_key : null,
    registration_year: parseNumber(competitions?.registration_year),
    registered_events_count: parseNumber(competitions?.registered_events_count) ?? registered.length,
    registered_events_source:
      typeof competitions?.registered_events_source === 'string' ? competitions.registered_events_source : 'intel',
    registered_events: registered as TeamCompetitionsResponse['registered_events'],
  };
}

// Friendly labels for the video-derived auto-scout fields shown in the robot profile.
const AUTO_SCOUT_FIELD_LABELS: Record<string, string> = {
  offense_level_1_5: 'Offense (1–5)',
  defense_level_1_5: 'Defense (1–5)',
  field_awareness_1_5: 'Field awareness (1–5)',
  decision_quality_1_5: 'Decision quality (1–5)',
  teleop_scored: 'Teleop scored',
  teleop_under_defense_scored: 'Scored under defense',
  teleop_cycles: 'Teleop cycles',
  auto_scored: 'Auto scored',
  auto_missed: 'Auto missed',
  intake_failures: 'Intake failures',
  foul_count: 'Fouls',
  auto_mobility: 'Auto mobility',
  endgame_mode: 'Endgame',
};

function humanizeFieldKey(key: string): string {
  return (
    AUTO_SCOUT_FIELD_LABELS[key] ||
    key.replace(/_1_5$/, ' (1–5)').replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
  );
}

type ProfileRow = { key: string; label: string; headline: string; sub: string; barPct: number | null };

function profileFieldToRow(key: string, field: AutoScoutProfileField): ProfileRow {
  const label = humanizeFieldKey(key);
  const conf =
    field.avg_confidence_0_1 != null ? `${Math.round(field.avg_confidence_0_1 * 100)}% conf` : 'conf n/a';
  const samples = `${field.samples} match${field.samples === 1 ? '' : 'es'}`;
  if (field.type === 'rate') {
    const pct = field.true_rate_0_1 != null ? Math.round(field.true_rate_0_1 * 100) : null;
    return {
      key,
      label,
      headline: pct != null ? `${pct}%` : '—',
      sub: `${samples} · ${conf}`,
      barPct: pct,
    };
  }
  if (field.type === 'numeric') {
    const isLevel = key.endsWith('_1_5');
    const value = field.median ?? field.last;
    return {
      key,
      label,
      headline: value != null ? String(value) : '—',
      sub: `range ${field.min ?? '—'}–${field.max ?? '—'} · ${samples} · ${conf}`,
      barPct: isLevel && value != null ? (clampNumber(value, 0, 5) / 5) * 100 : null,
    };
  }
  return {
    key,
    label,
    headline: field.mode,
    sub: `${samples} · ${conf}`,
    barPct: null,
  };
}

// The finder shows the number people type, not the frc-prefixed key.
function teamInputLabel(teamKey: string): string {
  return teamKey.replace(/^frc/i, '');
}

// Recordings your team made that nobody has reviewed yet: counted here for you,
// hidden from everyone else until an operator accepts them.
function OwnRecordingsNote({ matches }: { matches?: number }) {
  if (!matches) return null;
  return (
    <p className="muted">
      Includes {matches} match{matches === 1 ? '' : 'es'} from your team's own recordings. Only your team sees
      these until they're reviewed.
    </p>
  );
}

export function TeamCenterPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobileLayout = useMobileLayout();
  const pageVisible = usePageVisibility();
  const liveRefreshSec = useLiveRefreshSetting();
  const storedCenterContext = readStoredCenterContext();
  const adminModeEnabled = isClientAdminModeEnabled();
  const teamTabs: readonly TeamTab[] = adminModeEnabled ? ALL_TEAM_TABS : BASE_TEAM_TABS;

  const defaultEventKey = (searchParams.get('event') || '').trim().toLowerCase();
  const defaultTeamKey =
    (searchParams.get('team') || storedCenterContext.teamKey || '').trim().toLowerCase();
  const tabParam = searchParams.get('tab');
  const defaultTab = resolveTab(tabParam, teamTabs, 'overview');

  const [eventInput, setEventInput] = useState(defaultEventKey);
  const [teamInput, setTeamInput] = useState(() => teamInputLabel(defaultTeamKey));
  const [selectedEventKey, setSelectedEventKey] = useState(defaultEventKey);
  const [selectedTeamKey, setSelectedTeamKey] = useState(defaultTeamKey);
  const [activeTab, setActiveTab] = useState<TeamTab>(defaultTab);
  // The header's tab bar; once it scrolls away the slim team bar takes over.
  const heroTabsRef = useRef<HTMLDivElement | null>(null);

  const [teamBreakdown, setTeamBreakdown] = useState<TeamIntelBreakdown | null>(null);
  const [autoScoutProfile, setAutoScoutProfile] = useState<AutoScoutProfile | null>(null);
  const [teamRating, setTeamRating] = useState<EventTeamRatingItem | null>(null);
  // No rating this season: last season's, shown apart (services/seasons.py).
  const [previousSeason, setPreviousSeason] = useState<PreviousSeasonRating | null>(null);
  const [teamCompetitions, setTeamCompetitions] = useState<TeamCompetitionsResponse | null>(null);
  const [teamRobotImage, setTeamRobotImage] = useState<TeamRobotImageResponse | null>(null);
  const [teamLogo, setTeamLogo] = useState<TeamLogoResponse | null>(null);
  const [teamTbaEventStatus, setTeamTbaEventStatus] = useState<Record<string, unknown> | null>(null);
  const [teamTbaAwards, setTeamTbaAwards] = useState<Array<Record<string, unknown>>>([]);
  const [eventLiveForm, setEventLiveForm] = useState<EventTeamLiveFormResponse | null>(null);
  const [eventScheduleRows, setEventScheduleRows] = useState<EventScheduleItem[]>([]);
  const [eventTeams, setEventTeams] = useState<EventTeamsIntelResponse | null>(null);

  const [teamHeatmap, setTeamHeatmap] = useState<TeamHeatmapResponse | null>(null);
  const [teamShiftPlay, setTeamShiftPlay] = useState<TeamShiftPlayResponse | null>(null);
  const [teamRobotImageLoading, setTeamRobotImageLoading] = useState(false);

  const [loadingTeamData, setLoadingTeamData] = useState(false);
  const [statusText, setStatusText] = useState('Enter a team number.');
  /* A list, not a pipe-joined string. This used to render as
     "Intel: … | Intel: … | Intel: …" — a machine-joined line with its
     delimiter showing, in the middle of the page. They are separate
     messages, so they are separate list items. */
  const [errorMessages, setErrorMessages] = useState<string[]>([]);
  // The team whose numbers didn't arrive (failed or timed out), so the page can say so
  // where a phone shows it, instead of N/A and 0 that read like real data.
  const [intelFailedFor, setIntelFailedFor] = useState('');

  // Append without duplicating; several requests can report the same failure.
  const addErrorMessage = useCallback((message: string) => {
    setErrorMessages((current) => (current.includes(message) ? current : [...current, message]));
  }, []);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [scheduleVisibleCount, setScheduleVisibleCount] = useState(TEAM_SCHEDULE_INITIAL_VISIBLE_COUNT);
  const [scheduleSortMode, setScheduleSortMode] = useState<TeamScheduleSortMode>('time');
  const [mobileFinderOpen, setMobileFinderOpen] = useState(() => !defaultTeamKey);
  const lastContextRef = useRef('');
  const staticFailedRef = useRef(false);
  const currentSelectionRef = useRef('');
  currentSelectionRef.current = `${selectedTeamKey}|${selectedEventKey || ''}`;
  const lastRequestedSelectionRef = useRef('');
  const lastEventDetailsPrefetchRef = useRef('');
  const robotImageFetchContextRef = useRef('');
  const eventDetailsLoadRef = useRef<{
    contextKey: string;
    scheduleLoaded: boolean;
    teamsLoaded: boolean;
  }>({
    contextKey: '',
    scheduleLoaded: false,
    teamsLoaded: false,
  });

  const urlSync = useExternalSearchSync(searchParams, (params) => {
    const urlEventKey = (params.get('event') || '').trim().toLowerCase();
    const urlTeamKey =
      (params.get('team') || readStoredCenterContext().teamKey || '').trim().toLowerCase();
    const urlTabParam = params.get('tab');
    const urlTab = resolveTab(urlTabParam, teamTabs, 'overview');

    setSelectedEventKey(urlEventKey);
    setSelectedTeamKey(urlTeamKey);
    setActiveTab(urlTab);
    setEventInput(urlEventKey);
    setTeamInput(teamInputLabel(urlTeamKey));
  });

  useEffect(() => {
    if (teamTabs.includes(activeTab)) return;
    setActiveTab('overview');
  }, [activeTab, teamTabs]);

  useEffect(() => {
    // A link from one Team Center view to another used to flip the URL and
    // this state back and forth until React gave up ("Maximum update depth
    // exceeded"); see useExternalSearchSync.
    if (!urlSync.shouldWrite()) return;
    const currentSearch = searchParams.toString();

    const next = new URLSearchParams();
    if (selectedEventKey) next.set('event', selectedEventKey);
    if (selectedTeamKey) next.set('team', selectedTeamKey);
    next.set('tab', activeTab);

    if (next.toString() !== currentSearch) {
      urlSync.markWritten(next.toString());
      setSearchParams(next, { replace: true });
    }

    const contextUpdate = {
      teamKey: selectedTeamKey,
      sourcePath: '/team-center',
      ...(selectedEventKey ? { eventKey: selectedEventKey } : {}),
    };
    writeCenterContext(contextUpdate);
  }, [activeTab, searchParams, selectedEventKey, selectedTeamKey, setSearchParams, urlSync]);

  useEffect(() => {
    setScheduleVisibleCount(TEAM_SCHEDULE_INITIAL_VISIBLE_COUNT);
    setScheduleSortMode('time');
  }, [selectedEventKey, selectedTeamKey]);

  useEffect(() => {
    if (!isMobileLayout) setMobileFinderOpen(false);
  }, [isMobileLayout]);

  useEffect(() => {
    if (selectedTeamKey) return;
    lastContextRef.current = '';
    robotImageFetchContextRef.current = '';
    setTeamBreakdown(null);
    setAutoScoutProfile(null);
    setTeamRating(null);
    setPreviousSeason(null);
    setTeamCompetitions(null);
    setTeamRobotImage(null);
    setTeamRobotImageLoading(false);
    setTeamLogo(null);
    setTeamTbaEventStatus(null);
    setTeamTbaAwards([]);
    setEventLiveForm(null);
    setEventScheduleRows([]);
    setEventTeams(null);
    setTeamHeatmap(null);
    setTeamShiftPlay(null);
    setErrorMessages([]);
    setStatusText('Enter a team number.');
    setLoadingTeamData(false);
  }, [selectedTeamKey]);

  const loadTeamContext = useCallback(async (reason: SingleFlightPollReason): Promise<boolean> => {
    if (!selectedTeamKey) return true;

    const selectedEvent = selectedEventKey || undefined;
    const contextKey = `${selectedTeamKey}|${selectedEvent || ''}`;
    const contextChanged = lastContextRef.current !== contextKey;
    // A new team starts from blank; the same team under another event (or the whole season, when
    // it turns out it never attended the event in context) keeps its numbers on screen until the
    // new ones land. Blanking there made the page show the team, reset to N/A, then show it again.
    const teamChanged = lastContextRef.current.split('|')[0] !== selectedTeamKey;
    const shouldLoadEventSpecific = Boolean(selectedEvent);
    const shouldLoadEventDetails = shouldLoadEventSpecific && activeTab === 'events';
    if (contextChanged && teamChanged) {
      setTeamBreakdown(null);
      setAutoScoutProfile(null);
      setTeamRating(null);
    setPreviousSeason(null);
      setTeamCompetitions(null);
      setTeamRobotImage(null);
      setTeamLogo(null);
      setTeamTbaEventStatus(null);
      setTeamTbaAwards([]);
      setEventLiveForm(null);
      setEventScheduleRows([]);
      setEventTeams(null);
    } else if (contextChanged) {
      // Only the event changed: what belongs to the old event goes.
      setEventLiveForm(null);
      setEventScheduleRows([]);
      setEventTeams(null);
    }
    if (!shouldLoadEventSpecific) {
      setEventLiveForm(null);
      setEventScheduleRows([]);
      setEventTeams(null);
    }
    if (contextChanged || eventDetailsLoadRef.current.contextKey !== contextKey) {
      eventDetailsLoadRef.current = {
        contextKey,
        scheduleLoaded: false,
        teamsLoaded: false,
      };
    }
    lastContextRef.current = contextKey;

    setLoadingTeamData(true);
    setErrorMessages([]);
    setStatusText(
      contextChanged || reason === 'initial'
        ? `Loading team ${teamInputLabel(selectedTeamKey)}…`
        : 'Refreshing…',
    );
    try {
      const eventDetailsMissing =
        shouldLoadEventDetails &&
        (!eventDetailsLoadRef.current.scheduleLoaded || !eventDetailsLoadRef.current.teamsLoaded);
      // A failed team-intel or logo load retries on the next poll, which is what the
      // "retries on its own" note promises.
      const shouldRefreshStatic = contextChanged || reason !== 'poll' || eventDetailsMissing || staticFailedRef.current;
      const scheduleContextKey = contextKey;
      const scheduleRequest = shouldRefreshStatic && shouldLoadEventDetails
        ? getEventSchedule(
          selectedEventKey,
          false,
          { includeLiveResults: false },
          { timeoutMs: 45000 },
        )
        : null;
      const eventTeamsContextKey = contextKey;
      const eventTeamsRequest = shouldRefreshStatic && shouldLoadEventDetails
        ? getEventTeamsIntel(selectedEventKey, {
            include_tba: false,
            include_season_fallback: true,
            include_rating_details: false,
            include_rating_signals: false,
            auto_heal_ratings: true,
          }, {
            timeoutMs: 25000,
          })
        : null;
      // These two are awaited further down; mark them handled now so a fast failure on bad
      // Wi-Fi isn't reported as an unhandled rejection in the meantime.
      void scheduleRequest?.catch(() => undefined);
      void eventTeamsRequest?.catch(() => undefined);
      const [intelResult, logoResult, liveFormResult] =
        await Promise.allSettled([
          shouldRefreshStatic ? loadTeamCenterIntel(selectedTeamKey, selectedEvent) : Promise.resolve(null),
          shouldRefreshStatic
            ? loadTeamCenterLogo(selectedTeamKey, shouldLoadEventSpecific ? selectedEvent : undefined)
            : Promise.resolve(null),
          shouldLoadEventSpecific
            ? getEventTeamLiveForm(selectedEventKey, { form_window: 5, live_window_sec: 180 })
            : Promise.resolve(null),
        ]);
      // Another team or event was picked meanwhile; its own load follows right away.
      if (currentSelectionRef.current !== `${selectedTeamKey}|${selectedEventKey || ''}`) return true;

      const errors: string[] = [];

      if (shouldRefreshStatic && intelResult.status === 'fulfilled' && intelResult.value) {
        setIntelFailedFor('');
        const intel = intelResult.value as unknown as Record<string, unknown>;
        const team = asRecord(intel.team);
        const teamNumber = parseNumber(team?.team_number) ?? teamNumberFromTeamKey(selectedTeamKey) ?? 0;
        const nickname = typeof team?.nickname === 'string' ? team.nickname : null;
        const breakdown = breakdownFromIntel(intel, selectedTeamKey, true);
        const competitions = competitionsFromIntel(intel, selectedTeamKey);
        const rating = breakdown ? ratingFromIntel(intel, selectedTeamKey, teamNumber, nickname, true) : null;
        const tba = asRecord(intel.tba);
        const tbaAwardsRaw = Array.isArray(tba?.awards) ? tba.awards : [];

        setTeamBreakdown(breakdown);
        setAutoScoutProfile((intel.auto_scout_profile as AutoScoutProfile | undefined) ?? null);
        setTeamCompetitions(competitions);
        setTeamRating(rating);
        setPreviousSeason(rating ? null : previousSeasonFromIntel(intel));
        setTeamTbaEventStatus(asRecord(tba?.event_status));
        setTeamTbaAwards(
          tbaAwardsRaw.filter((value): value is Record<string, unknown> => Boolean(asRecord(value))),
        );

        // intel.warnings are notes about the data ("No scouted matches yet"), not failed
        // loads; the freshness callout below already shows the one that matters.
      } else if (shouldRefreshStatic && intelResult.status === 'rejected') {
        setIntelFailedFor(selectedTeamKey);
        if (!isTransientAbortLikeError(intelResult.reason)) {
          errors.push(`Team intel: ${intelResult.reason instanceof Error ? intelResult.reason.message : 'failed'}`);
        }
      }

      if (shouldRefreshStatic && logoResult.status === 'fulfilled' && logoResult.value) {
        setTeamLogo(logoResult.value);
      } else if (shouldRefreshStatic && logoResult.status === 'rejected') {
        if (!isTransientAbortLikeError(logoResult.reason)) {
          errors.push(`Logo: ${logoResult.reason instanceof Error ? logoResult.reason.message : 'failed'}`);
        }
      }

      if (liveFormResult.status === 'fulfilled') {
        setEventLiveForm(liveFormResult.value);
      } else if (shouldLoadEventSpecific) {
        if (!isTransientAbortLikeError(liveFormResult.reason)) {
          errors.push(`Live form: ${liveFormResult.reason instanceof Error ? liveFormResult.reason.message : 'failed'}`);
        }
      }

      if (eventTeamsRequest) {
        void eventTeamsRequest
          .then((payload) => {
            if (lastContextRef.current !== eventTeamsContextKey) return;
            setEventTeams(payload as EventTeamsIntelResponse);
            eventDetailsLoadRef.current = {
              ...eventDetailsLoadRef.current,
              contextKey: eventTeamsContextKey,
              teamsLoaded: true,
            };
          })
          .catch((error) => {
            if (lastContextRef.current !== eventTeamsContextKey) return;
            eventDetailsLoadRef.current = {
              ...eventDetailsLoadRef.current,
              contextKey: eventTeamsContextKey,
              teamsLoaded: false,
            };
            if (isTransientAbortLikeError(error)) return;
            const detail = error instanceof Error ? error.message : 'failed';
            addErrorMessage(`Event teams: ${detail}`);
          });
      }

      if (scheduleRequest) {
        void scheduleRequest
          .then((payload) => {
            if (lastContextRef.current !== scheduleContextKey) return;
            setEventScheduleRows(payload?.matches || []);
            eventDetailsLoadRef.current = {
              ...eventDetailsLoadRef.current,
              contextKey: scheduleContextKey,
              scheduleLoaded: true,
            };
          })
          .catch((error) => {
            if (lastContextRef.current !== scheduleContextKey) return;
            eventDetailsLoadRef.current = {
              ...eventDetailsLoadRef.current,
              contextKey: scheduleContextKey,
              scheduleLoaded: false,
            };
            if (isTransientAbortLikeError(error)) return;
            const detail = error instanceof Error ? error.message : 'failed';
            addErrorMessage(`Schedule: ${detail}`);
          });
      }

      if (shouldRefreshStatic) {
        // A timed-out intel load is retried too; only the logo may give up quietly.
        staticFailedRef.current = intelResult.status === 'rejected'
          || (logoResult.status === 'rejected' && !isTransientAbortLikeError(logoResult.reason));
      }
      setErrorMessages(errors);
      setStatusText(
        errors.length > 0
          ? 'Some data is missing'
          : 'Up to date',
      );
      setLastUpdatedAt(Date.now());
      return errors.length === 0;
    } finally {
      setLoadingTeamData(false);
    }
  }, [activeTab, addErrorMessage, selectedEventKey, selectedTeamKey]);

  const { triggerNow: triggerTeamReload } = useSingleFlightPolling({
    enabled: Boolean(selectedTeamKey),
    visible: pageVisible,
    intervalMs: Math.max(10, liveRefreshSec) * 1000,
    run: loadTeamContext,
    backoffMultiplier: 1.6,
    minBackoffMs: Math.max(10, liveRefreshSec) * 1000,
    maxBackoffMs: 60000,
  });

  useEffect(() => {
    if (!selectedTeamKey) {
      lastRequestedSelectionRef.current = '';
      return;
    }
    const selectionKey = `${selectedTeamKey}|${selectedEventKey || ''}`;
    if (!lastRequestedSelectionRef.current) {
      lastRequestedSelectionRef.current = selectionKey;
      return;
    }
    if (lastRequestedSelectionRef.current === selectionKey) return;
    lastRequestedSelectionRef.current = selectionKey;
    if (activeTab === 'events' && selectedEventKey) {
      lastEventDetailsPrefetchRef.current = selectionKey;
    }
    triggerTeamReload('manual');
  }, [activeTab, selectedEventKey, selectedTeamKey, triggerTeamReload]);

  useEffect(() => {
    if (!selectedTeamKey || !selectedEventKey || activeTab !== 'events') return;
    const key = `${selectedTeamKey}|${selectedEventKey}`;
    if (lastEventDetailsPrefetchRef.current === key) return;
    lastEventDetailsPrefetchRef.current = key;
    triggerTeamReload('manual');
  }, [activeTab, selectedEventKey, selectedTeamKey, triggerTeamReload]);

  /* ── heatmap: lazy-load when performance tab is active ────────── */
  // Your team's own phone recordings count here before anyone reviews them;
  // the server shows them to your workspace only.
  const workspace = useWorkspace();
  const inWorkspace = Boolean(workspace);
  const workspaceId = workspace?.workspace.id ?? null;
  // Positional data belongs to one team at one event: drop it as soon as either
  // changes, so "Overall" never shows the previous event's heat map.
  useEffect(() => {
    setTeamHeatmap(null);
    setTeamShiftPlay(null);
  }, [selectedTeamKey, selectedEventKey]);

  useEffect(() => {
    if (activeTab !== 'performance' || !selectedTeamKey || !selectedEventKey) return;
    let cancelled = false;
    // Supplementary: a failed load just leaves the card hidden.
    getTeamHeatmap(selectedTeamKey, selectedEventKey, { include_workspace: inWorkspace })
      .then((res) => { if (!cancelled) setTeamHeatmap(res); })
      .catch(() => { if (!cancelled) setTeamHeatmap(null); });
    return () => { cancelled = true; };
  }, [activeTab, selectedTeamKey, selectedEventKey, inWorkspace, workspaceId]);

  /* ── shift-play (offense/defense + attack/defense heat maps) ────── */
  useEffect(() => {
    if (activeTab !== 'performance' || !selectedTeamKey || !selectedEventKey) return;
    let cancelled = false;
    getTeamShiftPlay(selectedTeamKey, selectedEventKey, { include_workspace: inWorkspace })
      .then((res) => { if (!cancelled) setTeamShiftPlay(res); })
      .catch(() => { if (!cancelled) setTeamShiftPlay(null); });
    return () => { cancelled = true; };
  }, [activeTab, selectedTeamKey, selectedEventKey, inWorkspace, workspaceId]);

  useEffect(() => {
    if (activeTab !== 'media' || !selectedTeamKey) return;
    const contextKey = `${selectedTeamKey}|${selectedEventKey || ''}`;
    if (robotImageFetchContextRef.current === contextKey) return;
    robotImageFetchContextRef.current = contextKey;
    let cancelled = false;
    let finished = false;
    setTeamRobotImageLoading(true);
    getTeamRobotImage(selectedTeamKey, selectedEventKey || undefined)
      .then((res) => {
        if (cancelled) return;
        finished = true;
        setTeamRobotImage(res);
      })
      .catch(() => {
        if (cancelled) return;
        setTeamRobotImage(null);
        // A failed load may be retried by coming back to the tab.
        robotImageFetchContextRef.current = '';
      })
      .finally(() => {
        if (!cancelled) setTeamRobotImageLoading(false);
      });
    return () => {
      // Leaving the tab mid-load threw the answer away but kept the "fetched" mark,
      // so the photo never loaded on return. Clear it unless this load finished.
      if (!finished && robotImageFetchContextRef.current === contextKey) {
        robotImageFetchContextRef.current = '';
      }
      cancelled = true;
      setTeamRobotImageLoading(false);
    };
  }, [activeTab, selectedEventKey, selectedTeamKey]);

  const registeredEventOptions = useMemo(() => {
    const competitionsTeamKey = String(teamCompetitions?.team_key || '').toLowerCase();
    if (!selectedTeamKey || competitionsTeamKey !== selectedTeamKey) return [];
    const rows = teamCompetitions?.registered_events || [];
    return rows
      .map((event) => ({
        event_key: String(event.event_key || '').toLowerCase(),
        name: String(event.name || event.event_key || '').trim(),
        start_date: event.start_date || null,
      }))
      .filter((row) => row.event_key.length > 0);
  }, [selectedTeamKey, teamCompetitions]);

  const eventNameFor = (eventKey: string) =>
    registeredEventOptions.find((event) => event.event_key === eventKey)?.name || eventDisplayName(eventKey);
  const selectedEventInOptions = useMemo(
    () => !selectedEventKey || registeredEventOptions.some((event) => event.event_key === selectedEventKey),
    [registeredEventOptions, selectedEventKey],
  );

  // The event usually arrives from the shared context (whatever the user last looked
  // at), not from the team. Showing 254 "at 2026arc", an event it never attended,
  // put fallback numbers under that event's name. Drop it and say so.
  const [skippedEventKey, setSkippedEventKey] = useState('');
  useEffect(() => {
    setSkippedEventKey('');
  }, [selectedTeamKey]);
  useEffect(() => {
    if (!selectedEventKey || registeredEventOptions.length === 0 || selectedEventInOptions) return;
    const eventYear = Number(selectedEventKey.slice(0, 4));
    if (teamCompetitions?.registration_year !== eventYear) return;
    setSkippedEventKey(selectedEventKey);
    setSelectedEventKey('');
    setEventInput('');
  }, [registeredEventOptions, selectedEventInOptions, selectedEventKey, teamCompetitions?.registration_year]);

  const selectedTeamLiveForm = useMemo(() => {
    if (!selectedTeamKey) return null;
    return eventLiveForm?.team_statuses?.[selectedTeamKey.toLowerCase()] || null;
  }, [eventLiveForm, selectedTeamKey]);

  const eventTeamEpaByKey = useMemo(() => {
    const teams = Array.isArray(eventTeams?.teams) ? eventTeams.teams : [];
    if (teams.length === 0) return {} as Record<string, number>;
    const map: Record<string, number> = {};
    for (const entry of teams) {
      const row = asRecord(entry);
      const teamKey = typeof row?.team_key === 'string' ? row.team_key.toLowerCase() : '';
      if (!teamKey) continue;
      const rating = asRecord(row?.rating);
      const ratingValue = parseNumber(rating?.rating_0_100);
      if (ratingValue !== null) {
        map[teamKey] = ratingValue;
      }
    }
    return map;
  }, [eventTeams]);

  const defaultEventEpa = useMemo(() => {
    const values = Object.values(eventTeamEpaByKey).filter((value) => Number.isFinite(value));
    if (values.length === 0) return 50;
    return meanNumber(values);
  }, [eventTeamEpaByKey]);

  const eventEpaSpread = useMemo(() => {
    const values = Object.values(eventTeamEpaByKey).filter((value) => Number.isFinite(value));
    if (values.length < 2) return 12;
    const mean = meanNumber(values);
    const variance = values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length;
    return Math.max(6, Math.sqrt(variance));
  }, [eventTeamEpaByKey]);

  const scheduleRowsWithDifficulty = useMemo(() => {
    if (!selectedTeamKey || eventScheduleRows.length === 0) return [];

    const normalizedTeamKey = selectedTeamKey.toLowerCase();

    const baseRows = eventScheduleRows
      .map((match) => {
        const redTeams = match.red.map((team) => team.team_key.toLowerCase());
        const blueTeams = match.blue.map((team) => team.team_key.toLowerCase());

        const inRed = redTeams.includes(normalizedTeamKey);
        const inBlue = blueTeams.includes(normalizedTeamKey);
        if (!inRed && !inBlue) return null;

        const alliance = inRed ? 'RED' : 'BLUE';
        const ownAlliance = inRed ? match.red : match.blue;
        const oppAlliance = inRed ? match.blue : match.red;
        const ownTeamRecord = ownAlliance.find((team) => team.team_key.toLowerCase() === normalizedTeamKey) || null;
        const partners = ownAlliance.filter((team) => team.team_key.toLowerCase() !== normalizedTeamKey);

        const opponentEpas = oppAlliance.map((team) => eventTeamEpaByKey[team.team_key.toLowerCase()] ?? defaultEventEpa);
        const opponentEpaAvg = opponentEpas.length > 0 ? meanNumber(opponentEpas) : defaultEventEpa;
        const opponentEpaMin = opponentEpas.length > 0 ? Math.min(...opponentEpas) : defaultEventEpa;
        const opponentEpaMax = opponentEpas.length > 0 ? Math.max(...opponentEpas) : defaultEventEpa;

        // Schedule difficulty is intentionally simple + fast: opponents' average rating
        // only, normalized against this event's rating spread so the 1-10 scale
        // remains meaningful across different events.
        const difficultyRaw = clampNumber(
          5 + ((opponentEpaAvg - defaultEventEpa) / Math.max(1, eventEpaSpread)) * 1.75,
          1,
          10,
        );
        const difficultyTier: DifficultyTier =
          difficultyRaw >= 8.5
            ? 'very-hard'
            : difficultyRaw >= 7
              ? 'hard'
              : difficultyRaw >= 5
                ? 'medium'
                : 'favorable';

        return {
          match_key: match.match_key,
          display_name: match.display_name,
          scheduled_time: match.scheduled_time,
          alliance,
          station: ownTeamRecord?.station || null,
          partners: partners.map((team) => `#${team.team_number}`),
          opponents: oppAlliance.map((team) => `#${team.team_number}`),
          partner_team_keys: partners.map((team) => team.team_key.toLowerCase()),
          opponent_team_keys: oppAlliance.map((team) => team.team_key.toLowerCase()),
          opponent_epa_avg: Number(opponentEpaAvg.toFixed(1)),
          opponent_epa_min: Number(opponentEpaMin.toFixed(1)),
          opponent_epa_max: Number(opponentEpaMax.toFixed(1)),
          schedule_difficulty_0_10: Number(difficultyRaw.toFixed(1)),
          difficulty_tier: difficultyTier,
        } satisfies TeamScheduleDifficultyRow;
      })
      .filter((row): row is TeamScheduleDifficultyRow => Boolean(row));

    return baseRows.sort((a, b) => {
      if (!a.scheduled_time && !b.scheduled_time) return a.match_key.localeCompare(b.match_key);
      if (!a.scheduled_time) return 1;
      if (!b.scheduled_time) return -1;
      return a.scheduled_time - b.scheduled_time;
    });
  }, [defaultEventEpa, eventEpaSpread, eventScheduleRows, eventTeamEpaByKey, selectedTeamKey]);

  // Prepared for future display metrics; compute eagerly for warm cache paths.
  useMemo(() => {
    const values = scheduleRowsWithDifficulty
      .map((row) => row.schedule_difficulty_0_10)
      .filter((value) => Number.isFinite(value));
    if (values.length === 0) return null;
    return Number(meanNumber(values).toFixed(1));
  }, [scheduleRowsWithDifficulty]);

  const sortedScheduleRowsWithDifficulty = useMemo(() => {
    const rows = [...scheduleRowsWithDifficulty];
    if (scheduleSortMode === 'hardest') {
      rows.sort((a, b) => {
        if (b.schedule_difficulty_0_10 !== a.schedule_difficulty_0_10) {
          return b.schedule_difficulty_0_10 - a.schedule_difficulty_0_10;
        }
        return a.match_key.localeCompare(b.match_key);
      });
      return rows;
    }
    if (scheduleSortMode === 'easiest') {
      rows.sort((a, b) => {
        if (a.schedule_difficulty_0_10 !== b.schedule_difficulty_0_10) {
          return a.schedule_difficulty_0_10 - b.schedule_difficulty_0_10;
        }
        return a.match_key.localeCompare(b.match_key);
      });
      return rows;
    }
    rows.sort((a, b) => {
      if (!a.scheduled_time && !b.scheduled_time) return a.match_key.localeCompare(b.match_key);
      if (!a.scheduled_time) return 1;
      if (!b.scheduled_time) return -1;
      return a.scheduled_time - b.scheduled_time;
    });
    return rows;
  }, [scheduleRowsWithDifficulty, scheduleSortMode]);

  const visibleScheduleRowsWithDifficulty = useMemo(
    () => sortedScheduleRowsWithDifficulty.slice(0, Math.max(1, scheduleVisibleCount)),
    [scheduleVisibleCount, sortedScheduleRowsWithDifficulty],
  );

  /* Opponent-EPA range used to be desktop-only <small> text under the
     difficulty pill and the phone showed only the average, so the two views
     disagreed about what the difficulty was based on. It is one column now. */
  const scheduleColumns: TableColumn<(typeof visibleScheduleRowsWithDifficulty)[number]>[] = [
    {
      key: 'match',
      label: 'Match',
      render: (row) => (
        <button type="button" className="center-inline-link" onClick={() => openMatchCenter(row.match_key)}>
          {row.display_name}
        </button>
      ),
    },
    { key: 'time', label: 'Time', render: (row) => fmtDateShort(matchStartTime(row)) },
    {
      key: 'alliance',
      label: 'Alliance',
      render: (row) => (
        <span className={`center-alliance-pill ${row.alliance.toLowerCase()}`}>
          {row.alliance}
          {row.station ? ` (${row.station})` : ''}
        </span>
      ),
    },
    { key: 'partners', label: 'Partners', render: (row) => row.partners.join(', ') || 'TBD' },
    { key: 'opponents', label: 'Opponents', render: (row) => row.opponents.join(', ') || 'TBD' },
    {
      key: 'difficulty',
      label: 'Difficulty',
      render: (row) => (
        <span className={`center-difficulty-pill ${row.difficulty_tier}`}>
          {metric(row.schedule_difficulty_0_10, 1)} / 10
        </span>
      ),
    },
    {
      key: 'opponent_epa',
      label: 'Opp avg rating',
      numeric: true,
      render: (row) =>
        `${metric(row.opponent_epa_avg, 0)} (${metric(row.opponent_epa_min, 0)}-${metric(row.opponent_epa_max, 0)})`,
    },
  ];

  useEffect(() => {
    const maxAutoVisible = Math.min(TEAM_SCHEDULE_AUTO_VISIBLE_TARGET, sortedScheduleRowsWithDifficulty.length);
    if (maxAutoVisible <= scheduleVisibleCount) return;
    const handle = scheduleIdleWork(() => {
      setScheduleVisibleCount((current) => {
        if (current >= maxAutoVisible) return current;
        return Math.min(maxAutoVisible, current + TEAM_SCHEDULE_AUTO_CHUNK_SIZE);
      });
    }, { fallbackDelayMs: 45, timeoutMs: 180 });
    return () => cancelIdleWork(handle);
  }, [scheduleVisibleCount, sortedScheduleRowsWithDifficulty.length]);

  // With no scouting or video for a team, the backend fills fuel and auto from
  // TBA's per-team estimates and climb from the official record, and flags each;
  // showing those as measured stats misled people.
  const coverageFor = (key: string) => teamBreakdown?.metric_coverage?.[key];
  const isEstimatedMetric = (key: string): boolean => Boolean(coverageFor(key)?.estimated_from_model);
  const estimateChip = (key: string) =>
    isEstimatedMetric(key) ? (
      <Chip size="sm" tone="warn" className="kpi-tag" title={coverageFor(key)?.missing_reason ?? undefined}>Estimate</Chip>
    ) : null;
  const climbRecord = coverageFor('climb_success_prob')?.official_record ? coverageFor('climb_success_prob') : null;
  const metricsAreEstimates = Boolean(teamBreakdown);
  const officialStats = teamBreakdown?.official_stats ?? null;

  const climbCapability = useMemo(
    () => {
      const fromPayload = teamBreakdown?.climb_sources?.level_capability as ClimbLevelCapability | undefined;
      if (fromPayload) {
        return {
          best_level: fromPayload.best_level,
          best_level_label: fromPayload.best_level_label || 'No Level Data',
          best_level_score_0_100:
            typeof fromPayload.best_level_score_0_100 === 'number' ? fromPayload.best_level_score_0_100 : null,
          level_counts: {
            level1: Math.max(0, Math.floor(fromPayload.level_counts?.level1 ?? 0)),
            level2: Math.max(0, Math.floor(fromPayload.level_counts?.level2 ?? 0)),
            level3: Math.max(0, Math.floor(fromPayload.level_counts?.level3 ?? 0)),
          },
          matches_with_level: Math.max(0, Math.floor(fromPayload.matches_with_level ?? 0)),
          matches_with_success_no_level: Math.max(
            0,
            Math.floor(fromPayload.matches_with_success_no_level ?? 0),
          ),
          matches_considered: Math.max(0, Math.floor(fromPayload.matches_considered ?? 0)),
        } satisfies ClimbCapabilitySummary;
      }
      return summarizeClimbCapability(teamBreakdown?.recent_matches);
    },
    [teamBreakdown?.climb_sources?.level_capability, teamBreakdown?.recent_matches],
  );


  const robotProfileRows = useMemo<ProfileRow[]>(() => {
    if (!autoScoutProfile?.available) return [];
    return Object.entries(autoScoutProfile.fields).map(([key, field]) => profileFieldToRow(key, field));
  }, [autoScoutProfile]);

  const performanceRows = useMemo(() => {
    return [
      {
        label: 'Fuel per active-hub min',
        value: teamBreakdown?.averages?.fuel_scoring_rate ?? null,
        display: metric(teamBreakdown?.averages?.fuel_scoring_rate ?? null, 1),
        score: scoreAgainst(teamBreakdown?.averages?.fuel_scoring_rate, SEASON_BENCHMARKS.eliteFuelPerActiveMin),
      },
      {
        label: 'Auto Contribution',
        value: teamBreakdown?.averages?.auto_contribution ?? null,
        display: metric(teamBreakdown?.averages?.auto_contribution ?? null, 1),
        score: scoreAgainst(teamBreakdown?.averages?.auto_contribution, SEASON_BENCHMARKS.eliteAutoPoints),
      },
      {
        label: 'Climb Success',
        value: teamBreakdown?.averages?.climb_success_prob ?? null,
        display: pct(teamBreakdown?.averages?.climb_success_prob ?? null, 1),
        score:
          typeof teamBreakdown?.averages?.climb_success_prob === 'number'
            ? clampNumber(teamBreakdown.averages.climb_success_prob * 100, 0, 100)
            : null,
      },
      {
        label: 'Best Climb Level',
        value: climbCapability.best_level_score_0_100,
        display: climbCapability.best_level_label,
        score: climbCapability.best_level_score_0_100,
      },
      {
        label: 'Scoring Rating',
        value: teamRating?.robot_level_0_100 ?? null,
        display: metric(teamRating?.robot_level_0_100 ?? null, 1),
        score: teamRating?.robot_level_0_100 ?? null,
      },
      {
        label: 'Consistency',
        value: teamRating?.subscores.consistency ?? null,
        display: metric(teamRating?.subscores.consistency ?? null, 0),
        score: teamRating?.subscores.consistency ?? null,
      },
    ];
  }, [climbCapability, teamBreakdown, teamRating]);

  const ratingBasisLabel = useMemo(() => {
    if (teamRating?.basis === 'earlier_events') return 'From earlier events (not played here yet)';
    if (teamRating?.basis === 'last_season') return 'No matches this season yet: starts from last season';
    if (teamRating?.basis === 'rookie') return 'Rookie team: starts a little below a typical robot';
    if (teamRating?.basis === 'season_typical') return 'No official matches yet: a typical robot';
    return null;
  }, [teamRating?.basis]);

  // "87.7" on its own says nothing. Rank and field size travel together, and
  // the event is named whenever the rating did not come from the selected one —
  // the same disclaim-don't-hide rule the ratings fallback already follows.
  const ratingRange = ratingRangeLabel(teamRating?.rating_range_80);
  const ratingRankLabel = useMemo(() => {
    const rank = teamRating?.rank ?? null;
    const fieldSize = teamRating?.field_size ?? null;
    const likely = ratingRange ? `likely ${ratingRange}` : null;
    if (rank === null || fieldSize === null || fieldSize <= 0) return likely;
    const rankedAt = teamRating?.rank_event_key ?? null;
    const where = rankedAt && rankedAt !== selectedEventKey ? ` at ${eventDisplayName(rankedAt)}` : '';
    return `${ordinal(rank)} of ${fieldSize} by model rating${where}${likely ? ` · ${likely}` : ''}`;
  }, [teamRating, selectedEventKey, ratingRange]);

  const tbaRank = useMemo(() => {
    const status = asRecord(teamTbaEventStatus);
    const qual = asRecord(status?.qual);
    const ranking = asRecord(qual?.ranking);
    return parseNumber(ranking?.rank);
  }, [teamTbaEventStatus]);

  const tbaRecord = useMemo(() => {
    const status = asRecord(teamTbaEventStatus);
    const qual = asRecord(status?.qual);
    const ranking = asRecord(qual?.ranking);
    const record = asRecord(ranking?.record);
    if (!record) return MISSING;
    const wins = parseNumber(record.wins) ?? 0;
    const losses = parseNumber(record.losses) ?? 0;
    const ties = parseNumber(record.ties) ?? 0;
    return `${wins}-${losses}-${ties}`;
  }, [teamTbaEventStatus]);

  const tbaOverallStatus = useMemo(() => {
    const status = asRecord(teamTbaEventStatus);
    if (typeof status?.overall_status_str !== 'string') return MISSING;
    const cleaned = stripHtmlTags(status.overall_status_str);
    return cleaned || MISSING;
  }, [teamTbaEventStatus]);

  const tbaSeasonAwardsCount = teamTbaAwards.length;
  const tbaEventAwardsCount = useMemo(() => {
    if (!selectedEventKey) return null;
    return teamTbaAwards.filter((award) => {
      const row = asRecord(award);
      return typeof row?.event_key === 'string' && row.event_key.toLowerCase() === selectedEventKey;
    }).length;
  }, [selectedEventKey, teamTbaAwards]);

  const teamNumber = teamBreakdown?.team.team_number ?? teamNumberFromTeamKey(selectedTeamKey) ?? 0;
  const teamNickname = teamBreakdown?.team.nickname || `Team ${teamNumber}`;
  const _seasonScopeYear = teamBreakdown?.season_scope?.season_year ?? CURRENT_SEASON_YEAR;
  void _seasonScopeYear;

  function submitTeamAndEvent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedTeam = normalizeTeamKeyInput(teamInput);
    const normalizedEvent = eventInput.trim().toLowerCase();
    const currentSelectionKey = `${selectedTeamKey}|${selectedEventKey || ''}`;

    if (!normalizedTeam) {
      setErrorMessages(['Enter a team number, like 118.']);
      return;
    }

    setSelectedTeamKey(normalizedTeam);
    setSelectedEventKey(normalizedEvent);
    setTeamInput(teamInputLabel(normalizedTeam));
    setEventInput(normalizedEvent);
    setActiveTab('overview');
    const nextSelectionKey = `${normalizedTeam}|${normalizedEvent || ''}`;
    if (nextSelectionKey === currentSelectionKey) {
      triggerTeamReload('manual');
    }
    if (isMobileLayout) setMobileFinderOpen(false);
  }

  // A past event from the rating history: this team at that event, here.
  function openTeamAtEvent(eventKey: string) {
    const normalized = eventKey.toLowerCase();
    setSelectedEventKey(normalized);
    setEventInput(normalized);
    setActiveTab('overview');
    window.scrollTo({ top: 0 });
  }

  function openEventCenter(eventKey: string) {
    navigate(`/events?event=${eventKey.toLowerCase()}&tab=teams`);
  }

  function openMatchCenter(matchKey: string) {
    const normalizedMatchKey = normalizeMatchKey(matchKey, selectedEventKey);
    const matchEventKey = eventKeyFromMatchKey(normalizedMatchKey) || selectedEventKey;
    const params = new URLSearchParams();
    if (normalizedMatchKey) params.set('match', normalizedMatchKey);
    if (matchEventKey) params.set('event', matchEventKey);
    if (!matchEventKey) {
      navigate(`/match-center?${params.toString()}`);
      return;
    }
    setSelectedEventKey(matchEventKey);
    setEventInput(matchEventKey);
    writeCenterContext({ eventKey: matchEventKey, sourcePath: '/team-center' });
    if (isMobileLayout) setMobileFinderOpen(false);
    navigate(`/match-center?${params.toString()}`);
  }


  return (
    <div className={`center-layout center-layout-team mobile-finder-layout ${isMobileLayout && mobileFinderOpen ? 'mobile-finder-open' : ''}`.trim()}>
      {isMobileLayout && (mobileFinderOpen || !selectedTeamKey) ? (
        <MobileViewActions
          label="Team views"
          actions={
            mobileFinderOpen
              ? selectedTeamKey
                ? [{ label: `Back to Team ${teamNumberFromTeamKey(selectedTeamKey) ?? selectedTeamKey}`, onClick: () => setMobileFinderOpen(false), back: true }]
                : []
              : [{ label: 'Change team', onClick: () => setMobileFinderOpen(true) }]
          }
        />
      ) : null}
      <aside className="center-sidebar">
        <SurfaceCard title="Team Finder">
          <form className="center-stack-form" onSubmit={submitTeamAndEvent}>
            <label className="center-label" htmlFor="team-center-team-input">
              Team
            </label>
            <input
              id="team-center-team-input"
              value={teamInput}
              onChange={(event) => setTeamInput(event.target.value)}
              placeholder={TEAM_NUMBER_PLACEHOLDER}
              className="center-input"
            />
            <label className="center-label" htmlFor="team-center-event-input">
              Event
            </label>
            <select
              id="team-center-event-input"
              value={eventInput}
              onChange={(event) => setEventInput(event.target.value)}
              className="center-input"
            >
              <option value="">Overall (all events)</option>
              {selectedEventKey && !selectedEventInOptions ? (
                <option value={selectedEventKey}>Manual event ({selectedEventKey})</option>
              ) : null}
              {registeredEventOptions.map((event) => (
                <option key={`team-center-event-option-${event.event_key}`} value={event.event_key}>
                  {event.name || event.event_key}
                </option>
              ))}
            </select>
            <button type="submit" className="center-btn" disabled={loadingTeamData}>
              {loadingTeamData ? 'Loading…' : <><SearchIcon className="icon-inline" /> Load Team</>}
            </button>
          </form>

          <div className="center-status-row compact">
            {statusText !== 'Up to date' ? <span className="center-chip">{statusText}</span> : null}
            <span className="center-chip" title={`Refreshes every ${liveRefreshSec}s`}>Updated {relativeFromTimestamp(lastUpdatedAt)}</span>
          </div>
          {/* The per-request messages ("Schedule: …", "Event teams: …") stay in the tooltip. */}
          {errorMessages.length > 0 ? (
            <p className="center-callout warning" title={errorMessages.join('\n')}>
              Some of this team's data didn't load. It retries on its own.
            </p>
          ) : null}

          <div className="center-actions-row">
            <Link className="center-btn ghost" to={selectedEventKey ? `/events?event=${selectedEventKey}` : '/events'} title="Go to Events">
              <CalendarIcon className="icon-inline" /> This event
            </Link>
            {/* This used to open Match Center with no team, landing on an unrelated match.
                The team's own schedule here lives on the Event history tab. */}
            <button
              type="button"
              className="center-btn ghost"
              disabled={!selectedTeamKey}
              onClick={() => {
                setActiveTab('events');
                if (isMobileLayout) setMobileFinderOpen(false);
              }}
            >
              <ScoreboardIcon className="icon-inline" /> This team’s matches
            </button>
          </div>
        </SurfaceCard>
      </aside>

      <section className="center-main">
        {!selectedTeamKey ? (
          <SurfaceCard title="No Team Selected" subtitle="Enter a team in the Finder.">
            <EmptyState
              compact
              mascot
              title="Which team?"
              description="Type a number or a name to see its rating, fuel rate, matches and scouting."
              action={(
                <button
                  type="button"
                  className="center-btn"
                  onClick={() => {
                    if (isMobileLayout) setMobileFinderOpen(true);
                    window.setTimeout(() => document.getElementById('team-center-team-input')?.focus(), 0);
                  }}
                >
                  Find a team
                </button>
              )}
            />
          </SurfaceCard>
        ) : (
          <>
            {intelFailedFor === selectedTeamKey && !teamRating && !teamBreakdown ? (
              <p className="center-callout warning" role="status">
                This team's numbers didn't load, so the empty values below aren't real. It retries on its own.
              </p>
            ) : null}
            <TeamStickyBar
              watchRef={heroTabsRef}
              phone={isMobileLayout}
              teamNumber={teamNumber}
              nickname={teamNickname}
              rating={<CountUp key={selectedTeamKey} value={teamRating?.rating_0_100 ?? null} digits={1} />}
              detail={tbaRank !== null ? `#${tbaRank}` : undefined}
              tab={activeTab}
              onTab={setActiveTab}
              tabs={teamTabs.map((tab) => ({ value: tab, label: teamTabLabel(tab) }))}
            />
            {isMobileLayout ? (
              <>
                <div className="fm-team-hero">
                  <div className="fm-team-hero-top">
                    {teamLogo?.available && teamLogo.image_url ? (
                      <MediaImage
                        src={teamLogo.image_url}
                        alt={`Logo for ${selectedTeamKey}`}
                        className="fm-team-hero-logo"
                        decoding="async"
                        fallback={(
                          <span className="fm-team-hero-logo-fallback" aria-hidden="true">
                        {String(teamNumber).slice(0, 2)}
                      </span>
                        )}
                      />
                    ) : (
                      <span className="fm-team-hero-logo-fallback" aria-hidden="true">
                        {String(teamNumber).slice(0, 2)}
                      </span>
                    )}
                    <div className="fm-team-hero-info">
                      <h2>
                        Team {teamNumber}
                        {selectedTeamLiveForm?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                      </h2>
                      <span className="fm-team-hero-name">{teamNickname}</span>
                      {selectedEventKey ? (
                        <span className="fm-team-hero-event">{eventNameFor(selectedEventKey)}</span>
                      ) : skippedEventKey ? (
                        <span className="fm-team-hero-event">Didn't compete at {eventDisplayName(skippedEventKey)}; showing the whole season</span>
                      ) : null}
                    </div>
                    {!mobileFinderOpen ? (
                      <MobileViewActions inline label="Team views" actions={[{ label: 'Change', ariaLabel: 'Change team', onClick: () => setMobileFinderOpen(true) }]} />
                    ) : null}
                  </div>
                  <div className="fm-team-hero-stats">
                    <div className="fm-team-hero-stat highlight">
                      <strong><CountUp key={selectedTeamKey} value={teamRating?.rating_0_100 ?? null} digits={1} /></strong>
                      <span>Rating</span>
                    </div>
                    <div className="fm-team-hero-stat" title="TBA's per-team estimate from official results">
                      <strong><CountUp key={selectedTeamKey} value={typeof officialStats?.fuel_per_match === 'number' ? officialStats.fuel_per_match : null} /></strong>
                      <span>Fuel / match</span>
                    </div>
                    {selectedEventKey ? (
                      <div className="fm-team-hero-stat" title="Official ranking at this event">
                        <strong>{tbaRank !== null ? `#${tbaRank}` : MISSING}</strong>
                        <span>Rank</span>
                      </div>
                    ) : (
                      // No event picked: an event rank can't exist, so show how many events the season covers.
                      <div className="fm-team-hero-stat" title="Events this team is registered for this season">
                        <strong>{registeredEventOptions.length || MISSING}</strong>
                        <span>Events</span>
                      </div>
                    )}
                    <div className="fm-team-hero-stat" title="Official matches this rating has seen at this event">
                      <strong>{teamRating?.matches_here ?? '—'}</strong>
                      <span>Matches</span>
                    </div>
                  </div>
                  {!teamRating && previousSeason ? (
                    <p className="fm-team-hero-event" data-testid="previous-season-rating">
                      Not rated yet this season. {previousSeasonText(previousSeason)}
                    </p>
                  ) : null}
                  <div className="fm-team-hero-form"><TeamFormStrip entry={selectedTeamLiveForm} /></div>
                  {/* min-width 0: in the header's flex column this wrapper would otherwise be as wide as
                      all the tabs and push the page sideways on a narrow phone. */}
                  <div ref={heroTabsRef} style={{ minWidth: 0, maxWidth: '100%' }}>
                  <SegmentedTabs
                    motion
                    swipe
                    ariaLabel="Team center tabs"
                    value={activeTab}
                    onChange={setActiveTab}
                    items={teamTabs.map((tab) => ({
                      value: tab,
                      label: teamTabLabel(tab),
                    }))}
                  />
                  </div>
                </div>
              </>
            ) : (
            <SurfaceCard
              title={`Team ${teamNumber}`}
              subtitle={`${teamNickname}${selectedEventKey ? ` · ${eventNameFor(selectedEventKey)}` : skippedEventKey ? ` · Whole season (not at ${eventDisplayName(skippedEventKey)})` : ''}`}
            >
              <div className="center-team-hero">
                <div className="center-team-identity">
                  {teamLogo?.available && teamLogo.image_url ? (
                    <MediaImage
                      src={teamLogo.image_url}
                      alt={`Logo for ${selectedTeamKey}`}
                      className="center-team-logo"
                      decoding="async"
                      fallback={(
                        <span className="center-team-logo-fallback" aria-hidden="true">
                      {String(teamNumber).slice(0, 2)}
                    </span>
                      )}
                    />
                  ) : (
                    <span className="center-team-logo-fallback" aria-hidden="true">
                      {String(teamNumber).slice(0, 2)}
                    </span>
                  )}
                  <div>
                    <h3>
                      {teamNickname}
                      {selectedTeamLiveForm?.is_live ? <i className="center-live-dot" aria-hidden="true" /> : null}
                    </h3>
                    <p>{selectedTeamLiveForm?.is_live ? 'In Live Match' : ''}</p>
                    <div><TeamFormStrip entry={selectedTeamLiveForm} /></div>
                  </div>
                </div>
                <div className="center-team-hero">
                  <Stat
                    size="display"
                    label="FRCMOB rating"
                    value={<CountUp key={selectedTeamKey} value={teamRating?.rating_0_100 ?? null} digits={1} />}
                    sub={!teamRating && previousSeason ? 'Not rated yet this season' : ratingRankLabel}
                    confidence={teamRating?.confidence_0_1 ?? undefined}
                    reserveDetail
                  />
                  {!teamRating && previousSeason ? (
                    <p className="center-callout muted" data-testid="previous-season-rating">{previousSeasonText(previousSeason)}</p>
                  ) : null}
                  <div className="center-status-row compact">
                    {typeof officialStats?.fuel_per_match === 'number' ? (
                      <span className="center-chip" title="TBA's per-team estimate from official results">
                        {Math.round(officialStats.fuel_per_match)} fuel/match
                      </span>
                    ) : null}
                    <span className="center-chip">Qual rank: {tbaRank !== null ? `#${tbaRank}` : MISSING}</span>
                  </div>
                </div>
              </div>

              <div className="center-tabs-header" ref={heroTabsRef}>
                <SegmentedTabs
                  motion
                  ariaLabel="Team center tabs"
                  value={activeTab}
                  onChange={setActiveTab}
                  items={teamTabs.map((tab) => ({
                    value: tab,
                    label: teamTabLabel(tab),
                    icon: TEAM_TAB_ICONS[tab],
                  }))}
                />
              </div>
            </SurfaceCard>
            )}

            {/* The overview's own cards stand in while it loads (their values read "—"); a
                separate card here vanished when data came and jolted the page up. */}
            {loadingTeamData && !teamBreakdown && !teamRating && activeTab !== 'overview' ? (
              <SurfaceCard title="Loading" compactable>
                <SkeletonBlock rows={6} />
              </SurfaceCard>
            ) : null}

            {activeTab === 'overview' ? (
                <div className={isMobileLayout ? 'fm-content-stack' : 'center-content-grid'}>
                  <SurfaceCard
                    title="Robot Stats"
                    subtitle={
                      metricsAreEstimates
                        ? officialMetricsNote(officialStats)
                        : loadingTeamData && !teamBreakdown
                          ? "Loading this team's numbers…"
                          : undefined
                    }
                    compactable
                  >
                    <div className="center-kpi-grid team-stats-grid">
                      <article className="center-kpi-card">
                        <span><FlameIcon className="icon-inline icon-muted" /> Fuel / active min</span>
                        <strong>{metric(teamBreakdown?.averages?.fuel_scoring_rate ?? null, 1)}</strong>
                        {estimateChip('fuel_scoring_rate')}
                        {isEstimatedMetric('fuel_scoring_rate') && typeof officialStats?.fuel_per_match === 'number' ? (
                          <small>≈ {Math.round(officialStats.fuel_per_match)} fuel per match</small>
                        ) : null}
                      </article>
                      <article className="center-kpi-card">
                        <span><RobotIcon className="icon-inline icon-muted" /> Auto Contribution</span>
                        <strong>{metric(teamBreakdown?.averages?.auto_contribution ?? null, 1)}</strong>
                        {estimateChip('auto_contribution')}
                        {isEstimatedMetric('auto_contribution') ? <small>points per match</small> : null}
                      </article>
                      <article className="center-kpi-card">
                        <span><MountainIcon className="icon-inline icon-muted" /> Climb Success</span>
                        <strong>{pct(teamBreakdown?.averages?.climb_success_prob ?? null, 1)}</strong>
                        {climbRecord ? (
                          <>
                            <Chip size="sm" tone="neutral" className="kpi-tag" title="Official tower result for this robot in each match">Official</Chip>
                            <small>
                              {climbRecord.official_climbs ?? 0} of {climbRecord.official_matches ?? 0} matches
                            </small>
                          </>
                        ) : null}
                      </article>
                      <article className="center-kpi-card">
                        <span><TrophyIcon className="icon-inline icon-muted" /> Best Climb Level</span>
                        <strong>{climbCapability.best_level_label}</strong>
                        {/* With no climbs, "0/69 matches" only repeats the Climb Success tile. */}
                        {climbCapability.matches_with_level > 0 ? (
                          <small>
                            {climbCapability.matches_with_level}/{climbCapability.matches_considered} matches
                            {` · L3 ${climbCapability.level_counts.level3} · L2 ${climbCapability.level_counts.level2} · L1 ${climbCapability.level_counts.level1}`}
                          </small>
                        ) : null}
                      </article>
                    </div>
                  </SurfaceCard>

                  <SurfaceCard title="Rating Breakdown" compactable>
                    {/* The rating is these points per match on one season-wide
                        scale: the typical 2026 robot is 50. */}
                    <div className="center-kpi-grid">
                      <article className="center-kpi-card">
                        <span><GaugeIcon className="icon-inline icon-muted" /> Points / match</span>
                        <strong>{metric(teamRating?.expected_points?.total ?? null, 0)}</strong>
                        {ratingBasisLabel ? <small>{ratingBasisLabel}</small> : null}
                      </article>
                      <article className="center-kpi-card">
                        <span><GaugeIcon className="icon-inline icon-muted" /> Likely rating</span>
                        <strong>{ratingRange ?? MISSING}</strong>
                        <small>80% range: narrows as matches come in</small>
                      </article>
                      <article className="center-kpi-card">
                        <span><FlameIcon className="icon-inline icon-muted" /> Teleop</span>
                        <strong>{metric(teamRating?.expected_points?.teleop ?? null, 0)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><StopwatchIcon className="icon-inline icon-muted" /> Auto</span>
                        <strong>{metric(teamRating?.expected_points?.auto ?? null, 0)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><MountainIcon className="icon-inline icon-muted" /> Climb</span>
                        <strong>{metric(teamRating?.expected_points?.climb ?? null, 0)}</strong>
                      </article>
                    </div>
                    {teamRating?.recent_changes?.length ? (
                      <ul className="center-simple-list" aria-label="Recent rating changes">
                        {teamRating.recent_changes.map((change) => (
                          <li key={change.match_key}>
                            <span>{matchLabelFromKey(change.match_key) ?? change.match_key}</span>
                            <span className="muted">
                              {signedChange(change.rating_change, 1)} rating
                              {' · '}
                              {signedChange(change.points_change, 0)} pts/match
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </SurfaceCard>

                  <SurfaceCard title="Strengths" compactable>
                    {teamRating?.pros?.some((signal) => isRobotSignal(signal.label)) ? (
                      <ul className="center-simple-list">
                        {teamRating.pros.filter((signal) => isRobotSignal(signal.label)).slice(0, isMobileLayout ? 3 : 6).map((signal) => (
                          <li key={`pro-${signal.label}`}>
                            <span>{plainSignalLabel(signal.label)}</span>
                            <span className="muted">{signal.rationale || signalRankLabel(signal.percentile, 'strength')}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="center-callout muted">No standout strengths yet.</p>
                    )}
                  </SurfaceCard>

                  {/* An empty "No risks flagged." card on a phone was a screen of nothing. */}
                  {isMobileLayout && !teamRating?.cons?.some((signal) => isRobotSignal(signal.label)) ? null : (
                  <SurfaceCard title="Risks" compactable>
                    {teamRating?.cons?.some((signal) => isRobotSignal(signal.label)) ? (
                      <ul className="center-simple-list">
                        {teamRating.cons.filter((signal) => isRobotSignal(signal.label)).slice(0, isMobileLayout ? 3 : 6).map((signal) => (
                          <li key={`con-${signal.label}`}>
                            <span>{plainSignalLabel(signal.label)}</span>
                            <span className="muted">{signal.rationale || signalRankLabel(signal.percentile, 'risk')}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="center-callout muted">No risks flagged.</p>
                    )}
                  </SurfaceCard>
                  )}

                  {/* Model internals; on a phone the page is long enough without them. */}
                  {!isMobileLayout ? (
                  <SurfaceCard title="What the Rating Uses" compactable>
                    <p className="center-callout muted">
                      Official match results only: the points this team adds to its alliance, recent matches counting
                      most. Until it has played a few matches here, earlier events this season count too.
                    </p>
                    <div className="center-kpi-grid">
                      <article className="center-kpi-card">
                        <span><GaugeIcon className="icon-inline icon-muted" /> Matches here</span>
                        <strong>{metric(teamRating?.matches_here ?? null, 0)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><GaugeIcon className="icon-inline icon-muted" /> Earlier matches</span>
                        <strong>{metric(teamRating?.matches_before ?? null, 0)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><ClockIcon className="icon-inline icon-muted" /> Updated</span>
                        <strong>
                          {relativeFromTimestamp(
                            teamRating?.updated_at && Number.isFinite(Date.parse(teamRating.updated_at))
                              ? Date.parse(teamRating.updated_at)
                              : null,
                          )}
                        </strong>
                      </article>
                    </div>
                  </SurfaceCard>
                  ) : null}

                  <SurfaceCard title={selectedEventKey ? 'Ranking & Awards' : 'Awards'} compactable>
                    <div className="center-kpi-grid">
                      {/* Event cells only mean something when an event is picked; in the season
                          view they were three N/A boxes. */}
                      {selectedEventKey ? (
                        <>
                          {/* The phone header already shows the rank. */}
                          {!isMobileLayout ? (
                          <article className="center-kpi-card">
                            <span><HashIcon className="icon-inline icon-muted" /> Event Rank</span>
                            <strong>{tbaRank !== null ? `#${tbaRank}` : MISSING}</strong>
                          </article>
                          ) : null}
                          <article className="center-kpi-card">
                            <span><CheckCircleIcon className="icon-inline icon-muted" /> Event Record</span>
                            <strong>{tbaRecord}</strong>
                          </article>
                          <article className="center-kpi-card">
                            <span><AwardIcon className="icon-inline icon-muted" /> Event Awards</span>
                            <strong>{tbaEventAwardsCount !== null ? tbaEventAwardsCount : MISSING}</strong>
                          </article>
                        </>
                      ) : null}
                      <article className="center-kpi-card">
                        <span><AwardIcon className="icon-inline icon-muted" /> Season Awards</span>
                        <strong>{tbaSeasonAwardsCount}</strong>
                      </article>
                    </div>
                    {tbaOverallStatus !== MISSING ? <p className="center-callout muted">{tbaOverallStatus}</p> : null}
                  </SurfaceCard>
                </div>

            ) : null}

            {activeTab === 'performance' ? (
                <div className={isMobileLayout ? 'fm-content-stack' : undefined}>
                <SurfaceCard title="Performance Profile" compactable>
                  <div className="center-metric-bar-list">
                    {performanceRows.map((row) => (
                      <article key={`perf-${row.label}`} className="center-metric-bar-row">
                        <header>
                          <span>{row.label}</span>
                          <strong>{row.display}</strong>
                        </header>
                        <div className="center-metric-bar-track">
                          <div
                            className="center-metric-bar-fill"
                            style={{ width: `${row.score !== null ? clampNumber(row.score, 0, 100) : 0}%` }}
                          />
                        </div>
                      </article>
                    ))}
                  </div>
                </SurfaceCard>

                {/* ── Recording-based cards ────────────────────────────
                    Only reviewed phone recordings (or your team's own) feed these, and almost
                    no team has any yet, so a card shows only when it has something: an
                    empty field drawn as a heat map read like broken data. */}
                {robotProfileRows.length > 0 ? (
                <SurfaceCard
                  title="Robot Profile (from recordings)"
                  subtitle={`${autoScoutProfile?.sample_matches ?? 0} recorded match${autoScoutProfile?.sample_matches === 1 ? '' : 'es'}${autoScoutProfile?.is_last_season ? ' · last season' : ''}`}
                  collapsible
                  compactable
                >
                    <div className="center-metric-bar-list">
                      {robotProfileRows.map((row) => (
                        <article key={`robot-profile-${row.key}`} className="center-metric-bar-row">
                          <header>
                            <span>{row.label}</span>
                            <strong>{row.headline}</strong>
                          </header>
                          {row.barPct !== null ? (
                            <div className="center-metric-bar-track">
                              <div
                                className="center-metric-bar-fill"
                                style={{ width: `${clampNumber(row.barPct, 0, 100)}%` }}
                              />
                            </div>
                          ) : null}
                          <small className="muted">{row.sub}</small>
                        </article>
                      ))}
                    </div>
                </SurfaceCard>
                ) : null}

                {teamHeatmap && teamHeatmap.match_count > 0 ? (
                <SurfaceCard expandable title="Field Heatmap" collapsible compactable>
                      <FieldHeatmap data={teamHeatmap} />
                      <OwnRecordingsNote matches={teamHeatmap.own_unreviewed_matches} />
                </SurfaceCard>
                ) : null}

                {teamShiftPlay?.available && teamShiftPlay.offense && teamShiftPlay.defense ? (
                <SurfaceCard
                  expandable
                  title="Attack vs Defense"
                  subtitle={`Shift analysis · ${teamShiftPlay.sample_matches} match${teamShiftPlay.sample_matches === 1 ? '' : 'es'}`}
                  collapsible
                  compactable
                >
                    <div className="fm-content-stack">
                      <div className="center-metric-bar-list">
                        <article className="center-metric-bar-row">
                          <header>
                            <span>Offense (1–5)</span>
                            <strong>{teamShiftPlay.offense.level_1_5}</strong>
                          </header>
                          <div className="center-metric-bar-track">
                            <div className="center-metric-bar-fill" style={{ width: `${(teamShiftPlay.offense.level_1_5 / 5) * 100}%` }} />
                          </div>
                          <small className="muted">{Math.round(teamShiftPlay.offense.confidence_0_1 * 100)}% conf · own active shifts</small>
                        </article>
                        <article className="center-metric-bar-row">
                          <header>
                            <span>Defense (1–5)</span>
                            <strong>{teamShiftPlay.defense.assessable ? teamShiftPlay.defense.level_1_5 : '—'}</strong>
                          </header>
                          <div className="center-metric-bar-track">
                            <div className="center-metric-bar-fill" style={{ width: `${teamShiftPlay.defense.assessable ? (teamShiftPlay.defense.level_1_5 / 5) * 100 : 0}%` }} />
                          </div>
                          <small className="muted">
                            {teamShiftPlay.defense.assessable
                              ? `${Math.round(teamShiftPlay.defense.confidence_0_1 * 100)}% conf · opponent active shifts`
                              : 'Not assessable (needs opponent tracking)'}
                          </small>
                        </article>
                      </div>
                      <OwnRecordingsNote matches={teamShiftPlay.own_unreviewed_matches} />
                      <div className={isMobileLayout ? 'fm-content-stack' : 'center-content-grid'}>
                        <div>
                          <p className="center-eyebrow">Attack pattern (own shifts)</p>
                          {teamShiftPlay.attack_heatmap ? (
                            <FieldHeatmap data={teamShiftPlay.attack_heatmap} />
                          ) : (
                            <p className="center-callout muted">No attack data.</p>
                          )}
                        </div>
                        <div>
                          <p className="center-eyebrow">Defense pattern (opponent shifts)</p>
                          {teamShiftPlay.defense_heatmap && teamShiftPlay.defense.assessable ? (
                            <FieldHeatmap data={teamShiftPlay.defense_heatmap} />
                          ) : (
                            <p className="center-callout muted">No defense data.</p>
                          )}
                        </div>
                      </div>
                    </div>
                </SurfaceCard>
                ) : null}
                </div>

            ) : null}

            {activeTab === 'events' ? (
                <div className={isMobileLayout ? 'fm-content-stack' : 'center-content-grid'}>
                  <RatingHistoryCard teamKey={selectedTeamKey} onOpenEvent={openTeamAtEvent} />
                  <SurfaceCard title="Competitions" compactable>
                    {!teamCompetitions || teamCompetitions.registered_events.length === 0 ? (
                      <p className="center-callout muted">No competitions found.</p>
                    ) : (
                      <div className="center-list-stack">
                        {/* The card is the button: each one used to end in its own full-width
                            "Open event" button. */}
                        {teamCompetitions.registered_events.map((event) => (
                          <button
                            key={`competition-${event.event_key}`}
                            type="button"
                            className="center-list-item-card tc-competition-card"
                            title={`Open ${event.name}`}
                            onClick={() => openEventCenter(event.event_key)}
                          >
                            <span className="tc-competition-head">
                              <strong>{event.name}</strong>
                              <ChevronRightIcon className="icon-inline" />
                            </span>
                            <small>
                              {[
                                [event.start_date, event.end_date].filter(Boolean).join(' to ') || 'Dates not published',
                                [event.city, event.state_prov, event.country].filter(Boolean).join(', '),
                              ].filter(Boolean).join(' · ')}
                            </small>
                          </button>
                        ))}
                      </div>
                    )}
                  </SurfaceCard>

                  <SurfaceCard
                    expandable
                    title="Event Schedule Difficulty"
                    subtitle="How strong each match's opponents are, by rating."
                    compactable
                  >
                    {!selectedEventKey ? (
                      <p className="center-callout muted">Select an event to view difficulty.</p>
                    ) : null}
                    {selectedEventKey && scheduleRowsWithDifficulty.length === 0 ? (
                      <p className="center-callout muted">No schedule rows for this team.</p>
                    ) : null}
                    {scheduleRowsWithDifficulty.length > 0 ? (
                      <>
                        {/* One render at every width. The sort chips used to be
                            inside the mobile branch, so a desktop visitor could
                            not sort the schedule at all. */}
                        <div className="center-sort-chip-row" role="toolbar" aria-label="Schedule difficulty sorting">
                          {(
                            [
                              { id: 'time', label: 'By Time' },
                              { id: 'hardest', label: 'Hardest' },
                              { id: 'easiest', label: 'Easiest' },
                            ] as Array<{ id: TeamScheduleSortMode; label: string }>
                          ).map((option) => (
                            <button
                              key={`team-schedule-sort-${option.id}`}
                              type="button"
                              className={`center-sort-chip ${scheduleSortMode === option.id ? 'active' : ''}`.trim()}
                              onClick={() => setScheduleSortMode(option.id)}
                            >
                              {option.id === 'time' ? <ClockIcon className="icon-inline" /> : option.id === 'hardest' ? <FlameIcon className="icon-inline" /> : <EyeIcon className="icon-inline" />} {option.label}
                            </button>
                          ))}
                        </div>
                        <Table
                          columns={scheduleColumns}
                          rows={visibleScheduleRowsWithDifficulty}
                          rowKey={(row) => `team-schedule-${row.match_key}`}
                          cardBreakpoint={MOBILE_LAYOUT_BREAKPOINT}
                          // Two lines per match on a phone. As labelled cards each match was
                          // ~290px, so a 12-match schedule ran over five screens.
                          renderCards={(narrowRows) => (
                            <div className="tc-difficulty-list">
                              {narrowRows.map((row) => (
                                <button
                                  key={`team-schedule-card-${row.match_key}`}
                                  type="button"
                                  className="tc-difficulty-row"
                                  onClick={() => openMatchCenter(row.match_key)}
                                >
                                  <span className="tc-difficulty-top">
                                    <strong>{row.display_name}</strong>
                                    <span className={`center-alliance-pill ${row.alliance.toLowerCase()}`}>
                                      {row.alliance}{row.station ? ` ${row.station}` : ''}
                                    </span>
                                    <span className={`center-difficulty-pill ${row.difficulty_tier}`}>
                                      {metric(row.schedule_difficulty_0_10, 1)} / 10
                                    </span>
                                  </span>
                                  <small>
                                    vs {row.opponents.join(', ') || 'TBD'}
                                    {row.partners.length ? ` · with ${row.partners.join(', ')}` : ''}
                                  </small>
                                </button>
                              ))}
                            </div>
                          )}
                        />
                      </>
                    ) : null}
                    {sortedScheduleRowsWithDifficulty.length > visibleScheduleRowsWithDifficulty.length ? (
                      <div className="center-actions-row">
                        <button
                          type="button"
                          className="center-btn ghost"
                          onClick={() => setScheduleVisibleCount((prev) => prev + TEAM_SCHEDULE_AUTO_CHUNK_SIZE)}
                        >
                          <ChevronDownIcon className="icon-inline" /> Show More Schedule Rows ({visibleScheduleRowsWithDifficulty.length}/{sortedScheduleRowsWithDifficulty.length})
                        </button>
                      </div>
                    ) : null}
                  </SurfaceCard>
                </div>

            ) : null}

            {activeTab === 'media' ? (
                <div className={isMobileLayout ? 'fm-content-stack' : 'center-content-grid'}>
                  <SurfaceCard title="Team Logo" compactable>
                    {teamLogo?.available && teamLogo.image_url ? (
                      <MediaImage
                        src={teamLogo.image_url}
                        alt={`Team logo for ${selectedTeamKey}`}
                        className="center-media-image"
                        loading="lazy"
                        decoding="async"
                        fetchPriority="low"
                        fallback={<p className="center-callout muted">The team logo couldn&apos;t be loaded from its source.</p>}
                      />
                    ) : (
                      <p className="center-callout muted">{teamLogo?.reason || 'No team logo available.'}</p>
                    )}
                    {teamLogo?.view_url ? (
                      <a href={teamLogo.view_url} target="_blank" rel="noreferrer" className="center-btn ghost">
                        <ExternalLinkIcon className="icon-inline" /> Open Logo Source
                      </a>
                    ) : null}
                  </SurfaceCard>

                  <SurfaceCard title="Robot Picture" compactable>
                    {teamRobotImageLoading ? (
                      <div className="center-loading-state">
                        <SkeletonBlock rows={4} compact />
                      </div>
                    ) : teamRobotImage?.available && teamRobotImage.image_url ? (
                      <>
                        <MediaImage
                          src={teamRobotImage.image_url}
                          alt={`Robot picture for ${selectedTeamKey}`}
                          className="center-media-image"
                          loading="lazy"
                          decoding="async"
                          fetchPriority="low"
                          fallback={<p className="center-callout muted">This robot picture is no longer available at its source.</p>}
                        />
                        {teamRobotImage.year !== null && teamRobotImage.year !== CURRENT_SEASON_YEAR ? (
                          <p className="center-callout warning">
                            Showing {teamRobotImage.year} media — {CURRENT_SEASON_YEAR} unavailable.
                          </p>
                        ) : null}
                      </>
                    ) : (
                      <p className="center-callout muted">{teamRobotImage?.reason || 'No robot image available.'}</p>
                    )}
                    {teamRobotImage?.view_url ? (
                      <a href={teamRobotImage.view_url} target="_blank" rel="noreferrer" className="center-btn ghost">
                        <ExternalLinkIcon className="icon-inline" /> Open Robot Source
                      </a>
                    ) : null}
                  </SurfaceCard>
                </div>

            ) : null}

            {activeTab === 'advanced' ? (
                <div className={isMobileLayout ? 'fm-content-stack' : 'center-content-grid'}>
                  <SurfaceCard title="Model Details">
                    {!teamRating ? <p className="center-callout muted">No rating payload available for this team.</p> : null}
                    {teamRating ? (
                      <pre className="center-code-block">{JSON.stringify(teamRating, null, 2)}</pre>
                    ) : null}
                  </SurfaceCard>

                  <SurfaceCard title="TBA Raw Payloads">
                    <pre className="center-code-block">
                      {JSON.stringify(
                        {
                          event_status: teamTbaEventStatus,
                          season_awards_count: tbaSeasonAwardsCount,
                          event_awards_count: tbaEventAwardsCount,
                          season_awards: teamTbaAwards,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </SurfaceCard>
                </div>

            ) : null}
          </>
        )}
      </section>
    </div>
  );
}
