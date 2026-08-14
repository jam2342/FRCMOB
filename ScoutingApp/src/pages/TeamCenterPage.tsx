import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  getEventSchedule,
  getEventTeamLiveForm,
  getEventTeamsIntel,
  getTeamHeatmap,
  getTeamShiftPlay,
  getTeamIntel,
  getTeamLogo,
  getTeamRobotImage,
  isClientAdminModeEnabled,
} from '../api';
import type {
  AutoScoutProfile,
  AutoScoutProfileField,
  ClimbLevelCapability,
  EventTeamsIntelResponse,
  EventScheduleItem,
  EventTeamLiveFormEntry,
  EventTeamLiveFormResponse,
  EventTeamRatingItem,
  TeamBreakdownResponse,
  TeamCompetitionsResponse,
  TeamHeatmapResponse,
  TeamShiftPlayResponse,
  TeamLogoResponse,
  TeamRobotImageResponse,
} from '../api';
import { FieldHeatmap } from '../components/cv/FieldHeatmap';
import { SkeletonBlock } from '../components/ui/SkeletonBlock';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useLiveRefreshSetting } from '../hooks/useLiveRefreshSetting';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling, type SingleFlightPollReason } from '../hooks/useSingleFlightPolling';
import {
  asRecord,
  clampNumber,
  CURRENT_SEASON_YEAR,
  FALLBACK_SEASON_YEAR,
  fmtDateShort,
  meanNumber,
  metric,
  metricUnit,
  normalizeMatchKey,
  normalizeTeamKeyInput,
  isTransientAbortLikeError,
  parseNumber,
  pct,
  relativeFromTimestamp,
  summarizeFreshness,
  teamNumberFromTeamKey,
  titleizeKey,
} from './centerUtils';
import {
  GridIcon, BarChartIcon, CalendarIcon, ImageIcon, CodeIcon,
  FlameIcon, StopwatchIcon, RobotIcon, MountainIcon, ShieldIcon,
  ShieldCheckIcon, StarIcon, GaugeIcon, SteeringWheelIcon,
  TrophyIcon, HashIcon,
  ClockIcon, CheckCircleIcon, ExternalLinkIcon, ChevronDownIcon,
  SearchIcon, ScoreboardIcon, EyeIcon, AwardIcon,
} from '../components/ui/Icons';
import { readStoredCenterContext, writeCenterContext } from '../layout/centerContext';
import { cancelIdleWork, scheduleIdleWork } from '../utils/idle';

const BASE_TEAM_TABS = ['overview', 'performance', 'events', 'media'] as const;
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

function isTeamTab(value: string | null): value is TeamTab {
  return (
    value === 'overview' ||
    value === 'performance' ||
    value === 'events' ||
    value === 'media' ||
    value === 'advanced'
  );
}

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
  recentMatches: TeamBreakdownResponse['recent_matches'] | null | undefined,
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

function breakdownFromIntel(
  intel: Record<string, unknown>,
  teamKey: string,
): TeamBreakdownResponse | null {
  const analysis = asRecord(intel.analysis);
  const team = asRecord(intel.team);
  if (!analysis || !team) return null;
  const averages = asRecord(analysis.averages);
  const climbSourcesRaw = asRecord(analysis.climb_sources);
  const climbVideoRaw = asRecord(climbSourcesRaw?.video_only);
  const climbOfficialRaw = asRecord(climbSourcesRaw?.official_score_breakdown);
  const climbLevelRaw = asRecord(climbSourcesRaw?.level_capability);
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
        level_capability: climbLevelRaw
          ? {
              best_level:
                climbLevelRaw.best_level === 'level1' ||
                climbLevelRaw.best_level === 'level2' ||
                climbLevelRaw.best_level === 'level3'
                  ? climbLevelRaw.best_level
                  : null,
              best_level_label:
                typeof climbLevelRaw.best_level_label === 'string'
                  ? climbLevelRaw.best_level_label
                  : 'No Level Data',
              best_level_score_0_100: parseNumber(climbLevelRaw.best_level_score_0_100),
              level_counts: {
                level1: Math.max(
                  0,
                  Math.floor(parseNumber(asRecord(climbLevelRaw.level_counts)?.level1) ?? 0),
                ),
                level2: Math.max(
                  0,
                  Math.floor(parseNumber(asRecord(climbLevelRaw.level_counts)?.level2) ?? 0),
                ),
                level3: Math.max(
                  0,
                  Math.floor(parseNumber(asRecord(climbLevelRaw.level_counts)?.level3) ?? 0),
                ),
              },
              matches_with_level: Math.max(0, Math.floor(parseNumber(climbLevelRaw.matches_with_level) ?? 0)),
              matches_with_success_no_level: Math.max(
                0,
                Math.floor(parseNumber(climbLevelRaw.matches_with_success_no_level) ?? 0),
              ),
              matches_considered: Math.max(0, Math.floor(parseNumber(climbLevelRaw.matches_considered) ?? 0)),
            }
          : undefined,
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
    metric_units: (asRecord(analysis.metric_units) || {}) as Record<string, string>,
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
  teamNumber: number,
  nickname: string | null,
): EventTeamRatingItem | null {
  const rating = asRecord(intel.rating);
  if (!rating || !rating.available) return null;
  const subscores = asRecord(rating.subscores) || {};
  const ratingDetails = asRecord(rating.details);
  const rawFeatures = asRecord(ratingDetails?.raw_features);

  const rawNormEpa =
    parseNumber(rawFeatures?.statbotics_norm_epa) ??
    null;

  const normalizedEpa =
    typeof rawNormEpa === 'number'
      ? rawNormEpa > 300
        ? rawNormEpa / 100
        : rawNormEpa
      : null;

  const epaToScore =
    normalizedEpa === null
      ? null
      : clampNumber(((normalizedEpa - 8) / 28) * 100, 0, 100);

  const baseOverall = parseNumber(rating.rating_0_100) ?? 50;
  const baseRobot = parseNumber(rating.robot_level_0_100) ?? 50;
  const baseDriver = parseNumber(rating.driver_skill_0_100) ?? 50;

  const displayOverall =
    epaToScore === null ? baseOverall : clampNumber((baseOverall * 0.82) + (epaToScore * 0.18), 0, 100);
  const displayRobot =
    epaToScore === null ? baseRobot : clampNumber((baseRobot * 0.6) + (epaToScore * 0.4), 0, 100);
  const displayDriver =
    epaToScore === null ? baseDriver : clampNumber((baseDriver * 0.92) + (epaToScore * 0.08), 0, 100);

  return {
    event_key: typeof rating.context_event_key === 'string' ? rating.context_event_key : (intel.event_key as string) || '',
    team_key: teamKey,
    team_number: teamNumber,
    nickname,
    rating_0_100: Number(displayOverall.toFixed(1)),
    confidence_0_1: parseNumber(rating.confidence_0_1) ?? 0,
    robot_level_0_100: Number(displayRobot.toFixed(1)),
    driver_skill_0_100: Number(displayDriver.toFixed(1)),
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

export function TeamCenterPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobileLayout = useMobileLayout();
  const pageVisible = usePageVisibility();
  const didInitializeTeamTab = useRef(false);
  const liveRefreshSec = useLiveRefreshSetting();
  const storedCenterContext = readStoredCenterContext();
  const adminModeEnabled = isClientAdminModeEnabled();
  const teamTabs: readonly TeamTab[] = adminModeEnabled ? ALL_TEAM_TABS : BASE_TEAM_TABS;

  const defaultEventKey = (searchParams.get('event') || '').trim().toLowerCase();
  const defaultTeamKey =
    (searchParams.get('team') || storedCenterContext.teamKey || '').trim().toLowerCase();
  const tabParam = searchParams.get('tab');
  const defaultTab: TeamTab = isTeamTab(tabParam) && teamTabs.includes(tabParam) ? tabParam : 'overview';

  const [eventInput, setEventInput] = useState(defaultEventKey);
  const [teamInput, setTeamInput] = useState(defaultTeamKey);
  const [selectedEventKey, setSelectedEventKey] = useState(defaultEventKey);
  const [selectedTeamKey, setSelectedTeamKey] = useState(defaultTeamKey);
  const [activeTab, setActiveTab] = useState<TeamTab>(defaultTab);

  const [teamBreakdown, setTeamBreakdown] = useState<TeamBreakdownResponse | null>(null);
  const [autoScoutProfile, setAutoScoutProfile] = useState<AutoScoutProfile | null>(null);
  const [teamRating, setTeamRating] = useState<EventTeamRatingItem | null>(null);
  const [teamCompetitions, setTeamCompetitions] = useState<TeamCompetitionsResponse | null>(null);
  const [teamRobotImage, setTeamRobotImage] = useState<TeamRobotImageResponse | null>(null);
  const [teamLogo, setTeamLogo] = useState<TeamLogoResponse | null>(null);
  const [teamTbaEventStatus, setTeamTbaEventStatus] = useState<Record<string, unknown> | null>(null);
  const [teamTbaAwards, setTeamTbaAwards] = useState<Array<Record<string, unknown>>>([]);
  const [eventLiveForm, setEventLiveForm] = useState<EventTeamLiveFormResponse | null>(null);
  const [eventScheduleRows, setEventScheduleRows] = useState<EventScheduleItem[]>([]);
  const [eventTeams, setEventTeams] = useState<EventTeamsIntelResponse | null>(null);

  const [teamHeatmap, setTeamHeatmap] = useState<TeamHeatmapResponse | null>(null);
  const [heatmapLoading, setHeatmapLoading] = useState(false);
  const [teamShiftPlay, setTeamShiftPlay] = useState<TeamShiftPlayResponse | null>(null);
  const [shiftPlayLoading, setShiftPlayLoading] = useState(false);
  const [teamRobotImageLoading, setTeamRobotImageLoading] = useState(false);

  const [loadingTeamData, setLoadingTeamData] = useState(false);
  const [statusText, setStatusText] = useState('Enter a team number.');
  const [errorText, setErrorText] = useState('');
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [scheduleVisibleCount, setScheduleVisibleCount] = useState(TEAM_SCHEDULE_INITIAL_VISIBLE_COUNT);
  const [scheduleSortMode, setScheduleSortMode] = useState<TeamScheduleSortMode>('time');
  const [mobileFinderOpen, setMobileFinderOpen] = useState(() => !defaultTeamKey);
  const lastContextRef = useRef('');
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

  useEffect(() => {
    if (!selectedTeamKey) {
      didInitializeTeamTab.current = false;
      return;
    }
    if (!didInitializeTeamTab.current) {
      setActiveTab('overview');
      didInitializeTeamTab.current = true;
      return;
    }
  }, [selectedTeamKey]);

  useEffect(() => {
    const urlEventKey = (searchParams.get('event') || '').trim().toLowerCase();
    const urlTeamKey =
      (searchParams.get('team') || readStoredCenterContext().teamKey || '').trim().toLowerCase();
    const urlTabParam = searchParams.get('tab');
    const urlTab: TeamTab =
      isTeamTab(urlTabParam) && teamTabs.includes(urlTabParam) ? urlTabParam : 'overview';

    setSelectedEventKey((prev) => (prev === urlEventKey ? prev : urlEventKey));
    setSelectedTeamKey((prev) => (prev === urlTeamKey ? prev : urlTeamKey));
    setActiveTab((prev) => (prev === urlTab ? prev : urlTab));
    setEventInput((prev) => (prev === urlEventKey ? prev : urlEventKey));
    setTeamInput((prev) => (prev === urlTeamKey ? prev : urlTeamKey));
  }, [searchParams, teamTabs]);

  useEffect(() => {
    if (teamTabs.includes(activeTab)) return;
    setActiveTab('overview');
  }, [activeTab, teamTabs]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (selectedEventKey) next.set('event', selectedEventKey);
    if (selectedTeamKey) next.set('team', selectedTeamKey);
    next.set('tab', activeTab);

    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }

    const contextUpdate = {
      teamKey: selectedTeamKey,
      sourcePath: '/team-center',
      ...(selectedEventKey ? { eventKey: selectedEventKey } : {}),
    };
    writeCenterContext(contextUpdate);
  }, [activeTab, searchParams, selectedEventKey, selectedTeamKey, setSearchParams]);

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
    setErrorText('');
    setStatusText('Enter a team number.');
    setLoadingTeamData(false);
  }, [selectedTeamKey]);

  const loadTeamContext = useCallback(async (reason: SingleFlightPollReason): Promise<boolean> => {
    if (!selectedTeamKey) return true;

    const selectedEvent = selectedEventKey || undefined;
    const contextKey = `${selectedTeamKey}|${selectedEvent || ''}`;
    const contextChanged = lastContextRef.current !== contextKey;
    const shouldLoadEventSpecific = Boolean(selectedEvent);
    const shouldLoadEventDetails = shouldLoadEventSpecific && activeTab === 'events';
    if (contextChanged) {
      setTeamBreakdown(null);
      setAutoScoutProfile(null);
      setTeamRating(null);
      setTeamCompetitions(null);
      setTeamRobotImage(null);
      setTeamLogo(null);
      setTeamTbaEventStatus(null);
      setTeamTbaAwards([]);
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
    setErrorText('');
    setStatusText(
      contextChanged || reason === 'initial'
        ? `Loading ${selectedTeamKey}...`
        : `Refreshing ${selectedTeamKey}...`,
    );
    try {
      const eventDetailsMissing =
        shouldLoadEventDetails &&
        (!eventDetailsLoadRef.current.scheduleLoaded || !eventDetailsLoadRef.current.teamsLoaded);
      const shouldRefreshStatic = contextChanged || reason !== 'poll' || eventDetailsMissing;
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
            include_statbotics: false,
            include_season_fallback: true,
            include_rating_details: false,
            include_rating_signals: false,
            auto_heal_ratings: true,
          }, {
            timeoutMs: 25000,
          })
        : null;
      const [intelResult, logoResult, liveFormResult] =
        await Promise.allSettled([
          shouldRefreshStatic
            ? getTeamIntel(selectedTeamKey, {
                event_key: selectedEvent,
                preferred_year: CURRENT_SEASON_YEAR,
                fallback_year: FALLBACK_SEASON_YEAR,
                include_tba: true,
                include_statbotics: false,
                allow_season_fallback: true,
                auto_heal_ratings: true,
              }, {
                timeoutMs: 25000,
              })
            : Promise.resolve(null),
          shouldRefreshStatic
            ? getTeamLogo(selectedTeamKey, shouldLoadEventSpecific ? selectedEvent : undefined)
            : Promise.resolve(null),
          shouldLoadEventSpecific
            ? getEventTeamLiveForm(selectedEventKey, { form_window: 5, live_window_sec: 180 })
            : Promise.resolve(null),
        ]);

      const errors: string[] = [];

      if (shouldRefreshStatic && intelResult.status === 'fulfilled' && intelResult.value) {
        const intel = intelResult.value as unknown as Record<string, unknown>;
        const team = asRecord(intel.team);
        const teamNumber = parseNumber(team?.team_number) ?? teamNumberFromTeamKey(selectedTeamKey) ?? 0;
        const nickname = typeof team?.nickname === 'string' ? team.nickname : null;
        const breakdown = breakdownFromIntel(intel, selectedTeamKey);
        const competitions = competitionsFromIntel(intel, selectedTeamKey);
        const rating = breakdown ? ratingFromIntel(intel, selectedTeamKey, teamNumber, nickname) : null;
        const tba = asRecord(intel.tba);
        const tbaAwardsRaw = Array.isArray(tba?.awards) ? tba.awards : [];

        setTeamBreakdown(breakdown);
        setAutoScoutProfile((intel.auto_scout_profile as AutoScoutProfile | undefined) ?? null);
        setTeamCompetitions(competitions);
        setTeamRating(rating);
        setTeamTbaEventStatus(asRecord(tba?.event_status));
        setTeamTbaAwards(
          tbaAwardsRaw.filter((value): value is Record<string, unknown> => Boolean(asRecord(value))),
        );

        const intelWarnings = Array.isArray(intel.warnings)
          ? intel.warnings.map((warning) => String(warning || '').trim()).filter(Boolean)
          : [];
        if (intelWarnings.length > 0) {
          errors.push(...intelWarnings.map((warning) => `Intel: ${warning}`));
        }
      } else if (shouldRefreshStatic && intelResult.status === 'rejected') {
        if (!isTransientAbortLikeError(intelResult.reason)) {
          errors.push(`Intel: ${intelResult.reason instanceof Error ? intelResult.reason.message : 'failed'}`);
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
            setErrorText((current) => {
              if (!current) return `Event teams: ${detail}`;
              if (current.includes(`Event teams: ${detail}`)) return current;
              return `${current} | Event teams: ${detail}`;
            });
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
            setErrorText((current) => {
              if (!current) return `Schedule: ${detail}`;
              if (current.includes(`Schedule: ${detail}`)) return current;
              return `${current} | Schedule: ${detail}`;
            });
          });
      }

      setErrorText(errors.join(' | '));
      setStatusText(
        errors.length > 0
          ? `Partial data for ${selectedTeamKey}.`
          : `${selectedTeamKey} loaded.`,
      );
      setLastUpdatedAt(Date.now());
      return errors.length === 0;
    } finally {
      setLoadingTeamData(false);
    }
  }, [activeTab, selectedEventKey, selectedTeamKey]);

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
  useEffect(() => {
    if (activeTab !== 'performance' || !selectedTeamKey || !selectedEventKey) return;
    let cancelled = false;
    setHeatmapLoading(true);
    getTeamHeatmap(selectedTeamKey, selectedEventKey)
      .then((res) => { if (!cancelled) setTeamHeatmap(res); })
      .catch(() => { if (!cancelled) setTeamHeatmap(null); })
      .finally(() => { if (!cancelled) setHeatmapLoading(false); });
    return () => { cancelled = true; };
  }, [activeTab, selectedTeamKey, selectedEventKey]);

  /* ── shift-play (offense/defense + attack/defense heat maps) ────── */
  useEffect(() => {
    if (activeTab !== 'performance' || !selectedTeamKey || !selectedEventKey) return;
    let cancelled = false;
    setShiftPlayLoading(true);
    getTeamShiftPlay(selectedTeamKey, selectedEventKey)
      .then((res) => { if (!cancelled) setTeamShiftPlay(res); })
      .catch(() => { if (!cancelled) setTeamShiftPlay(null); })
      .finally(() => { if (!cancelled) setShiftPlayLoading(false); });
    return () => { cancelled = true; };
  }, [activeTab, selectedTeamKey, selectedEventKey]);

  useEffect(() => {
    if (activeTab !== 'media' || !selectedTeamKey) return;
    const contextKey = `${selectedTeamKey}|${selectedEventKey || ''}`;
    if (robotImageFetchContextRef.current === contextKey) return;
    robotImageFetchContextRef.current = contextKey;
    let cancelled = false;
    setTeamRobotImageLoading(true);
    getTeamRobotImage(selectedTeamKey, selectedEventKey || undefined)
      .then((res) => {
        if (cancelled) return;
        setTeamRobotImage(res);
      })
      .catch(() => {
        if (cancelled) return;
        setTeamRobotImage(null);
      })
      .finally(() => {
        if (!cancelled) setTeamRobotImageLoading(false);
      });
    return () => {
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

  const selectedEventInOptions = useMemo(
    () => !selectedEventKey || registeredEventOptions.some((event) => event.event_key === selectedEventKey),
    [registeredEventOptions, selectedEventKey],
  );

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
      const epaProxyFromRating = ratingValue !== null ? clampNumber(5 + ratingValue * 1.15, 12, 130) : null;
      const resolvedEpa = epaProxyFromRating;
      if (resolvedEpa !== null) {
        map[teamKey] = resolvedEpa;
      }
    }
    return map;
  }, [eventTeams]);

  const defaultEventEpa = useMemo(() => {
    const values = Object.values(eventTeamEpaByKey).filter((value) => Number.isFinite(value));
    if (values.length === 0) return 45;
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

        // Schedule difficulty is intentionally simple + fast: opponent average EPA only.
        // We normalize against the current event EPA distribution so the 1-10 scale
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

  const freshnessSummary = useMemo(
    () => summarizeFreshness(teamBreakdown?.data_freshness || null),
    [teamBreakdown?.data_freshness],
  );

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

  const robotEpaSummary = (() => {
    const ratingDetails = asRecord(teamRating?.details);
    const ratingRawFeatures = asRecord(ratingDetails?.raw_features);
    const ratingRawNormEpa = parseNumber(ratingRawFeatures?.statbotics_norm_epa);
    const ratingProxy = parseNumber(teamRating?.rating_0_100);
    const rawValue = ratingRawNormEpa ?? (ratingProxy !== null ? 1200 + ratingProxy * 8.5 : null);
    const value =
      typeof rawValue === 'number'
        ? rawValue > 300
          ? rawValue / 100
          : rawValue
        : null;
    const source =
      ratingRawNormEpa !== null
        ? 'Model Feature EPA'
        : ratingProxy !== null
          ? 'Rating-derived EPA proxy'
          : 'Unavailable';
    return { value, source };
  })();

  const robotProfileRows = useMemo<ProfileRow[]>(() => {
    if (!autoScoutProfile?.available) return [];
    return Object.entries(autoScoutProfile.fields).map(([key, field]) => profileFieldToRow(key, field));
  }, [autoScoutProfile]);

  const performanceRows = useMemo(() => {
    return [
      {
        label: 'Fuel Scoring Rate (per min)',
        value: teamBreakdown?.averages?.fuel_scoring_rate ?? null,
        display: metric(teamBreakdown?.averages?.fuel_scoring_rate ?? null, 2),
        score: teamBreakdown?.averages?.fuel_scoring_rate
          ? clampNumber(teamBreakdown.averages.fuel_scoring_rate * 20, 0, 100)
          : null,
      },
      {
        label: 'Cycle Speed',
        value: teamBreakdown?.averages?.cycle_time_sec ?? null,
        display: metric(teamBreakdown?.averages?.cycle_time_sec ?? null, 1),
        score:
          typeof teamBreakdown?.averages?.cycle_time_sec === 'number'
            ? clampNumber(100 - teamBreakdown.averages.cycle_time_sec * 4, 0, 100)
            : null,
      },
      {
        label: 'Auto Contribution',
        value: teamBreakdown?.averages?.auto_contribution ?? null,
        display: metric(teamBreakdown?.averages?.auto_contribution ?? null, 2),
        score:
          typeof teamBreakdown?.averages?.auto_contribution === 'number'
            ? clampNumber(teamBreakdown.averages.auto_contribution * 10, 0, 100)
            : null,
      },
      {
        label: 'Robot EPA',
        value: robotEpaSummary.value,
        display: metric(robotEpaSummary.value, 1),
        score:
          typeof robotEpaSummary.value === 'number'
            ? clampNumber((robotEpaSummary.value / 130) * 100, 0, 100)
            : null,
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
        label: 'Defense Presence',
        value: teamRating?.subscores.defense_presence ?? null,
        display: metric(teamRating?.subscores.defense_presence ?? null, 1),
        score: teamRating?.subscores.defense_presence ?? null,
      },
      {
        label: 'Penalty Discipline',
        value: teamRating?.subscores.penalty_discipline ?? null,
        display: metric(teamRating?.subscores.penalty_discipline ?? null, 1),
        score: teamRating?.subscores.penalty_discipline ?? null,
      },
      {
        label: 'Robot Level',
        value: teamRating?.robot_level_0_100 ?? null,
        display: metric(teamRating?.robot_level_0_100 ?? null, 1),
        score: teamRating?.robot_level_0_100 ?? null,
      },
      {
        label: 'Driver Skill',
        value: teamRating?.driver_skill_0_100 ?? null,
        display: metric(teamRating?.driver_skill_0_100 ?? null, 1),
        score: teamRating?.driver_skill_0_100 ?? null,
      },
    ];
  }, [climbCapability, robotEpaSummary.value, teamBreakdown, teamRating]);

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
    if (!record) return 'N/A';
    const wins = parseNumber(record.wins) ?? 0;
    const losses = parseNumber(record.losses) ?? 0;
    const ties = parseNumber(record.ties) ?? 0;
    return `${wins}-${losses}-${ties}`;
  }, [teamTbaEventStatus]);

  const tbaOverallStatus = useMemo(() => {
    const status = asRecord(teamTbaEventStatus);
    if (typeof status?.overall_status_str !== 'string') return 'N/A';
    const cleaned = stripHtmlTags(status.overall_status_str);
    return cleaned || 'N/A';
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
      setErrorText('Use a valid team key like frc118 or team number like 118.');
      return;
    }

    setSelectedTeamKey(normalizedTeam);
    setSelectedEventKey(normalizedEvent);
    setTeamInput(normalizedTeam);
    setEventInput(normalizedEvent);
    setActiveTab('overview');
    const nextSelectionKey = `${normalizedTeam}|${normalizedEvent || ''}`;
    if (nextSelectionKey === currentSelectionKey) {
      triggerTeamReload('manual');
    }
    if (isMobileLayout) setMobileFinderOpen(false);
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
      {isMobileLayout ? (
        <SegmentedTabs
          className="mobile-view-toggle"
          itemClassName="mobile-view-toggle-btn"
          ariaLabel="Team mobile view switch"
          value={mobileFinderOpen ? 'finder' : 'center'}
          onChange={(next) => setMobileFinderOpen(next === 'finder')}
          items={[
            { value: 'finder', label: 'Team Finder' },
            { value: 'center', label: 'Team Center', disabled: !selectedTeamKey },
          ]}
        />
      ) : null}
      <aside className="center-sidebar">
        <SurfaceCard title="Team Finder" subtitle="Load team + event context.">
          <form className="center-stack-form" onSubmit={submitTeamAndEvent}>
            <label className="center-label" htmlFor="team-center-team-input">
              Team
            </label>
            <input
              id="team-center-team-input"
              value={teamInput}
              onChange={(event) => setTeamInput(event.target.value)}
              placeholder="frc118 or 118"
              className="center-input"
            />
            <label className="center-label" htmlFor="team-center-event-input">
              Event (optional)
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
                  {event.name || event.event_key} ({event.event_key})
                </option>
              ))}
            </select>
            <button type="submit" className="center-btn" disabled={loadingTeamData}>
              {loadingTeamData ? 'Loading...' : <><SearchIcon className="icon-inline" /> Load Team</>}
            </button>
          </form>

          <div className="center-status-row compact">
            <span className="center-chip">{statusText}</span>
            <span className="center-chip">{relativeFromTimestamp(lastUpdatedAt)} · {liveRefreshSec}s</span>
          </div>
          {errorText ? <p className="center-callout warning">{errorText}</p> : null}

          <div className="center-actions-row">
            <Link className="center-btn ghost" to={selectedEventKey ? `/events?event=${selectedEventKey}` : '/events'} title="Go to Event Center">
              <CalendarIcon className="icon-inline" /> Event Center
            </Link>
            <Link
              className="center-btn ghost"
              to={selectedEventKey ? `/match-center?event=${selectedEventKey}` : '/match-center'}
              title="Go to Match Center"
            >
              <ScoreboardIcon className="icon-inline" /> Match Center
            </Link>
          </div>
        </SurfaceCard>
      </aside>

      <section className="center-main">
        {!selectedTeamKey ? (
          <SurfaceCard title="No Team Selected" subtitle="Enter a team in the Finder.">
            <p className="center-callout muted">
              Select a team to view scouting intel.
            </p>
          </SurfaceCard>
        ) : (
          <>
            {isMobileLayout ? (
              <>
                <div className="fm-team-hero">
                  <div className="fm-team-hero-top">
                    {teamLogo?.available && teamLogo.image_url ? (
                      <img
                        src={teamLogo.image_url}
                        alt={`Logo for ${selectedTeamKey}`}
                        className="fm-team-hero-logo"
                        decoding="async"
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
                      {selectedEventKey ? <span className="fm-team-hero-event">Event {selectedEventKey}</span> : null}
                    </div>
                  </div>
                  <div className="fm-team-hero-stats">
                    <div className="fm-team-hero-stat highlight">
                      <strong>{metric(teamRating?.rating_0_100 ?? null, 1)}</strong>
                      <span>Rating</span>
                    </div>
                    <div className="fm-team-hero-stat">
                      <strong>{metric(robotEpaSummary.value, 1)}</strong>
                      <span>EPA</span>
                    </div>
                    <div className="fm-team-hero-stat">
                      <strong>{tbaRank !== null ? `#${tbaRank}` : 'N/A'}</strong>
                      <span>Rank</span>
                    </div>
                    <div className="fm-team-hero-stat">
                      <strong>{pct(teamRating?.confidence_0_1 ?? null, 0)}</strong>
                      <span>Conf</span>
                    </div>
                    <div className={`fm-team-hero-stat ${freshnessSummary.state === 'stale' ? 'warning' : ''}`}>
                      <strong>{teamBreakdown?.matches_analyzed ?? 0}</strong>
                      <span>Analyzed</span>
                    </div>
                  </div>
                  <div className="fm-team-hero-form">{teamFormStrip(selectedTeamLiveForm)}</div>
                  <SegmentedTabs
                    className="fm-tab-bar"
                    itemClassName="fm-tab"
                    ariaLabel="Team center tabs"
                    value={activeTab}
                    onChange={setActiveTab}
                    items={teamTabs.map((tab) => ({
                      value: tab,
                      label: titleizeKey(tab),
                    }))}
                  />
                </div>
              </>
            ) : (
            <SurfaceCard
              title={`Team ${teamNumber} (${selectedTeamKey})`}
              subtitle={`${teamNickname}${selectedEventKey ? ` · Event ${selectedEventKey}` : ''}`}
            >
              <div className="center-team-hero">
                <div className="center-team-identity">
                  {teamLogo?.available && teamLogo.image_url ? (
                    <img
                      src={teamLogo.image_url}
                      alt={`Logo for ${selectedTeamKey}`}
                      className="center-team-logo"
                      decoding="async"
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
                    <div>{teamFormStrip(selectedTeamLiveForm)}</div>
                  </div>
                </div>
                <div className="center-status-row compact">
                  <span className="center-chip">{teamBreakdown?.matches_analyzed ?? 0} analyzed</span>
                  <span className="center-chip">Rating: {metric(teamRating?.rating_0_100 ?? null, 1)}</span>
                  <span className="center-chip">Conf: {pct(teamRating?.confidence_0_1 ?? null, 1)}</span>
                  <span className="center-chip" title="Expected Points Added">EPA: {metric(robotEpaSummary.value, 1)}</span>
                  <span className="center-chip">Rank: {tbaRank !== null ? `#${tbaRank}` : 'N/A'}</span>
                  <span className={`center-chip freshness ${freshnessSummary.state}`}>{freshnessSummary.label}</span>
                </div>
                {freshnessSummary.detail ? (
                  <p className={`center-callout ${freshnessSummary.state === 'stale' ? 'warning' : 'muted'}`}>
                    {freshnessSummary.detail}
                  </p>
                ) : null}
              </div>

              <div className="center-tabs-header">
                <SegmentedTabs
                  className="center-tabs"
                  itemClassName="center-tab-btn"
                  ariaLabel="Team center tabs"
                  value={activeTab}
                  onChange={setActiveTab}
                  items={teamTabs.map((tab) => ({
                    value: tab,
                    label: titleizeKey(tab),
                    icon: TEAM_TAB_ICONS[tab],
                  }))}
                />
              </div>
            </SurfaceCard>
            )}

            {loadingTeamData && !teamBreakdown && !teamRating ? (
              <SurfaceCard title="Loading" subtitle="Loading team data." compactable>
                <SkeletonBlock rows={6} />
              </SurfaceCard>
            ) : null}

            {activeTab === 'overview' ? (
              <SurfaceCardGroup groupId="team-center-overview">
                <div className={isMobileLayout ? 'fm-content-stack' : 'center-content-grid'}>
                  <SurfaceCard
                    title="FRC Scouting Metrics"
                    subtitle="Scouting pipeline metrics."
                    compactable
                  >
                    <div className="center-kpi-grid">
                      <article className="center-kpi-card">
                        <span><FlameIcon className="icon-inline icon-muted" /> Fuel Rate / min</span>
                        <strong>{metric(teamBreakdown?.averages?.fuel_scoring_rate ?? null, 2)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><StopwatchIcon className="icon-inline icon-muted" /> Cycle Time</span>
                        <strong>{metricUnit(teamBreakdown?.averages?.cycle_time_sec ?? null, 2, 's')}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><RobotIcon className="icon-inline icon-muted" /> Auto Contribution</span>
                        <strong>{metric(teamBreakdown?.averages?.auto_contribution ?? null, 2)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><MountainIcon className="icon-inline icon-muted" /> Climb Success</span>
                        <strong>{pct(teamBreakdown?.averages?.climb_success_prob ?? null, 1)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><TrophyIcon className="icon-inline icon-muted" /> Best Climb Level</span>
                        <strong>{climbCapability.best_level_label}</strong>
                        <small>
                          {climbCapability.matches_with_level}/{climbCapability.matches_considered} matches
                          {climbCapability.level_counts.level3 > 0 || climbCapability.level_counts.level2 > 0 || climbCapability.level_counts.level1 > 0
                            ? ` · L3 ${climbCapability.level_counts.level3} · L2 ${climbCapability.level_counts.level2} · L1 ${climbCapability.level_counts.level1}`
                            : ''}
                        </small>
                      </article>
                      <article className="center-kpi-card">
                        <span><ShieldIcon className="icon-inline icon-muted" /> Defense Time</span>
                        <strong>{metricUnit(teamBreakdown?.averages?.defensive_engagement_sec ?? null, 1, 's')}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><ShieldCheckIcon className="icon-inline icon-muted" /> Reliability</span>
                        <strong>{pct(teamBreakdown?.averages?.reliability_score ?? null, 1)}</strong>
                      </article>
                    </div>
                  </SurfaceCard>

                  <SurfaceCard title="Scouting Rating" subtitle="Model rating and confidence." compactable>
                    <div className="center-kpi-grid">
                      <article className="center-kpi-card">
                        <span><StarIcon className="icon-inline icon-muted" /> Overall Rating</span>
                        <strong>{metric(teamRating?.rating_0_100 ?? null, 1)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><ShieldCheckIcon className="icon-inline icon-muted" /> Model Confidence</span>
                        <strong>{pct(teamRating?.confidence_0_1 ?? null, 1)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><BarChartIcon className="icon-inline icon-muted" /> Robot EPA</span>
                        <strong>{metric(robotEpaSummary.value, 1)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><GaugeIcon className="icon-inline icon-muted" /> Robot Level</span>
                        <strong>{metric(teamRating?.robot_level_0_100 ?? null, 1)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><SteeringWheelIcon className="icon-inline icon-muted" /> Driver Skill</span>
                        <strong>{metric(teamRating?.driver_skill_0_100 ?? null, 1)}</strong>
                      </article>
                    </div>
                    <p className="center-callout muted">EPA Source: {robotEpaSummary.source}</p>
                  </SurfaceCard>

                  <SurfaceCard title="Strengths" compactable>
                    {teamRating?.pros?.length ? (
                      <ul className="center-simple-list">
                        {teamRating.pros.slice(0, 6).map((signal) => (
                          <li key={`pro-${signal.label}`}>
                            <span>{signal.label}</span>
                            <span>
                              {metric(signal.metric_value, 2)} / {metric(signal.percentile, 1)}%
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="center-callout muted">No pro signals available yet.</p>
                    )}
                  </SurfaceCard>

                  <SurfaceCard title="Risks" compactable>
                    {teamRating?.cons?.length ? (
                      <ul className="center-simple-list">
                        {teamRating.cons.slice(0, 6).map((signal) => (
                          <li key={`con-${signal.label}`}>
                            <span>{signal.label}</span>
                            <span>
                              {metric(signal.metric_value, 2)} / {metric(signal.percentile, 1)}%
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="center-callout muted">No con signals available yet.</p>
                    )}
                  </SurfaceCard>

                  <SurfaceCard title="Model Signal Snapshot" subtitle="Rating model and feature-derived signals." compactable>
                    <div className="center-kpi-grid">
                      <article className="center-kpi-card">
                        <span><BarChartIcon className="icon-inline icon-muted" /> EPA Proxy</span>
                        <strong>{metric(robotEpaSummary.value, 2)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><GaugeIcon className="icon-inline icon-muted" /> Results Anchor</span>
                        <strong>{metric(teamRating?.subscores.results_anchor ?? null, 1)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><GaugeIcon className="icon-inline icon-muted" /> Throughput</span>
                        <strong>{metric(teamRating?.subscores.throughput ?? null, 1)}</strong>
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
                    <p className="center-callout muted">Signal source: {robotEpaSummary.source}</p>
                  </SurfaceCard>

                  <SurfaceCard title="TBA Snapshot" subtitle="TBA status and awards." compactable>
                    <div className="center-kpi-grid">
                      <article className="center-kpi-card">
                        <span><HashIcon className="icon-inline icon-muted" /> Event Rank</span>
                        <strong>{tbaRank !== null ? `#${tbaRank}` : 'N/A'}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><CheckCircleIcon className="icon-inline icon-muted" /> Event Record</span>
                        <strong>{tbaRecord}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><AwardIcon className="icon-inline icon-muted" /> Event Awards</span>
                        <strong>{tbaEventAwardsCount !== null ? tbaEventAwardsCount : 'N/A'}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><AwardIcon className="icon-inline icon-muted" /> Season Awards</span>
                        <strong>{tbaSeasonAwardsCount}</strong>
                      </article>
                    </div>
                    <p className="center-callout muted">Status: {tbaOverallStatus}</p>
                  </SurfaceCard>
                </div>
              </SurfaceCardGroup>
            ) : null}

            {activeTab === 'performance' ? (
              <SurfaceCardGroup groupId="team-center-performance">
                <div className={isMobileLayout ? 'fm-content-stack' : undefined}>
                <SurfaceCard title="Performance Profile" subtitle="Model outputs and subscores." compactable>
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

                {/* ── Robot Profile (video-derived auto-scout) ─────── */}
                <SurfaceCard
                  title="Robot Profile (from video)"
                  subtitle={
                    autoScoutProfile?.available
                      ? `${autoScoutProfile.sample_matches} analyzed match${autoScoutProfile.sample_matches === 1 ? '' : 'es'}${autoScoutProfile.is_last_season ? ' · last season' : ''}`
                      : 'Auto-scout signals from match video.'
                  }
                  collapsible
                  compactable
                >
                  {robotProfileRows.length === 0 ? (
                    <p className="center-callout muted">No video-derived signals yet for this team.</p>
                  ) : (
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
                  )}
                </SurfaceCard>

                {/* ── Positional Heatmap ──────────────────────────── */}
                <SurfaceCard title="Field Heatmap" subtitle="Positional density from CV tracking." collapsible compactable>
                  {heatmapLoading ? (
                    <div style={{ minHeight: 180 }}>
                      <SkeletonBlock rows={5} compact />
                    </div>
                  ) : teamHeatmap ? (
                    <FieldHeatmap data={teamHeatmap} />
                  ) : (
                    <p className="center-callout muted">No tracking data available for this team at this event.</p>
                  )}
                </SurfaceCard>

                {/* ── Attack vs Defense (shift analysis) ──────────── */}
                <SurfaceCard
                  title="Attack vs Defense"
                  subtitle={
                    teamShiftPlay?.available
                      ? `Shift analysis · ${teamShiftPlay.sample_matches} match${teamShiftPlay.sample_matches === 1 ? '' : 'es'}`
                      : 'Offense vs defense from the match shift schedule.'
                  }
                  collapsible
                  compactable
                >
                  {shiftPlayLoading ? (
                    <div style={{ minHeight: 180 }}>
                      <SkeletonBlock rows={5} compact />
                    </div>
                  ) : teamShiftPlay?.available && teamShiftPlay.offense && teamShiftPlay.defense ? (
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
                  ) : (
                    <p className="center-callout muted">No shift analysis available for this team at this event.</p>
                  )}
                </SurfaceCard>
                </div>
              </SurfaceCardGroup>
            ) : null}

            {activeTab === 'events' ? (
              <SurfaceCardGroup groupId="team-center-events">
                <div className={isMobileLayout ? 'fm-content-stack' : 'center-content-grid'}>
                  <SurfaceCard title="Competitions" subtitle="Registered events." compactable>
                    {!teamCompetitions || teamCompetitions.registered_events.length === 0 ? (
                      <p className="center-callout muted">No competitions found.</p>
                    ) : (
                      <div className="center-list-stack">
                        {teamCompetitions.registered_events.map((event) => (
                          <article key={`competition-${event.event_key}`} className="center-list-item-card">
                            <header>
                              <strong>{event.name}</strong>
                              <small>
                                {event.event_key} · {event.year}
                              </small>
                            </header>
                            <small>
                              {[event.start_date, event.end_date].filter(Boolean).join(' to ') || 'Dates not published'}
                            </small>
                            <small>
                              {[event.city, event.state_prov, event.country].filter(Boolean).join(', ') ||
                                'Location unavailable'}
                            </small>
                            <div className="center-actions-row">
                              <button type="button" className="center-btn" title={`Open ${event.name}`} onClick={() => openEventCenter(event.event_key)}>
                                <CalendarIcon className="icon-inline" /> Event Center
                              </button>
                            </div>
                          </article>
                        ))}
                      </div>
                    )}
                  </SurfaceCard>

                  <SurfaceCard
                    title="Event Schedule Difficulty"
                    subtitle="Opponent EPA-based schedule pressure."
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
                        {isMobileLayout ? (
                          <>
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
                            <div className="center-mobile-card-list">
                              {visibleScheduleRowsWithDifficulty.map((row) => (
                                <article key={`team-schedule-mobile-${row.match_key}`} className="center-mobile-data-card">
                                  <header>
                                    <button
                                      type="button"
                                      className="center-inline-link"
                                      onClick={() => openMatchCenter(row.match_key)}
                                    >
                                      {row.display_name}
                                    </button>
                                    <span className={`center-difficulty-pill ${row.difficulty_tier}`}>
                                      {metric(row.schedule_difficulty_0_10, 1)} / 10
                                    </span>
                                  </header>
                                  <p className="meta">
                                    {fmtDateShort(row.scheduled_time)}
                                  </p>
                                  <div className="center-mobile-data-grid">
                                    <span>
                                      Alliance
                                      <strong>
                                        {row.alliance}
                                        {row.station ? ` (${row.station})` : ''}
                                      </strong>
                                    </span>
                                    <span>
                                      Opp Avg EPA
                                      <strong>{metric(row.opponent_epa_avg, 0)}</strong>
                                    </span>
                                    <span>
                                      Partners
                                      <strong>{row.partners.join(', ') || 'TBD'}</strong>
                                    </span>
                                    <span>
                                      Opponents
                                      <strong>{row.opponents.join(', ') || 'TBD'}</strong>
                                    </span>
                                  </div>
                                </article>
                              ))}
                            </div>
                          </>
                        ) : (
                          <div className="center-table-wrap">
                            <table className="center-table">
                              <thead>
                                <tr>
                                  <th>Match</th>
                                  <th>Time</th>
                                  <th>Alliance</th>
                                  <th>Partners</th>
                                  <th>Opponents</th>
                                  <th>Difficulty</th>
                                </tr>
                              </thead>
                              <tbody>
                                {visibleScheduleRowsWithDifficulty.map((row) => (
                                  <tr key={`team-schedule-${row.match_key}`}>
                                    <td>
                                      <button
                                        type="button"
                                        className="center-inline-link"
                                        onClick={() => openMatchCenter(row.match_key)}
                                      >
                                        {row.display_name}
                                      </button>
                                    </td>
                                    <td>{fmtDateShort(row.scheduled_time)}</td>
                                    <td>
                                      <span className={`center-alliance-pill ${row.alliance.toLowerCase()}`}>
                                        {row.alliance}
                                        {row.station ? ` (${row.station})` : ''}
                                      </span>
                                    </td>
                                    <td>{row.partners.join(', ') || 'TBD'}</td>
                                    <td>{row.opponents.join(', ') || 'TBD'}</td>
                                    <td>
                                      <span className={`center-difficulty-pill ${row.difficulty_tier}`}>
                                        {metric(row.schedule_difficulty_0_10, 1)} / 10
                                      </span>
                                      <small>
                                        Opp Avg EPA {metric(row.opponent_epa_avg, 0)} ({metric(row.opponent_epa_min, 0)}-
                                        {metric(row.opponent_epa_max, 0)})
                                      </small>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
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
              </SurfaceCardGroup>
            ) : null}

            {activeTab === 'media' ? (
              <SurfaceCardGroup groupId="team-center-media">
                <div className={isMobileLayout ? 'fm-content-stack' : 'center-content-grid'}>
                  <SurfaceCard title="Team Logo" compactable>
                    {teamLogo?.available && teamLogo.image_url ? (
                      <img
                        src={teamLogo.image_url}
                        alt={`Team logo for ${selectedTeamKey}`}
                        className="center-media-image"
                        loading="lazy"
                        decoding="async"
                        fetchPriority="low"
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
                        <img
                          src={teamRobotImage.image_url}
                          alt={`Robot picture for ${selectedTeamKey}`}
                          className="center-media-image"
                          loading="lazy"
                          decoding="async"
                          fetchPriority="low"
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
              </SurfaceCardGroup>
            ) : null}

            {activeTab === 'advanced' ? (
              <SurfaceCardGroup groupId="team-center-advanced">
                <div className={isMobileLayout ? 'fm-content-stack' : 'center-content-grid'}>
                  <SurfaceCard title="Model Details" subtitle="Raw rating payload.">
                    {!teamRating ? <p className="center-callout muted">No rating payload available for this team.</p> : null}
                    {teamRating ? (
                      <pre className="center-code-block">{JSON.stringify(teamRating, null, 2)}</pre>
                    ) : null}
                  </SurfaceCard>

                  <SurfaceCard title="Latest Summary" subtitle="Latest analyzed match payload.">
                    {!teamBreakdown?.recent_matches?.length ? (
                      <p className="center-callout muted">No recent analyzed matches available.</p>
                    ) : (
                      <pre className="center-code-block">
                        {JSON.stringify(teamBreakdown.recent_matches[0]?.summary || {}, null, 2)}
                      </pre>
                    )}
                  </SurfaceCard>

                  <SurfaceCard title="TBA Raw Payloads" subtitle="TBA status and awards payloads.">
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
              </SurfaceCardGroup>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}
