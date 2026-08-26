import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  clientAdminKeyAvailable,
  getApiHealth,
  getEventAlliances,
  getEventAwards,
  getEventRankings,
  getEventSchedule,
  getEventTeamLiveForm,
  getEventTeamsHistory,
  ingestEvent,
} from '../api';
import type {
  EventAllianceItem,
  EventAwardsResponse,
  EventRankingsResponse,
  EventScheduleItem,
  EventSearchItem,
  EventTeamLiveFormEntry,
  EventTeamLiveFormResponse,
  EventTeamsHistoryResponse,
} from '../api';
import { SkeletonBlock } from '../components/ui/SkeletonBlock';
import { EmptyState } from '../components/ui/EmptyState';
import { ActionOverflowMenu } from '../components/ui/ActionOverflowMenu';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { Table } from '../components/ui/primitives';
import { loadSeasonEventCatalog, loadSeasonSearchFallback } from '../features/events/eventCatalog';
import { useLiveRefreshSetting } from '../hooks/useLiveRefreshSetting';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { usePageClock } from '../hooks/usePageClock';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling, type SingleFlightPollReason } from '../hooks/useSingleFlightPolling';
import {
  asRecord,
  buildMatchCenterPath,
  copyTextToClipboard,
  CURRENT_SEASON_YEAR,
  FALLBACK_SEASON_YEAR,
  fmtDateShort,
  liveTimerLabel,
  metric,
  parseNumber,
  relativeFromTimestamp,
  teamNumberFromTeamKey,
  titleizeKey,
} from './centerUtils';
import {
  parseAllianceSlotTeam,
  readRecordFieldLoose,
  resolveBreakdownStage,
  type BreakdownStage,
} from './eventsPage.helpers';
import { readStoredCenterContext, writeCenterContext } from '../layout/centerContext';
import { type QuickJumpRegion } from '../layout/userSettings';
import {
  GridIcon, ListIcon, PieChartIcon, TrophyIcon, UsersIcon,
  LiveDotIcon, ClockIcon, CheckCircleIcon, TagIcon, GlobeIcon, RefreshIcon, CloudSyncIcon,
  CopyIcon, DownloadIcon,
  ScoreboardIcon, BracketIcon, ChevronDownIcon,
  TargetIcon, DeltaIcon,
  CalendarIcon, ChevronLeftIcon, ChevronRightIcon,
} from '../components/ui/Icons';
import { smartSearchEvents } from '../utils/eventSearch';
import {
  matchesRegionFilter,
  normalizeRegionFilter,
  REGION_FILTER_OPTIONS,
  regionLabel,
} from '../utils/regionFilters';
import { cancelIdleWork, scheduleIdleWork } from '../utils/idle';

const EVENT_TABS = ['overview', 'schedule', 'breakdown', 'rankings', 'teams'] as const;
type EventTab = (typeof EVENT_TABS)[number];

const EVENT_TAB_ICONS: Record<EventTab, React.ReactNode> = {
  overview: <GridIcon className="icon-inline" />,
  schedule: <ListIcon className="icon-inline" />,
  breakdown: <PieChartIcon className="icon-inline" />,
  rankings: <TrophyIcon className="icon-inline" />,
  teams: <UsersIcon className="icon-inline" />,
};
type EventRegionFilter = QuickJumpRegion;
type RankingsSortMode = 'rank' | 'team' | 'played';
type EventTeamsSortMode = 'team' | 'analyzed' | 'fuel' | 'climb';
type QualBreakdownSortMode = 'time' | 'status';
type EventsMobilePanel = 'finder' | 'calendar' | 'center';
const EVENTS_SUGGESTED_LIMIT = 160;
const EVENTS_SUGGESTED_REMOTE_TEAM_COUNT_FETCH_LIMIT = 0;
const EVENTS_MIN_SUGGESTED_COUNT = 24;
const EVENTS_CALENDAR_EVENT_LIMIT = 900;
const EVENTS_CALENDAR_DISPLAY_YEAR = new Date().getUTCFullYear();
const ALLIANCE_AUTO_REFRESH_MS = 30000;
const EVENT_TEAMS_INITIAL_VISIBLE_COUNT = 24;
const EVENT_TEAMS_AUTO_CHUNK_SIZE = 24;
const EVENT_TEAMS_AUTO_VISIBLE_TARGET = 72;
const EVENT_TEAMS_HISTORY_FAST_LIMIT = 2;
const EVENT_TEAMS_HISTORY_FULL_LIMIT = 6;

type RankingRow = {
  team_key: string;
  team_number: number;
  nickname: string;
  rank: number | null;
  matches_played: number | null;
  record: string;
  sort_orders: number[];
};

type EventDateRange = {
  startMs: number | null;
  endMs: number | null;
};

type AllianceSlot = {
  key: 'captain' | 'round1' | 'round2' | 'round3' | 'backup';
  label: string;
  aliases: string[];
};

type MatchSummarySnapshot = {
  total: number;
  completed: number;
  live: number;
  upcoming: number;
  avgCombinedScore: number | null;
  avgMargin: number | null;
};

type KnockoutSeriesRow = {
  key: string;
  compLevel: string;
  compLabel: string;
  setNumber: number;
  redTeams: EventScheduleItem['red'];
  blueTeams: EventScheduleItem['blue'];
  matches: EventScheduleItem[];
  redWins: number;
  blueWins: number;
  ties: number;
  completedMatches: number;
  seriesWinner: 'red' | 'blue' | 'tie' | null;
};

type BreakdownAwardTarget = {
  key: string;
  label: string;
  aliases: string[];
};

type ParsedEventAward = {
  name: string;
  winners: string[];
};

function defaultBreakdownStage(hasQualifying: boolean, hasKnockout: boolean): BreakdownStage {
  if (!hasQualifying && hasKnockout) return 'knockout';
  return 'qualifying';
}

function isEventTab(value: string | null): value is EventTab {
  return value === 'overview' || value === 'schedule' || value === 'breakdown' || value === 'rankings' || value === 'teams';
}

function normalizeCompLevel(compLevel: string | null | undefined): string {
  return (compLevel || '').trim().toLowerCase();
}

function inferCompLevelFromMatchKey(matchKey: string | null | undefined): string {
  const normalized = String(matchKey || '').trim().toLowerCase();
  if (!normalized) return '';
  const compact = normalized.includes(':') ? normalized.split(':').pop() || normalized : normalized;
  const levelMatch =
    compact.match(/_(qm|ef|qf|sf|f)(?:\d|_|$)/) ||
    compact.match(/^(?:\d{4}[a-z0-9]+_)?(qm|ef|qf|sf|f)(?:\d|_|$)/);
  if (levelMatch?.[1]) return levelMatch[1];
  if (compact.includes('_playoff') || compact.includes('_elim')) return 'playoff';
  return '';
}

function resolveCompLevel(match: Pick<EventScheduleItem, 'comp_level' | 'match_key'>): string {
  const direct = normalizeCompLevel(match.comp_level);
  if (direct) return direct;
  return inferCompLevelFromMatchKey(match.match_key);
}

function isQualificationCompLevel(compLevel: string | null | undefined): boolean {
  const normalized = normalizeCompLevel(compLevel);
  return (
    normalized === 'qm' ||
    normalized === 'qualification' ||
    normalized === 'qual' ||
    normalized === 'practice' ||
    normalized === 'pr'
  );
}

function isKnockoutCompLevel(compLevel: string | null | undefined): boolean {
  const normalized = normalizeCompLevel(compLevel);
  return (
    normalized === 'ef' ||
    normalized === 'qf' ||
    normalized === 'sf' ||
    normalized === 'f' ||
    normalized === 'quarterfinal' ||
    normalized === 'quarterfinals' ||
    normalized === 'semifinal' ||
    normalized === 'semifinals' ||
    normalized === 'final' ||
    normalized === 'finals' ||
    normalized === 'pf' ||
    normalized === 'elim' ||
    normalized === 'elimination' ||
    normalized === 'playoff' ||
    normalized === 'playoffs'
  );
}

function knockoutCompOrder(compLevel: string): number {
  const normalized = normalizeCompLevel(compLevel);
  if (normalized === 'ef') return 1;
  if (normalized === 'qf' || normalized === 'quarterfinal' || normalized === 'quarterfinals') return 2;
  if (normalized === 'playoff' || normalized === 'playoffs' || normalized === 'pf') return 2;
  if (normalized === 'sf' || normalized === 'semifinal' || normalized === 'semifinals') return 3;
  if (normalized === 'f' || normalized === 'final' || normalized === 'finals') return 4;
  return 9;
}

function knockoutCompLabel(compLevel: string): string {
  const normalized = normalizeCompLevel(compLevel);
  if (normalized === 'ef') return 'Eighth Finals';
  if (normalized === 'qf' || normalized === 'quarterfinal' || normalized === 'quarterfinals') return 'Quarterfinals';
  if (normalized === 'sf' || normalized === 'semifinal' || normalized === 'semifinals') return 'Semifinals';
  if (normalized === 'f' || normalized === 'final' || normalized === 'finals') return 'Finals';
  if (normalized === 'pf' || normalized === 'playoff' || normalized === 'playoffs' || normalized === 'elim' || normalized === 'elimination') return 'Playoffs';
  return titleizeKey(normalized || 'knockout');
}

function matchHasScores(match: EventScheduleItem): boolean {
  return (
    typeof match.red_score === 'number' &&
    typeof match.blue_score === 'number' &&
    Number.isFinite(match.red_score) &&
    Number.isFinite(match.blue_score) &&
    match.red_score >= 0 &&
    match.blue_score >= 0
  );
}

function inferMatchCompleted(match: EventScheduleItem, nowMs: number): boolean {
  const timer = liveTimerLabel(match.scheduled_time, nowMs);
  const hasScores = matchHasScores(match);
  return (
    Boolean(match.is_completed) ||
    match.winner_alliance === 'red' ||
    match.winner_alliance === 'blue' ||
    match.winner_alliance === 'tie' ||
    (hasScores && timer.state === 'ended')
  );
}

function inferWinnerAlliance(match: EventScheduleItem, nowMs: number): 'red' | 'blue' | 'tie' | null {
  if (match.winner_alliance === 'red' || match.winner_alliance === 'blue' || match.winner_alliance === 'tie') {
    return match.winner_alliance;
  }
  if (!inferMatchCompleted(match, nowMs) || !matchHasScores(match)) return null;
  if ((match.red_score || 0) > (match.blue_score || 0)) return 'red';
  if ((match.blue_score || 0) > (match.red_score || 0)) return 'blue';
  return 'tie';
}

function compactAllianceLabel(teams: EventScheduleItem['red']): string {
  if (!Array.isArray(teams) || teams.length === 0) return 'TBD';
  const labels = teams.map((team) => `#${team.team_number}`);
  if (labels.length <= 2) return labels.join(' · ');
  return `${labels.slice(0, 2).join(' · ')} +${labels.length - 2}`;
}

function compactStateLabel(state: ReturnType<typeof liveTimerLabel>['state']): string {
  if (state === 'live') return 'Live';
  if (state === 'upcoming') return 'Upcoming';
  if (state === 'ended') return 'Final';
  return 'Pending';
}

function summarizeMatchSet(matches: EventScheduleItem[], nowMs: number): MatchSummarySnapshot {
  const total = matches.length;
  let completed = 0;
  let live = 0;
  let upcoming = 0;
  const totals: number[] = [];
  const margins: number[] = [];

  for (const match of matches) {
    const timer = liveTimerLabel(match.scheduled_time, nowMs);
    const isCompleted = inferMatchCompleted(match, nowMs);
    if (isCompleted) {
      completed += 1;
    } else if (timer.state === 'live') {
      live += 1;
    } else {
      upcoming += 1;
    }
    if (matchHasScores(match)) {
      const red = Number(match.red_score || 0);
      const blue = Number(match.blue_score || 0);
      totals.push(red + blue);
      margins.push(Math.abs(red - blue));
    }
  }

  return {
    total,
    completed,
    live,
    upcoming,
    avgCombinedScore: totals.length > 0 ? Number((totals.reduce((sum, value) => sum + value, 0) / totals.length).toFixed(1)) : null,
    avgMargin: margins.length > 0 ? Number((margins.reduce((sum, value) => sum + value, 0) / margins.length).toFixed(1)) : null,
  };
}

const BREAKDOWN_AWARD_TARGETS: BreakdownAwardTarget[] = [
  { key: 'autonomous', label: 'Autonomous Award', aliases: ['Autonomous Award'] },
  { key: 'creativity', label: 'Creativity Award', aliases: ['Creativity Award'] },
  { key: 'digital_animation', label: 'Digital Animation Award', aliases: ['Digital Animation Award'] },
  { key: 'engineering_inspiration', label: 'Engineering Inspiration Award', aliases: ['Engineering Inspiration Award'] },
  { key: 'excellence_engineering', label: 'Excellence in Engineering Award', aliases: ['Excellence in Engineering Award'] },
  { key: 'finalist', label: 'Finalist', aliases: ['Finalist'] },
  { key: 'first_leadership', label: 'FIRST Leadership Award', aliases: ['FIRST Leadership Award'] },
  { key: 'first_impact', label: 'FIRST Impact Award', aliases: ['FIRST Impact Award', 'Chairmans Award'] },
  { key: 'founders', label: "Founder's Award", aliases: ["Founder's Award", 'Founders Award'] },
  {
    key: 'gracious_professionalism',
    label: 'Gracious Professionalism Award',
    aliases: ['Gracious Professionalism Award', 'Gracious Professionalism'],
  },
  { key: 'imagery', label: 'Imagery Award', aliases: ['Imagery Award', 'Imagery Award in honor of Jack Kamen'] },
  { key: 'industrial_design', label: 'Industrial Design Award', aliases: ['Industrial Design Award'] },
  { key: 'innovation_control', label: 'Innovation in Control Award', aliases: ['Innovation in Control Award'] },
  { key: 'judges', label: "Judges' Award", aliases: ["Judges' Award", 'Judges Award'] },
  { key: 'quality', label: 'Quality Award', aliases: ['Quality Award'] },
  { key: 'rising_all_star', label: 'Rising All-Star Award', aliases: ['Rising All-Star Award'] },
  { key: 'rookie_all_star', label: 'Rookie All-Star Award', aliases: ['Rookie All-Star Award'] },
  { key: 'safety_animation', label: 'Safety Animation Award', aliases: ['Safety Animation Award'] },
  { key: 'team_spirit', label: 'Team Spirit Award', aliases: ['Team Spirit Award'] },
  { key: 'team_sustainability', label: 'Team Sustainability Award', aliases: ['Team Sustainability Award'] },
  { key: 'volunteer_of_year', label: 'Volunteer of the Year Award', aliases: ['Volunteer of the Year Award'] },
  { key: 'winner', label: 'Winner', aliases: ['Winner'] },
  { key: 'woodie_flowers_finalist', label: 'Woodie Flowers Finalist Award', aliases: ['Woodie Flowers Finalist Award'] },
];

function normalizeAwardLabel(value: string): string {
  return value
    .toLowerCase()
    .replace(/[’'`]/g, '')
    .replace(/®/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function awardMatchesTarget(awardName: string, target: BreakdownAwardTarget): boolean {
  const normalizedName = normalizeAwardLabel(awardName);
  return [target.label, ...target.aliases].some((candidate) => {
    const normalizedCandidate = normalizeAwardLabel(candidate);
    return normalizedName.includes(normalizedCandidate) || normalizedCandidate.includes(normalizedName);
  });
}

function formatAwardRecipient(
  recipientValue: unknown,
  teamLookup: Record<string, { number: number; nickname: string | null }>,
): string | null {
  const recipient = asRecord(recipientValue);
  if (!recipient) return null;
  const teamKey = typeof recipient.team_key === 'string' ? recipient.team_key.trim().toLowerCase() : '';
  const awardee = typeof recipient.awardee === 'string' ? recipient.awardee.trim() : '';
  let teamLabel = '';
  if (teamKey) {
    const lookup = teamLookup[teamKey];
    const fallbackNumber = teamNumberFromTeamKey(teamKey);
    const teamNumber = lookup?.number ?? fallbackNumber;
    teamLabel = teamNumber ? `#${teamNumber}` : teamKey.toUpperCase();
  }
  if (teamLabel && awardee) return `${teamLabel} (${awardee})`;
  if (teamLabel) return teamLabel;
  if (awardee) return awardee;
  return null;
}

function lineupLabel(teams: EventScheduleItem['red']): string {
  return teams.map((team) => `#${team.team_number}`).join(', ') || 'TBD';
}

function buildFallbackTeamsFromSchedule(matches: EventScheduleItem[]): EventTeamsHistoryResponse['teams'] {
  const teamMap = new Map<string, { team_key: string; team_number: number; nickname: string | null }>();
  for (const match of matches) {
    const participants = [...(match.red || []), ...(match.blue || [])];
    for (const team of participants) {
      const rawTeamKey = typeof team.team_key === 'string' ? team.team_key.trim().toLowerCase() : '';
      const explicitNumber =
        typeof team.team_number === 'number' && Number.isFinite(team.team_number) && team.team_number > 0
          ? Math.trunc(team.team_number)
          : null;
      const resolvedTeamKey = rawTeamKey || (explicitNumber ? `frc${explicitNumber}` : '');
      if (!resolvedTeamKey) continue;
      const resolvedTeamNumber = explicitNumber ?? teamNumberFromTeamKey(resolvedTeamKey) ?? 0;
      const nickname =
        typeof team.nickname === 'string' && team.nickname.trim().length > 0
          ? team.nickname.trim()
          : null;
      const existing = teamMap.get(resolvedTeamKey);
      if (!existing) {
        teamMap.set(resolvedTeamKey, {
          team_key: resolvedTeamKey,
          team_number: resolvedTeamNumber,
          nickname,
        });
        continue;
      }
      if (!existing.nickname && nickname) existing.nickname = nickname;
      if (existing.team_number <= 0 && resolvedTeamNumber > 0) {
        existing.team_number = resolvedTeamNumber;
      }
    }
  }
  return Array.from(teamMap.values())
    .sort((a, b) => {
      if (a.team_number !== b.team_number) return a.team_number - b.team_number;
      return a.team_key.localeCompare(b.team_key);
    })
    .map((team) => ({
      team_key: team.team_key,
      team_number: team.team_number,
      nickname: team.nickname,
      region: '',
      state_prov: null,
      country: null,
      history_count: 0,
      averages: null,
      previous_games: [],
    }));
}

function seriesWinnerLineup(series: KnockoutSeriesRow): string {
  if (series.seriesWinner === 'red') return lineupLabel(series.redTeams);
  if (series.seriesWinner === 'blue') return lineupLabel(series.blueTeams);
  if (series.seriesWinner === 'tie') return 'Series tied';
  return 'TBD';
}

function eventLabel(event: EventSearchItem): string {
  const location = [event.city, event.state_prov, event.country]
    .map((value) => (value || '').trim())
    .filter((value) => Boolean(value));
  if (location.length > 0) return location.join(', ');
  return 'Location unavailable';
}

function mergeEventLists(...lists: EventSearchItem[][]): EventSearchItem[] {
  const byKey = new Map<string, EventSearchItem>();
  for (const list of lists) {
    for (const event of list) {
      const key = String(event.event_key || '').trim().toLowerCase();
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

function eventTypeTags(event: EventSearchItem): string[] {
  const name = (event.name || '').toLowerCase();
  const tags: string[] = [];
  if (name.includes('district')) tags.push('District');
  if (name.includes('regional')) tags.push('Regional');
  if (name.includes('league')) tags.push('League');
  if (name.includes('championship') || name.includes('cmp')) tags.push('Championship');
  if (tags.length === 0) tags.push('Event');
  return tags;
}

function parseEventDateValue(value: string | null | undefined): number | null {
  if (!value || typeof value !== 'string') return null;
  const token = value.trim().slice(0, 10);
  if (!token) return null;
  const ms = Date.parse(`${token}T00:00:00Z`);
  return Number.isFinite(ms) ? ms : null;
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
  const cursor = new Date(Date.UTC(new Date(firstMs).getUTCFullYear(), new Date(firstMs).getUTCMonth(), 1));
  const terminal = new Date(Date.UTC(new Date(lastMs).getUTCFullYear(), new Date(lastMs).getUTCMonth(), 1));
  while (cursor <= terminal) {
    tokens.push(`${cursor.getUTCFullYear()}-${String(cursor.getUTCMonth() + 1).padStart(2, '0')}`);
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
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

const ALLIANCE_SLOTS: AllianceSlot[] = [
  {
    key: 'captain',
    label: 'Captain',
    aliases: ['Captain', 'captainTeam', 'captain_team', 'captainTeamNumber', 'captain_team_number'],
  },
  {
    key: 'round1',
    label: 'Pick 1',
    aliases: ['Round1', 'round_1', 'pick1', 'firstPick', 'first_pick', 'pick1Team', 'pick_1_team'],
  },
  {
    key: 'round2',
    label: 'Pick 2',
    aliases: ['Round2', 'round_2', 'pick2', 'secondPick', 'second_pick', 'pick2Team', 'pick_2_team'],
  },
  {
    key: 'round3',
    label: 'Pick 3',
    aliases: ['Round3', 'round_3', 'pick3', 'thirdPick', 'third_pick', 'pick3Team', 'pick_3_team'],
  },
  { key: 'backup', label: 'Backup', aliases: ['Backup', 'alternate', 'Alternate', 'backupTeam', 'backup_team'] },
];

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

export function EventsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobileLayout = useMobileLayout();
  const pageVisible = usePageVisibility();
  const nowMs = usePageClock(pageVisible);
  const liveRefreshSec = useLiveRefreshSetting();

  const defaultEventKey =
    (searchParams.get('event') || readStoredCenterContext().eventKey).trim().toLowerCase();
  const defaultQuery = (searchParams.get('q') || '').trim();
  const defaultRegion = normalizeRegionFilter(searchParams.get('region'));
  const tabParam = searchParams.get('tab');
  const defaultTab: EventTab = isEventTab(tabParam) ? tabParam : 'overview';
  const lastHandledUrlQueryRef = useRef(defaultQuery.toLowerCase());
  const suppressNextLiveSearchRef = useRef(false);
  const searchRequestSeqRef = useRef(0);

  const [eventQuery, setEventQuery] = useState(defaultQuery);
  const [committedQuery, setCommittedQuery] = useState(defaultQuery);
  const [selectedEventKey, setSelectedEventKey] = useState(defaultEventKey);
  const [activeTab, setActiveTab] = useState<EventTab>(defaultTab);
  const [regionFilter, setRegionFilter] = useState<EventRegionFilter>(defaultRegion);

  const [suggestedEvents, setSuggestedEvents] = useState<EventSearchItem[]>([]);
  const [calendarSeasonEvents, setCalendarSeasonEvents] = useState<EventSearchItem[]>([]);
  const [searchResultsRaw, setSearchResultsRaw] = useState<EventSearchItem[]>([]);
  const [eventTeams, setEventTeams] = useState<EventTeamsHistoryResponse | null>(null);
  const [eventSchedule, setEventSchedule] = useState<EventScheduleItem[]>([]);
  const [eventScheduleName, setEventScheduleName] = useState<string | null>(null);
  const [eventRankings, setEventRankings] = useState<EventRankingsResponse | null>(null);
  const [eventAwards, setEventAwards] = useState<EventAwardsResponse | null>(null);
  const [eventLiveForm, setEventLiveForm] = useState<EventTeamLiveFormResponse | null>(null);
  const [eventAlliances, setEventAlliances] = useState<EventAllianceItem[]>([]);
  const [eventAlliancesError, setEventAlliancesError] = useState('');
  const [eventAlliancesLastModified, setEventAlliancesLastModified] = useState<string | null>(null);
  const [loadingEventAlliances, setLoadingEventAlliances] = useState(false);
  const [allianceActionStatus, setAllianceActionStatus] = useState('');

  const [loadingSuggested, setLoadingSuggested] = useState(false);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [loadingEventData, setLoadingEventData] = useState(false);
  const [syncingEvent, setSyncingEvent] = useState(false);

  const [searchError, setSearchError] = useState('');
  const [eventError, setEventError] = useState('');
  const [eventAwardsError, setEventAwardsError] = useState('');
  const [statusText, setStatusText] = useState('Pick an event.');
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [publicReadonlyMode, setPublicReadonlyMode] = useState<boolean | null>(null);
  const [writeAuthEnforced, setWriteAuthEnforced] = useState(false);
  const [adminKeyConfigured, setAdminKeyConfigured] = useState(false);
  const [scheduleVisibleCount, setScheduleVisibleCount] = useState(40);
  const [teamsVisibleCount, setTeamsVisibleCount] = useState(EVENT_TEAMS_INITIAL_VISIBLE_COUNT);
  const [rankingsVisibleCount, setRankingsVisibleCount] = useState(60);
  const [breakdownStage, setBreakdownStage] = useState<BreakdownStage>('qualifying');
  const [breakdownStageUserLocked, setBreakdownStageUserLocked] = useState(false);
  const [qualBreakdownSortMode, setQualBreakdownSortMode] = useState<QualBreakdownSortMode>('time');
  const [rankingsSortMode, setRankingsSortMode] = useState<RankingsSortMode>('rank');
  const [eventTeamsSortMode, setEventTeamsSortMode] = useState<EventTeamsSortMode>('team');
  const [expandedKnockoutSeries, setExpandedKnockoutSeries] = useState<Record<string, boolean>>({});
  const [mobileExpandedScheduleMatches, setMobileExpandedScheduleMatches] = useState<Record<string, boolean>>({});
  const [mobilePanel, setMobilePanel] = useState<EventsMobilePanel>(() => (!defaultEventKey ? 'finder' : 'center'));
  const [calendarMonth, setCalendarMonth] = useState(() => {
    const now = new Date();
    const year = now.getUTCFullYear();
    const month = String(now.getUTCMonth() + 1).padStart(2, '0');
    return `${year}-${month}`;
  });
  const [calendarModalOpen, setCalendarModalOpen] = useState(false);
  const [expandedCalendarDays, setExpandedCalendarDays] = useState<Set<string>>(() => new Set());

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
  const breakdownEventRef = useRef<string | null>(defaultEventKey || null);
  const lastEventContextRef = useRef('');
  const eventTeamsDeepRefreshSeqRef = useRef(0);
  const autoAdjustedCalendarMonthRef = useRef(false);

  useEffect(() => {
    if (!isMobileLayout) setMobilePanel('center');
  }, [isMobileLayout]);

  useEffect(() => {
    setMobileExpandedScheduleMatches({});
  }, [activeTab, isMobileLayout, selectedEventKey]);

  useEffect(() => {
    if (!isMobileLayout) return;
    if (selectedEventKey) return;
    setMobilePanel((current) => (current === 'center' ? 'finder' : current));
  }, [isMobileLayout, selectedEventKey]);

  useEffect(() => {
    if (!allianceActionStatus) return;
    const timer = window.setTimeout(() => setAllianceActionStatus(''), 3200);
    return () => window.clearTimeout(timer);
  }, [allianceActionStatus]);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      try {
        const health = await getApiHealth();
        if (cancelled) return;
        setPublicReadonlyMode(typeof health.public_readonly_mode === 'boolean' ? health.public_readonly_mode : null);
        setWriteAuthEnforced(Boolean(health.write_auth?.enforced));
        setAdminKeyConfigured(Boolean(health.write_auth?.admin_key_configured));
      } catch {
        if (cancelled) return;
        setPublicReadonlyMode(null);
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
          limit: EVENTS_CALENDAR_EVENT_LIMIT,
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
    setScheduleVisibleCount(40);
    setTeamsVisibleCount(EVENT_TEAMS_INITIAL_VISIBLE_COUNT);
    setRankingsVisibleCount(60);
    setRankingsSortMode('rank');
    setEventTeamsSortMode('team');
    setExpandedKnockoutSeries({});
    setQualBreakdownSortMode('time');
  }, [selectedEventKey]);

  useEffect(() => {
    const nextRegion = normalizeRegionFilter(searchParams.get('region'));
    setRegionFilter((prev) => (prev === nextRegion ? prev : nextRegion));
  }, [searchParams]);

  useEffect(() => {
    autoAdjustedCalendarMonthRef.current = false;
  }, [regionFilter]);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setLoadingSuggested(true);
      try {
        const suggested = await loadSeasonEventCatalog({
          preferredYear: CURRENT_SEASON_YEAR,
          fallbackYear: FALLBACK_SEASON_YEAR,
          limit: EVENTS_SUGGESTED_LIMIT,
          minTarget: EVENTS_MIN_SUGGESTED_COUNT,
          preferLiveNow: true,
          remoteTeamCountFetchLimit: EVENTS_SUGGESTED_REMOTE_TEAM_COUNT_FETCH_LIMIT,
        });

        if (suggested.length > 0) {
          setSuggestedEvents(suggested);
          return;
        }

        const fallbackEvents = await loadSeasonSearchFallback({
          preferredYear: CURRENT_SEASON_YEAR,
          fallbackYear: FALLBACK_SEASON_YEAR,
          limit: EVENTS_SUGGESTED_LIMIT,
        });
        if (cancelled) return;
        setSuggestedEvents(fallbackEvents);
        if (fallbackEvents.length > 0) {
          setStatusText('Using seasonal search fallback.');
        }
      } catch (error) {
        if (cancelled) return;
        try {
          const fallbackEvents = await loadSeasonSearchFallback({
            preferredYear: CURRENT_SEASON_YEAR,
            fallbackYear: FALLBACK_SEASON_YEAR,
            limit: EVENTS_SUGGESTED_LIMIT,
          });
          if (cancelled) return;
          setSuggestedEvents(fallbackEvents);
          if (fallbackEvents.length === 0) {
            setStatusText((error as Error).message || 'Unable to load events.');
          } else {
            setStatusText('Using seasonal search fallback.');
          }
        } catch {
          setStatusText((error as Error).message || 'Unable to load events.');
        }
      } finally {
        if (!cancelled) setLoadingSuggested(false);
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selectedEventKey) return;
    lastEventContextRef.current = '';
    setEventSchedule([]);
    setEventScheduleName(null);
    setEventRankings(null);
    setEventAwards(null);
    setEventTeams(null);
    setEventLiveForm(null);
    setEventAlliances([]);
    setEventAlliancesError('');
    setEventAlliancesLastModified(null);
    setAllianceActionStatus('');
    setEventError('');
    setEventAwardsError('');
  }, [selectedEventKey]);

  const eventHasLiveScoreWindow = useMemo(
    () =>
      eventSchedule.some(
        (match) =>
          !inferMatchCompleted(match, nowMs) &&
          liveTimerLabel(match.scheduled_time, nowMs).state === 'live',
      ),
    [eventSchedule, nowMs],
  );

  const effectiveEventPollSec = eventHasLiveScoreWindow ? 5 : Math.max(10, liveRefreshSec);

  const loadEventData = useCallback(async (reason: SingleFlightPollReason): Promise<boolean> => {
    if (!selectedEventKey) return true;
    const contextChanged = lastEventContextRef.current !== selectedEventKey;
    if (contextChanged) {
      setEventSchedule([]);
      setEventScheduleName(null);
      setEventRankings(null);
      setEventAwards(null);
      setEventTeams(null);
      setEventLiveForm(null);
      setEventAlliances([]);
      setEventAlliancesError('');
      setEventAlliancesLastModified(null);
    }
    lastEventContextRef.current = selectedEventKey;

    setLoadingEventData(true);
    setEventError('');
    setEventAwardsError('');
    setStatusText(
      contextChanged || reason === 'initial'
        ? `Loading ${selectedEventKey}...`
        : `Refreshing ${selectedEventKey}...`,
    );

    try {
      const shouldRefreshStatic = contextChanged || reason !== 'poll';
      const shouldBypassLiveCache = reason === 'poll' && eventHasLiveScoreWindow;
      const liveRequestOptions = shouldBypassLiveCache
        ? { bypassCache: true, cacheTtlMs: 0, staleWhileRevalidateMs: 0 }
        : undefined;
      const schedulePromise = getEventSchedule(selectedEventKey, false, undefined, liveRequestOptions);
      const formPromise = getEventTeamLiveForm(
        selectedEventKey,
        { form_window: 5, live_window_sec: 180 },
        liveRequestOptions,
      );
      const rankingsPromise = shouldRefreshStatic ? getEventRankings(selectedEventKey) : Promise.resolve(null);
      const teamsPromise = shouldRefreshStatic
        ? getEventTeamsHistory(selectedEventKey, EVENT_TEAMS_HISTORY_FAST_LIMIT, false)
        : Promise.resolve(null);
      const awardsPromise = shouldRefreshStatic ? getEventAwards(selectedEventKey) : Promise.resolve(null);

      const errors: string[] = [];
      let scheduleCount = 0;
      let teamCount = 0;

      const scheduleResult = await schedulePromise
        .then((value) => ({ status: 'fulfilled' as const, value }))
        .catch((reason) => ({ status: 'rejected' as const, reason }));

      if (scheduleResult.status === 'fulfilled') {
        const matches = scheduleResult.value.matches || [];
        scheduleCount = matches.length;
        setEventSchedule(matches);
        setEventScheduleName(scheduleResult.value.event_name || null);
      } else {
        if (contextChanged) setEventSchedule([]);
        errors.push(`Schedule: ${scheduleResult.reason instanceof Error ? scheduleResult.reason.message : 'failed'}`);
      }

      const [formResult, rankingsResult, teamsResult, awardsResult] = await Promise.allSettled([
        formPromise,
        rankingsPromise,
        teamsPromise,
        awardsPromise,
      ]);

      if (formResult.status === 'fulfilled') {
        setEventLiveForm(formResult.value);
      } else {
        if (contextChanged) setEventLiveForm(null);
        errors.push(`Live form: ${formResult.reason instanceof Error ? formResult.reason.message : 'failed'}`);
      }

      if (shouldRefreshStatic) {
        if (rankingsResult.status === 'fulfilled' && rankingsResult.value) {
          setEventRankings(rankingsResult.value);
        } else if (rankingsResult.status === 'rejected') {
          if (contextChanged) setEventRankings(null);
          errors.push(`Rankings: ${rankingsResult.reason instanceof Error ? rankingsResult.reason.message : 'failed'}`);
        }

        if (teamsResult.status === 'fulfilled' && teamsResult.value) {
          setEventTeams(teamsResult.value);
          teamCount =
            typeof teamsResult.value.teams_count === 'number'
              ? teamsResult.value.teams_count
              : (teamsResult.value.teams || []).length;
          if (EVENT_TEAMS_HISTORY_FULL_LIMIT > EVENT_TEAMS_HISTORY_FAST_LIMIT) {
            const teamsContextKey = selectedEventKey;
            const deepRefreshSeq = ++eventTeamsDeepRefreshSeqRef.current;
            void getEventTeamsHistory(teamsContextKey, EVENT_TEAMS_HISTORY_FULL_LIMIT, false)
              .then((fullPayload) => {
                if (lastEventContextRef.current !== teamsContextKey) return;
                if (eventTeamsDeepRefreshSeqRef.current !== deepRefreshSeq) return;
                setEventTeams(fullPayload);
              })
              .catch((error) => {
                if (lastEventContextRef.current !== teamsContextKey) return;
                if (eventTeamsDeepRefreshSeqRef.current !== deepRefreshSeq) return;
                const detail = error instanceof Error ? error.message : 'failed';
                setEventError((current) => {
                  const message = `Teams deep refresh: ${detail}`;
                  if (!current) return message;
                  if (current.includes(message)) return current;
                  return `${current} | ${message}`;
                });
              });
          }
        } else if (teamsResult.status === 'rejected') {
          if (contextChanged) setEventTeams(null);
          errors.push(`Teams: ${teamsResult.reason instanceof Error ? teamsResult.reason.message : 'failed'}`);
        }

        if (awardsResult.status === 'fulfilled' && awardsResult.value) {
          setEventAwards(awardsResult.value);
          setEventAwardsError('');
        } else if (awardsResult.status === 'rejected') {
          if (contextChanged) setEventAwards(null);
          const awardsMessage = awardsResult.reason instanceof Error ? awardsResult.reason.message : 'failed';
          setEventAwardsError(awardsMessage);
          errors.push(`Awards: ${awardsMessage}`);
        }
      }

      const shouldAutoOpenTeams = scheduleCount === 0 && teamCount > 0;
      if (shouldAutoOpenTeams) {
        setActiveTab((prev) => (prev === 'overview' || prev === 'schedule' || prev === 'breakdown' ? 'teams' : prev));
      }

      setEventError(errors.join(' | '));
      setLastUpdatedAt(Date.now());
      setStatusText(
        errors.length > 0
          ? `Partial data for ${selectedEventKey}.`
          : shouldAutoOpenTeams
            ? `No schedule yet for ${selectedEventKey}. Showing teams.`
            : `${selectedEventKey} loaded.`,
      );
      return errors.length === 0;
    } finally {
      setLoadingEventData(false);
    }
  }, [eventHasLiveScoreWindow, selectedEventKey]);

  const { triggerNow } = useSingleFlightPolling({
    enabled: Boolean(selectedEventKey),
    visible: pageVisible,
    intervalMs: effectiveEventPollSec * 1000,
    run: loadEventData,
    backoffMultiplier: 1.6,
    minBackoffMs: effectiveEventPollSec * 1000,
    maxBackoffMs: 60000,
  });

  const scheduleFallbackTeams = useMemo(
    () => buildFallbackTeamsFromSchedule(eventSchedule),
    [eventSchedule],
  );

  const effectiveEventTeams = useMemo(
    () => (eventTeams?.teams && eventTeams.teams.length > 0 ? eventTeams.teams : scheduleFallbackTeams),
    [eventTeams, scheduleFallbackTeams],
  );

  const effectiveTeamCount = useMemo(() => {
    const apiCount = typeof eventTeams?.teams_count === 'number' ? eventTeams.teams_count : 0;
    return Math.max(apiCount, effectiveEventTeams.length);
  }, [eventTeams, effectiveEventTeams.length]);

  const usingScheduleFallbackTeams = useMemo(
    () => (!eventTeams?.teams || eventTeams.teams.length === 0) && scheduleFallbackTeams.length > 0,
    [eventTeams, scheduleFallbackTeams.length],
  );

  const teamLookupByKey = useMemo(() => {
    const lookup: Record<string, { number: number; nickname: string | null }> = {};
    for (const team of effectiveEventTeams) {
      lookup[team.team_key.toLowerCase()] = {
        number: team.team_number,
        nickname: team.nickname,
      };
    }
    return lookup;
  }, [effectiveEventTeams]);

  const liveStatusByTeam = useMemo(() => eventLiveForm?.team_statuses || {}, [eventLiveForm]);

  const rankingRows = useMemo(() => {
    const payload = asRecord(eventRankings?.rankings);
    const rows = Array.isArray(payload?.rankings) ? payload.rankings : [];

    return rows
      .map((item) => {
        const row = asRecord(item);
        if (!row) return null;
        const teamKey = typeof row.team_key === 'string' ? row.team_key.toLowerCase() : '';
        if (!teamKey) return null;
        const record = asRecord(row.record);
        const sortOrdersRaw = Array.isArray(row.sort_orders) ? row.sort_orders : [];
        const sortOrders = sortOrdersRaw
          .map((value) => parseNumber(value))
          .filter((value): value is number => value !== null);
        const teamInfo = teamLookupByKey[teamKey];

        return {
          team_key: teamKey,
          team_number: teamInfo?.number ?? teamNumberFromTeamKey(teamKey) ?? 0,
          nickname: teamInfo?.nickname || teamKey,
          rank: parseNumber(row.rank),
          matches_played: parseNumber(row.matches_played),
          record:
            record && (record.wins !== undefined || record.losses !== undefined || record.ties !== undefined)
              ? `${parseNumber(record.wins) ?? 0}-${parseNumber(record.losses) ?? 0}-${parseNumber(record.ties) ?? 0}`
              : 'N/A',
          sort_orders: sortOrders,
        } satisfies RankingRow;
      })
      .filter((row): row is RankingRow => Boolean(row))
      .sort((a, b) => {
        const rankA = a.rank ?? Number.POSITIVE_INFINITY;
        const rankB = b.rank ?? Number.POSITIVE_INFINITY;
        if (rankA !== rankB) return rankA - rankB;
        return a.team_number - b.team_number;
      });
  }, [eventRankings, teamLookupByKey]);

  const rankingSortLabels = useMemo(() => {
    const payload = asRecord(eventRankings?.rankings);
    const info = Array.isArray(payload?.sort_order_info) ? payload.sort_order_info : [];
    return info
      .map((item) => {
        const row = asRecord(item);
        if (!row) return null;
        const name = typeof row.name === 'string' ? row.name : typeof row.precision === 'string' ? row.precision : null;
        return name || null;
      })
      .filter((value): value is string => Boolean(value));
  }, [eventRankings]);

  const topAnalyzedTeams = useMemo(() => {
    return [...effectiveEventTeams]
      .sort((a, b) => b.history_count - a.history_count)
      .slice(0, 5);
  }, [effectiveEventTeams]);

  const topFuelTeams = useMemo(() => {
    return [...effectiveEventTeams]
      .filter((team) => typeof team.averages?.fuel_scoring_rate === 'number')
      .sort((a, b) => (b.averages?.fuel_scoring_rate || 0) - (a.averages?.fuel_scoring_rate || 0))
      .slice(0, 5);
  }, [effectiveEventTeams]);

  const liveMatchCount = useMemo(() => {
    return eventSchedule.filter((match) => liveTimerLabel(match.scheduled_time, nowMs).state === 'live').length;
  }, [eventSchedule, nowMs]);

  const hasClientAdminKey = clientAdminKeyAvailable();
  const canSyncEvent = useMemo(() => {
    if (publicReadonlyMode === true) return false;
    if (writeAuthEnforced && !hasClientAdminKey) return false;
    if (writeAuthEnforced && adminKeyConfigured === false) return false;
    return true;
  }, [adminKeyConfigured, hasClientAdminKey, publicReadonlyMode, writeAuthEnforced]);

  const visibleScheduleRows = useMemo(
    () => eventSchedule.slice(0, Math.max(1, scheduleVisibleCount)),
    [eventSchedule, scheduleVisibleCount],
  );

  const qualificationMatches = useMemo(
    () =>
      eventSchedule
        .filter((match) => isQualificationCompLevel(resolveCompLevel(match)))
        .sort((a, b) => {
          const aTime = typeof a.scheduled_time === 'number' ? a.scheduled_time : Number.MAX_SAFE_INTEGER;
          const bTime = typeof b.scheduled_time === 'number' ? b.scheduled_time : Number.MAX_SAFE_INTEGER;
          if (aTime !== bTime) return aTime - bTime;
          return a.match_key.localeCompare(b.match_key);
        }),
    [eventSchedule],
  );

  const knockoutMatches = useMemo(
    () =>
      eventSchedule
        .filter((match) => isKnockoutCompLevel(resolveCompLevel(match)))
        .sort((a, b) => {
          const levelDelta = knockoutCompOrder(resolveCompLevel(a)) - knockoutCompOrder(resolveCompLevel(b));
          if (levelDelta !== 0) return levelDelta;
          if (a.set_number !== b.set_number) return a.set_number - b.set_number;
          if (a.match_number !== b.match_number) return a.match_number - b.match_number;
          return a.match_key.localeCompare(b.match_key);
        }),
    [eventSchedule],
  );

  const sortedQualificationMatches = useMemo(() => {
    const rows = [...qualificationMatches];
    if (qualBreakdownSortMode === 'status') {
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
        const scoreDelta = score(aCompleted, aTimer.state) - score(bCompleted, bTimer.state);
        if (scoreDelta !== 0) return scoreDelta;
        const aTime = typeof a.scheduled_time === 'number' ? a.scheduled_time : Number.MAX_SAFE_INTEGER;
        const bTime = typeof b.scheduled_time === 'number' ? b.scheduled_time : Number.MAX_SAFE_INTEGER;
        if (aTime !== bTime) return aTime - bTime;
        return a.match_key.localeCompare(b.match_key);
      });
      return rows;
    }
    rows.sort((a, b) => {
      const aTime = typeof a.scheduled_time === 'number' ? a.scheduled_time : Number.MAX_SAFE_INTEGER;
      const bTime = typeof b.scheduled_time === 'number' ? b.scheduled_time : Number.MAX_SAFE_INTEGER;
      if (aTime !== bTime) return aTime - bTime;
      return a.match_key.localeCompare(b.match_key);
    });
    return rows;
  }, [nowMs, qualBreakdownSortMode, qualificationMatches]);

  useEffect(() => {
    const previousEventKey = breakdownEventRef.current;
    const eventChanged = previousEventKey !== selectedEventKey;
    breakdownEventRef.current = selectedEventKey || null;

    if (!selectedEventKey) {
      setBreakdownStageUserLocked(false);
      setBreakdownStage('qualifying');
      return;
    }

    const hasQualifying = qualificationMatches.length > 0;
    const hasKnockout = knockoutMatches.length > 0;

    if (eventChanged) {
      setBreakdownStageUserLocked(false);
      setBreakdownStage(defaultBreakdownStage(hasQualifying, hasKnockout));
      return;
    }

    setBreakdownStage((current) =>
      resolveBreakdownStage(current, hasQualifying, hasKnockout, breakdownStageUserLocked),
    );
  }, [breakdownStageUserLocked, selectedEventKey, qualificationMatches.length, knockoutMatches.length]);

  const qualificationSummary = useMemo(
    () => summarizeMatchSet(qualificationMatches, nowMs),
    [nowMs, qualificationMatches],
  );

  const knockoutSummary = useMemo(
    () => summarizeMatchSet(knockoutMatches, nowMs),
    [knockoutMatches, nowMs],
  );

  const knockoutSeriesByRound = useMemo(() => {
    const grouped: Record<string, KnockoutSeriesRow[]> = {};
    const bySeries = new Map<string, EventScheduleItem[]>();
    for (const match of knockoutMatches) {
      const comp = resolveCompLevel(match);
      const setNumber = Number(match.set_number || 0);
      const key = `${comp}:${setNumber}`;
      const existing = bySeries.get(key) || [];
      existing.push(match);
      bySeries.set(key, existing);
    }

    for (const [seriesKey, matches] of bySeries.entries()) {
      const sortedMatches = [...matches].sort((a, b) => a.match_number - b.match_number);
      const sample = sortedMatches[0];
      const compLevel = sample ? resolveCompLevel(sample) : '';
      const compLabel = knockoutCompLabel(compLevel);
      const setNumber = Number(sample?.set_number || 0);
      let redWins = 0;
      let blueWins = 0;
      let ties = 0;
      let completedMatches = 0;
      for (const match of sortedMatches) {
        const winner = inferWinnerAlliance(match, nowMs);
        if (inferMatchCompleted(match, nowMs)) completedMatches += 1;
        if (winner === 'red') redWins += 1;
        else if (winner === 'blue') blueWins += 1;
        else if (winner === 'tie') ties += 1;
      }
      const seriesWinner = redWins > blueWins ? 'red' : blueWins > redWins ? 'blue' : ties > 0 && redWins === blueWins ? 'tie' : null;

      const seriesRow: KnockoutSeriesRow = {
        key: seriesKey,
        compLevel,
        compLabel,
        setNumber,
        redTeams: sample?.red || [],
        blueTeams: sample?.blue || [],
        matches: sortedMatches,
        redWins,
        blueWins,
        ties,
        completedMatches,
        seriesWinner,
      };
      if (!grouped[compLabel]) grouped[compLabel] = [];
      grouped[compLabel].push(seriesRow);
    }

    for (const roundLabel of Object.keys(grouped)) {
      grouped[roundLabel].sort((a, b) => {
        const levelDelta = knockoutCompOrder(a.compLevel) - knockoutCompOrder(b.compLevel);
        if (levelDelta !== 0) return levelDelta;
        return a.setNumber - b.setNumber;
      });
    }

    return Object.entries(grouped).sort((a, b) => {
      const levelA = knockoutCompOrder(a[1][0]?.compLevel || '');
      const levelB = knockoutCompOrder(b[1][0]?.compLevel || '');
      return levelA - levelB;
    });
  }, [knockoutMatches, nowMs]);

  const knockoutChampionLabel = useMemo(() => {
    if (knockoutSeriesByRound.length === 0) return 'TBD';
    const finalsRound = knockoutSeriesByRound.find(([, series]) => {
      const normalized = normalizeCompLevel(series[0]?.compLevel || '');
      return normalized === 'f' || normalized === 'final' || normalized === 'finals';
    });
    const candidateSeries = finalsRound?.[1] || knockoutSeriesByRound[knockoutSeriesByRound.length - 1]?.[1] || [];
    const championSeries = candidateSeries.find(
      (series) => series.seriesWinner === 'red' || series.seriesWinner === 'blue',
    );
    if (!championSeries) return 'TBD';
    return championSeries.seriesWinner === 'red'
      ? lineupLabel(championSeries.redTeams)
      : lineupLabel(championSeries.blueTeams);
  }, [knockoutSeriesByRound]);

  const allKnockoutSeriesKeys = useMemo(
    () => knockoutSeriesByRound.flatMap(([, series]) => series.map((row) => row.key)),
    [knockoutSeriesByRound],
  );

  const allianceSelectionsReady = Boolean(selectedEventKey);

  const refreshAllianceSelections = useCallback(async (
    options: { quiet?: boolean } = {},
  ): Promise<boolean> => {
    if (!selectedEventKey) {
      setEventAlliances([]);
      setEventAlliancesError('');
      setEventAlliancesLastModified(null);
      setLoadingEventAlliances(false);
      return true;
    }
    const quiet = Boolean(options.quiet);
    if (!quiet) setLoadingEventAlliances(true);
    try {
      const payload = await getEventAlliances(selectedEventKey);
      setEventAlliances(Array.isArray(payload.alliances) ? payload.alliances : []);
      setEventAlliancesLastModified(payload.last_modified || null);
      setEventAlliancesError('');
      return true;
    } catch (error) {
      const message = (error as Error).message || 'Unable to load alliance selections.';
      const likelyNotConfigured = message.toLowerCase().includes('not configured');
      setEventAlliances([]);
      setEventAlliancesLastModified(null);
      setEventAlliancesError(likelyNotConfigured ? '' : message);
      return false;
    } finally {
      if (!quiet) setLoadingEventAlliances(false);
    }
  }, [selectedEventKey]);

  useEffect(() => {
    void refreshAllianceSelections();
  }, [refreshAllianceSelections, lastUpdatedAt]);

  useEffect(() => {
    if (!selectedEventKey || !pageVisible) return;
    const timer = window.setInterval(() => {
      void refreshAllianceSelections({ quiet: true });
    }, ALLIANCE_AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [pageVisible, refreshAllianceSelections, selectedEventKey]);

  const allianceSelectionRows = useMemo(() => {
    return eventAlliances
      .map((value, index) => {
        const alliance = asRecord(value);
        if (!alliance) return null;
        const rawAllianceNumber = readRecordFieldLoose(alliance, ['number', 'allianceNumber', 'alliance_number']);
        const rawAllianceName = readRecordFieldLoose(alliance, ['name', 'allianceName', 'alliance_name']);
        const number = parseNumber(rawAllianceNumber) ?? index + 1;
        const name = typeof rawAllianceName === 'string' && rawAllianceName.trim().length > 0
          ? rawAllianceName.trim()
          : `Alliance ${number}`;
        const teams = ALLIANCE_SLOTS
          .map((slot) => {
            const slotPayload = readRecordFieldLoose(alliance, [slot.key, ...slot.aliases]);
            const team = parseAllianceSlotTeam(slotPayload);
            if (!team) return null;
            return {
              slot: slot.label,
              teamNumber: team.teamNumber,
              name: team.name,
            };
          })
          .filter((row): row is { slot: string; teamNumber: number; name: string } => Boolean(row));

        // FIRST/TBA variants sometimes publish alliance members as a picks array.
        if (teams.length === 0) {
          const picksPayload = readRecordFieldLoose(alliance, ['picks', 'Picks', 'teamPicks', 'team_picks', 'teams']);
          const picks = Array.isArray(picksPayload) ? picksPayload : [];
          const fallbackSlots = ['Captain', 'Pick 1', 'Pick 2', 'Pick 3'];
          for (let idx = 0; idx < picks.length && idx < fallbackSlots.length; idx += 1) {
            const team = parseAllianceSlotTeam(picks[idx]);
            if (!team) continue;
            teams.push({
              slot: fallbackSlots[idx],
              teamNumber: team.teamNumber,
              name: team.name,
            });
          }
          const backupPayload = readRecordFieldLoose(alliance, ['backup', 'Backup', 'alternate', 'Alternate']);
          const backupTeam = parseAllianceSlotTeam(backupPayload);
          if (backupTeam && !teams.some((team) => team.teamNumber === backupTeam.teamNumber)) {
            teams.push({
              slot: 'Backup',
              teamNumber: backupTeam.teamNumber,
              name: backupTeam.name,
            });
          }
        }

        return {
          key: `${name}-${number}-${index}`,
          number,
          name,
          teams,
        };
      })
      .filter((row): row is { key: string; number: number; name: string; teams: Array<{ slot: string; teamNumber: number; name: string }> } => Boolean(row))
      .sort((a, b) => a.number - b.number);
  }, [eventAlliances]);

  const allianceSelectionExportText = useMemo(() => {
    if (allianceSelectionRows.length === 0) return '';
    return allianceSelectionRows
      .map((alliance) => {
        const teamLine = alliance.teams.length > 0
          ? alliance.teams.map((team) => `${team.slot}: #${team.teamNumber}${team.name ? ` (${team.name})` : ''}`).join(' | ')
          : 'No teams published';
        return `${alliance.name} (Alliance ${alliance.number})\n${teamLine}`;
      })
      .join('\n\n');
  }, [allianceSelectionRows]);

  async function copyAllianceSelections() {
    if (!allianceSelectionExportText) return;
    const copied = await copyTextToClipboard(allianceSelectionExportText);
    setAllianceActionStatus(copied ? 'Alliance selections copied.' : 'Copy failed.');
  }

  function exportAllianceSelectionsCsv() {
    if (allianceSelectionRows.length === 0) return;
    const header = 'alliance_number,alliance_name,slot,team_number,team_name';
    const lines = allianceSelectionRows.flatMap((alliance) => (
      alliance.teams.length > 0
        ? alliance.teams.map((team) =>
            [
              alliance.number,
              JSON.stringify(alliance.name),
              JSON.stringify(team.slot),
              team.teamNumber,
              JSON.stringify(team.name || ''),
            ].join(','),
          )
        : [[
            alliance.number,
            JSON.stringify(alliance.name),
            JSON.stringify('N/A'),
            '',
            JSON.stringify(''),
          ].join(',')]
    ));
    const blob = new Blob([[header, ...lines].join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${selectedEventKey || 'event'}-alliances.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
    setAllianceActionStatus('Alliance CSV exported.');
  }

  async function refreshAllianceSelectionsManually() {
    const success = await refreshAllianceSelections();
    setAllianceActionStatus(success ? 'Alliance selections refreshed.' : 'Alliance refresh failed.');
  }

  const eventAlliancesUpdatedLabel = useMemo(() => {
    if (!eventAlliancesLastModified) return 'unknown';
    const parsedMs = Date.parse(eventAlliancesLastModified);
    if (!Number.isFinite(parsedMs)) return eventAlliancesLastModified;
    return relativeFromTimestamp(parsedMs);
  }, [eventAlliancesLastModified]);

  const parsedEventAwards = useMemo<ParsedEventAward[]>(() => {
    const rows = Array.isArray(eventAwards?.awards) ? eventAwards.awards : [];
    return rows
      .map((entry) => {
        const award = asRecord(entry);
        if (!award) return null;
        const name = typeof award.name === 'string' ? award.name.trim() : '';
        if (!name) return null;
        const recipients = Array.isArray(award.recipient_list) ? award.recipient_list : [];
        const winners = Array.from(
          new Set(
            recipients
              .map((recipient) => formatAwardRecipient(recipient, teamLookupByKey))
              .filter((value): value is string => Boolean(value)),
          ),
        );
        return { name, winners };
      })
      .filter((value): value is ParsedEventAward => Boolean(value));
  }, [eventAwards, teamLookupByKey]);

  const breakdownAwardRows = useMemo(() => {
    return BREAKDOWN_AWARD_TARGETS.map((target) => {
      const matches = parsedEventAwards.filter((award) => awardMatchesTarget(award.name, target));
      const winners = Array.from(new Set(matches.flatMap((award) => award.winners)));
      return {
        key: target.key,
        label: target.label,
        winners,
        matchedNames: matches.map((award) => award.name),
      };
    });
  }, [parsedEventAwards]);

  const eventTeamRows = useMemo(() => {
    const rows = [...effectiveEventTeams];
    if (eventTeamsSortMode === 'analyzed') {
      rows.sort((a, b) => {
        if (b.history_count !== a.history_count) return b.history_count - a.history_count;
        return a.team_number - b.team_number;
      });
      return rows;
    }
    if (eventTeamsSortMode === 'fuel') {
      rows.sort((a, b) => {
        const fuelA = typeof a.averages?.fuel_scoring_rate === 'number' ? a.averages.fuel_scoring_rate : Number.NEGATIVE_INFINITY;
        const fuelB = typeof b.averages?.fuel_scoring_rate === 'number' ? b.averages.fuel_scoring_rate : Number.NEGATIVE_INFINITY;
        if (fuelB !== fuelA) return fuelB - fuelA;
        return a.team_number - b.team_number;
      });
      return rows;
    }
    if (eventTeamsSortMode === 'climb') {
      rows.sort((a, b) => {
        const climbA = typeof a.averages?.climb_success_prob === 'number' ? a.averages.climb_success_prob : Number.NEGATIVE_INFINITY;
        const climbB = typeof b.averages?.climb_success_prob === 'number' ? b.averages.climb_success_prob : Number.NEGATIVE_INFINITY;
        if (climbB !== climbA) return climbB - climbA;
        return a.team_number - b.team_number;
      });
      return rows;
    }
    rows.sort((a, b) => a.team_number - b.team_number);
    return rows;
  }, [effectiveEventTeams, eventTeamsSortMode]);

  useEffect(() => {
    const maxAutoVisible = Math.min(EVENT_TEAMS_AUTO_VISIBLE_TARGET, eventTeamRows.length);
    if (maxAutoVisible <= teamsVisibleCount) return;
    const handle = scheduleIdleWork(() => {
      setTeamsVisibleCount((current) => {
        if (current >= maxAutoVisible) return current;
        return Math.min(maxAutoVisible, current + EVENT_TEAMS_AUTO_CHUNK_SIZE);
      });
    }, { fallbackDelayMs: 45, timeoutMs: 180 });
    return () => cancelIdleWork(handle);
  }, [eventTeamRows.length, teamsVisibleCount]);

  const visibleEventTeamRows = useMemo(
    () => eventTeamRows.slice(0, Math.max(1, teamsVisibleCount)),
    [eventTeamRows, teamsVisibleCount],
  );

  const sortedRankingRows = useMemo(() => {
    const rows = [...rankingRows];
    if (rankingsSortMode === 'team') {
      rows.sort((a, b) => a.team_number - b.team_number);
      return rows;
    }
    if (rankingsSortMode === 'played') {
      rows.sort((a, b) => {
        const playedA = a.matches_played ?? Number.NEGATIVE_INFINITY;
        const playedB = b.matches_played ?? Number.NEGATIVE_INFINITY;
        if (playedB !== playedA) return playedB - playedA;
        return (a.rank ?? Number.POSITIVE_INFINITY) - (b.rank ?? Number.POSITIVE_INFINITY);
      });
      return rows;
    }
    rows.sort((a, b) => (a.rank ?? Number.POSITIVE_INFINITY) - (b.rank ?? Number.POSITIVE_INFINITY));
    return rows;
  }, [rankingRows, rankingsSortMode]);

  const visibleRankingRows = useMemo(
    () => sortedRankingRows.slice(0, Math.max(1, rankingsVisibleCount)),
    [rankingsVisibleCount, sortedRankingRows],
  );

  const visibleSearchResults = useMemo(
    () =>
      searchResultsRaw.filter((event) =>
        matchesRegionFilter(regionFilter, event.state_prov || null, event.country || null),
      ),
    [regionFilter, searchResultsRaw],
  );

  const visibleSuggestedEvents = useMemo(
    () =>
      suggestedEvents.filter((event) =>
        matchesRegionFilter(regionFilter, event.state_prov || null, event.country || null),
      ),
    [regionFilter, suggestedEvents],
  );

  const calendarSourceEvents = useMemo(
    () =>
      mergeEventLists(calendarSeasonEvents, suggestedEvents).filter((event) => {
        if (!matchesRegionFilter(regionFilter, event.state_prov || null, event.country || null)) return false;
        return matchesCalendarDisplayYear(event, EVENTS_CALENDAR_DISPLAY_YEAR);
      }),
    [calendarSeasonEvents, regionFilter, suggestedEvents],
  );

  const calendarEventRows = useMemo(() => {
    return calendarSourceEvents
      .map((event) => {
        const resolved = resolveEventDateRange(event);
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
  }, [calendarSourceEvents]);

  const calendarAvailableMonths = useMemo(() => {
    const months = new Set<string>();
    for (const row of calendarEventRows) {
      for (const token of monthTokensForRange(row.startMs, row.endMs)) {
        months.add(token);
      }
    }
    return Array.from(months).sort();
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
          const resolved = resolveEventDateRange(event);
          return !resolved.startMs && !resolved.endMs;
        })
        .sort((a, b) => a.event_key.localeCompare(b.event_key)),
    [calendarSourceEvents],
  );

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

  const calendarMonthLabel = useMemo(() => {
    const [yearStr, monthStr] = calendarMonth.split('-');
    const year = parseInt(yearStr, 10);
    const month = parseInt(monthStr, 10) - 1;
    if (!Number.isFinite(year) || !Number.isFinite(month)) return calendarMonth;
    const d = new Date(Date.UTC(year, month, 1));
    return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' });
  }, [calendarMonth]);

  const openEvent = useCallback((eventKey: string) => {
    const normalized = eventKey.trim().toLowerCase();
    if (!normalized) return;
    setSelectedEventKey(normalized);
    setActiveTab('overview');
    if (isMobileLayout) setMobilePanel('center');
    // Force re-fetch even if same key is selected again
    setTimeout(() => triggerNow('manual'), 0);
  }, [isMobileLayout, triggerNow]);

  const runEventSearch = useCallback(
    async (queryInput: string, options?: { autoOpen?: boolean; includeStatus?: boolean }) => {
      const query = queryInput.trim();
      const shouldAutoOpen = options?.autoOpen ?? true;
      const includeStatus = options?.includeStatus ?? true;
      const liveSearchMode = !shouldAutoOpen && !includeStatus;
      const requestSeq = ++searchRequestSeqRef.current;

      if (!query) {
        setSearchResultsRaw([]);
        setSearchError('');
        return;
      }

      setLoadingSearch(true);
      setSearchError('');
      try {
        const localResolved = await smartSearchEvents(query, {
          preferredYear: CURRENT_SEASON_YEAR,
          fallbackYear: FALLBACK_SEASON_YEAR,
          maxResults: liveSearchMode ? 90 : 220,
          seedEvents: suggestedEvents,
          fastMode: true,
          localOnly: true,
          maxNetworkVariants: 0,
          includeSuggestedFallback: false,
        });
        if (requestSeq !== searchRequestSeqRef.current) return;
        setSearchResultsRaw(localResolved.events);

        const scopedLocalResults = localResolved.events.filter((row) =>
          matchesRegionFilter(regionFilter, row.state_prov || null, row.country || null),
        );
        const localBestMatch = scopedLocalResults[0] || null;

        if (localBestMatch?.event_key && shouldAutoOpen) {
          openEvent(localBestMatch.event_key);
        }

        if (liveSearchMode) return;

        const resolved = await smartSearchEvents(query, {
          preferredYear: CURRENT_SEASON_YEAR,
          fallbackYear: FALLBACK_SEASON_YEAR,
          maxResults: 220,
          seedEvents: suggestedEvents,
          fastMode: false,
          localOnly: false,
          maxNetworkVariants: 7,
          includeSuggestedFallback: true,
        });
        if (requestSeq !== searchRequestSeqRef.current) return;
        setSearchResultsRaw(resolved.events);

        const scopedResults = resolved.events.filter((row) =>
          matchesRegionFilter(regionFilter, row.state_prov || null, row.country || null),
        );
        const bestMatch = scopedResults[0] || null;
        const localBestKey = localBestMatch?.event_key?.toLowerCase() || '';

        if (bestMatch?.event_key && shouldAutoOpen && bestMatch.event_key.toLowerCase() !== localBestKey) {
          openEvent(bestMatch.event_key);
        }

        if (includeStatus) {
          if (scopedResults.length > 0 && bestMatch?.event_key) {
            const normalizedQuery = query.toLowerCase();
            const correctedHint =
              resolved.correctedQuery && resolved.correctedQuery !== normalizedQuery
                ? ` (interpreted as "${resolved.correctedQuery}")`
                : '';
            setStatusText(
              `Matched "${query}"${correctedHint} to ${bestMatch.event_key.toLowerCase()} in ${regionLabel(
                regionFilter,
              )} (${scopedResults.length} result${scopedResults.length === 1 ? '' : 's'}).`,
            );
          } else if (resolved.events.length > 0 && regionFilter !== 'all') {
            setStatusText(
              `Found matches for "${query}", but none in ${regionLabel(regionFilter)}. Switch region to All Regions to view them.`,
            );
          } else {
            setStatusText(`No event match found for "${query}".`);
          }
        }
      } catch (error) {
        if (requestSeq !== searchRequestSeqRef.current) return;
        setSearchResultsRaw([]);
        setSearchError((error as Error).message || 'Event search failed.');
      } finally {
        if (requestSeq === searchRequestSeqRef.current) {
          setLoadingSearch(false);
        }
      }
    },
    [openEvent, regionFilter, suggestedEvents],
  );

  useEffect(() => {
    const queryFromUrl = (searchParams.get('q') || '').trim();
    const normalizedUrlQuery = queryFromUrl.toLowerCase();
    if (normalizedUrlQuery === lastHandledUrlQueryRef.current) return;
    lastHandledUrlQueryRef.current = normalizedUrlQuery;

    if (!queryFromUrl) {
      setCommittedQuery('');
      return;
    }

    suppressNextLiveSearchRef.current = true;
    setEventQuery(queryFromUrl);
    setCommittedQuery(queryFromUrl);
    void runEventSearch(queryFromUrl, { autoOpen: true, includeStatus: true });
  }, [location.search, runEventSearch, searchParams]);

  useEffect(() => {
    const query = eventQuery.trim();
    if (!query) {
      setSearchResultsRaw([]);
      setSearchError('');
      if (committedQuery) {
        lastHandledUrlQueryRef.current = '';
        setCommittedQuery('');
      }
      return;
    }
    if (query.length < 2) {
      setSearchResultsRaw([]);
      setSearchError('');
      return;
    }
    if (suppressNextLiveSearchRef.current) {
      suppressNextLiveSearchRef.current = false;
      return;
    }

    const timer = window.setTimeout(() => {
      void runEventSearch(query, { autoOpen: false, includeStatus: false });
    }, 220);
    return () => window.clearTimeout(timer);
  }, [committedQuery, eventQuery, runEventSearch]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (selectedEventKey) next.set('event', selectedEventKey);
    if (committedQuery.trim()) next.set('q', committedQuery.trim());
    if (regionFilter !== 'all') next.set('region', regionFilter);
    next.set('tab', activeTab);

    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }

    if (selectedEventKey) {
      writeCenterContext({ eventKey: selectedEventKey, sourcePath: '/events' });
    }
  }, [activeTab, committedQuery, regionFilter, searchParams, selectedEventKey, setSearchParams]);

  async function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = eventQuery.trim();
    if (!query) {
      setSearchResultsRaw([]);
      setSearchError('');
      return;
    }
    await runEventSearch(query, { autoOpen: true, includeStatus: true });
    lastHandledUrlQueryRef.current = query.toLowerCase();
    setCommittedQuery(query);
  }

  async function handleSyncEvent() {
    if (!selectedEventKey) return;
    if (!canSyncEvent) {
      setEventError('Sync is disabled in public read-only mode.');
      return;
    }
    setSyncingEvent(true);
    setEventError('');
    try {
      const payload = await ingestEvent(selectedEventKey);
      setStatusText(
        `Synced ${payload.event_key}: ${payload.teams} teams, ${payload.matches} matches, ${payload.match_team_links} links.`,
      );
      setSelectedEventKey(payload.event_key.toLowerCase());
    } catch (error) {
      setEventError((error as Error).message || 'Event sync failed.');
    } finally {
      setSyncingEvent(false);
    }
  }

  function openTeamCenter(teamKey: string) {
    if (!selectedEventKey) return;
    navigate(`/team-center?event=${selectedEventKey}&team=${teamKey.toLowerCase()}`);
  }

  function openMatchCenter(matchKey: string) {
    if (!selectedEventKey) return;
    const normalizedEventKey = selectedEventKey.trim().toLowerCase();
    navigate(buildMatchCenterPath(normalizedEventKey, matchKey));
  }

  function scheduleMobileRowKey(matchKey: string): string {
    return `${selectedEventKey || 'event'}::${matchKey}`;
  }

  function isMobileScheduleMatchExpanded(matchKey: string): boolean {
    return Boolean(mobileExpandedScheduleMatches[scheduleMobileRowKey(matchKey)]);
  }

  function expandMobileScheduleMatch(matchKey: string) {
    const key = scheduleMobileRowKey(matchKey);
    setMobileExpandedScheduleMatches((previous) => ({ ...previous, [key]: true }));
  }

  function collapseMobileScheduleMatch(matchKey: string) {
    const key = scheduleMobileRowKey(matchKey);
    setMobileExpandedScheduleMatches((previous) => {
      if (!previous[key]) return previous;
      const next = { ...previous };
      delete next[key];
      return next;
    });
  }

  function renderKnockoutSeriesCard(series: KnockoutSeriesRow, keyPrefix: string) {
    const isExpanded = Boolean(expandedKnockoutSeries[series.key]);
    const winnerSummary = seriesWinnerLineup(series);
    const completedAllMatches = series.completedMatches >= series.matches.length;
    return (
      <article
        key={`${keyPrefix}-${series.key}`}
        className={`event-knockout-series-card ${isExpanded ? 'expanded' : 'collapsed'}`.trim()}
      >
        <header className="event-knockout-series-head">
          <div className="event-knockout-series-head-main">
            <strong>Set {series.setNumber || 'TBD'}</strong>
            <span className="center-chip">
              {series.redWins}-{series.blueWins}
              {series.ties > 0 ? ` (${series.ties} tie)` : ''}
            </span>
          </div>
          <div className="event-knockout-series-head-actions">
            <span className={`center-chip timer ${completedAllMatches ? 'ended' : series.completedMatches > 0 ? 'live' : 'upcoming'}`.trim()}>
              {series.completedMatches}/{series.matches.length} played
            </span>
            <button
              type="button"
              className="center-btn ghost event-knockout-expand-btn"
              onClick={() =>
                setExpandedKnockoutSeries((current) => ({
                  ...current,
                  [series.key]: !current[series.key],
                }))
              }
            >
              {isExpanded ? 'Collapse' : 'Expand'}
            </button>
          </div>
        </header>
        <div className="event-knockout-series-alliances">
          <div className={`center-alliance-col red ${series.seriesWinner === 'red' ? 'winner' : ''}`.trim()}>
            <label>Red</label>
            <p>{lineupLabel(series.redTeams)}</p>
          </div>
          <div className={`center-alliance-col blue ${series.seriesWinner === 'blue' ? 'winner' : ''}`.trim()}>
            <label>Blue</label>
            <p>{lineupLabel(series.blueTeams)}</p>
          </div>
        </div>
        <p className="event-knockout-series-winner">
          <strong>Winner:</strong> {winnerSummary}
        </p>
        {isExpanded ? (
          <ul className="center-simple-list event-knockout-series-matches">
            {series.matches.map((match) => {
              const timer = liveTimerLabel(match.scheduled_time, nowMs);
              const isCompleted = inferMatchCompleted(match, nowMs);
              const winner = inferWinnerAlliance(match, nowMs);
              const hasScores = matchHasScores(match);
              return (
                <li key={`${keyPrefix}-${series.key}-${match.match_key}`}>
                  <button type="button" className="center-inline-link" onClick={() => openMatchCenter(match.match_key)}>
                    {match.display_name}
                  </button>
                  <span>
                    {hasScores ? `${match.red_score}-${match.blue_score}` : timer.value}
                    {' · '}
                    {winner ? (winner === 'tie' ? 'Tie' : `${titleizeKey(winner)} wins`) : isCompleted ? 'Final' : timer.label}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : null}
      </article>
    );
  }

  const eventHeaderName = eventScheduleName || eventTeams?.event_name || selectedEventKey || 'Event Center';
  const mobileSidebarOpen = isMobileLayout && mobilePanel !== 'center';

  return (
    <div className="events-page">
    <div
      className={`center-layout mobile-finder-layout events-center-layout scouting-layout-grid ${mobileSidebarOpen ? 'mobile-finder-open' : ''} ${isMobileLayout && mobilePanel === 'calendar' ? 'mobile-calendar-open' : ''}`.trim()}
    >
      {isMobileLayout ? (
        <SegmentedTabs
          className="mobile-view-toggle events-mobile-view-toggle"
          itemClassName="mobile-view-toggle-btn events-mobile-view-toggle-btn"
          ariaLabel="Events mobile view switch"
          value={mobilePanel}
          onChange={setMobilePanel}
          items={[
            { value: 'finder', label: 'Event Finder' },
            { value: 'calendar', label: 'Calendar' },
            { value: 'center', label: 'Event Center', disabled: !selectedEventKey },
          ]}
        />
      ) : null}
      <aside className="center-sidebar">
        {!isMobileLayout || mobilePanel === 'finder' ? (
        <SurfaceCard title="Event Finder">
          <form className="center-input-row center-input-row-event-search" onSubmit={handleSearchSubmit}>
            <input
              value={eventQuery}
              onChange={(event) => setEventQuery(event.target.value)}
              placeholder='Try "houston district 2026" or "toronto regional"'
              aria-label="Search events"
            />
            <select
              value={regionFilter}
              onChange={(event) => setRegionFilter(normalizeRegionFilter(event.target.value))}
              className="center-input"
              aria-label="Event region filter"
            >
              {REGION_FILTER_OPTIONS.map((option) => (
                <option key={`events-region-${option.value}`} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <button type="submit" className="center-btn event-search-submit-btn" disabled={loadingSearch}>
              {loadingSearch ? 'Searching...' : 'Find Event'}
            </button>
          </form>
          <p className="center-callout muted helper-text">
            Typos and aliases supported.
          </p>


          {eventQuery.trim().length > 0 ? (
            <div className="center-list-scroll events-finder-list" role="list" aria-label="Search results">
              {!loadingSearch && visibleSearchResults.length === 0 && eventQuery.trim().length > 0 ? (
                searchError ? (
                  <EmptyState compact type="offline" title="Search unavailable" description={searchError} />
                ) : (
                  <EmptyState
                    compact
                    title="No matching events"
                    description="No matching events found."
                  />
                )
              ) : null}
              {visibleSearchResults.map((event) => (
                <button
                  key={`search-${event.event_key}`}
                  className={`event-picker-item events-finder-item ${
                    selectedEventKey === event.event_key.toLowerCase() ? 'active' : ''
                  }`.trim()}
                  onClick={() => openEvent(event.event_key)}
                  type="button"
                  title={`Switch to ${event.name}`}
                >
                  <strong>{event.name}</strong>
                  <small>{event.year} · {eventTypeTags(event).join(' · ')}</small>
                  <small>{eventLabel(event)}</small>
                </button>
              ))}
            </div>
          ) : (
            <div className="center-list-scroll events-finder-list" role="list" aria-label="Suggested events">
              {loadingSuggested ? (
                <div className="center-loading-state">
                  <SkeletonBlock rows={4} compact />
                </div>
              ) : null}
              {!loadingSuggested && visibleSuggestedEvents.length === 0 ? (
                <EmptyState compact title="No suggested events" description="No suggested events available." />
              ) : null}
              {visibleSuggestedEvents.map((event) => (
                <button
                  key={`suggested-${event.event_key}`}
                  className={`event-picker-item events-finder-item ${
                    selectedEventKey === event.event_key.toLowerCase() ? 'active' : ''
                  }`.trim()}
                  onClick={() => openEvent(event.event_key)}
                  type="button"
                  title={`Switch to ${event.name}`}
                >
                  <strong>{event.name}</strong>
                  <small>{event.year} · {eventTypeTags(event).join(' · ')}</small>
                  <small>{eventLabel(event)}</small>
                </button>
              ))}
            </div>
          )}
        </SurfaceCard>
        ) : null}

        {!isMobileLayout || mobilePanel === 'calendar' ? (
        <SurfaceCard title={calendarMonthLabel} subtitle={`${visibleCalendarEvents.length} events`} className="events-calendar-card">
          {isMobileLayout ? (
            <>
              <div className="events-cal-nav">
                <button
                  type="button"
                  className="events-cal-nav-btn"
                  onClick={() => setCalendarMonth((current) => shiftMonthToken(current, -1))}
                  aria-label="Previous month"
                >
                  <ChevronLeftIcon className="icon-inline" />
                </button>
                <span className="events-cal-nav-label">{calendarMonthLabel}</span>
                <button
                  type="button"
                  className="events-cal-nav-btn"
                  onClick={() => setCalendarMonth((current) => shiftMonthToken(current, 1))}
                  aria-label="Next month"
                >
                  <ChevronRightIcon className="icon-inline" />
                </button>
              </div>
              <div className="events-cal-list">
                {visibleCalendarEvents.length === 0 ? (
                  <p className="events-cal-empty">No events this month.</p>
                ) : null}
                {visibleCalendarEvents.map((event) => {
                  const key = event.event_key.toLowerCase();
                  const resolved = resolveEventDateRange(event);
                  const dateLabel = resolved.startMs
                    ? new Date(resolved.startMs).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                    : 'TBA';
                  return (
                    <button
                      key={`events-cal-${event.event_key}`}
                      type="button"
                      className={`events-cal-item ${selectedEventKey === key ? 'active' : ''}`.trim()}
                      onClick={() => openEvent(event.event_key)}
                      title={event.name}
                    >
                      <span className="events-cal-item-date">{dateLabel}</span>
                      <span className="events-cal-item-name">{event.name}</span>
                    </button>
                  );
                })}
              </div>
              {dateTbaCalendarEvents.length > 0 ? (
                <p className="events-cal-tba">Date TBA: {dateTbaCalendarEvents.length} events</p>
              ) : null}
            </>
          ) : (
            <>
              <button
                type="button"
                className="center-btn events-calendar-open-btn"
                onClick={() => setCalendarModalOpen(true)}
                title="Open full calendar"
              >
                <CalendarIcon className="icon-inline" /> Open Calendar
              </button>
              <p className="center-callout muted events-calendar-summary">
                {visibleCalendarEvents.length} event{visibleCalendarEvents.length === 1 ? '' : 's'} in{' '}
                {regionLabel(regionFilter)} for {calendarMonthLabel}.
              </p>
              {dateTbaCalendarEvents.length > 0 ? (
                <p className="center-callout muted helper-text events-calendar-tba-label">
                  Date TBA events: {dateTbaCalendarEvents.length}
                </p>
              ) : null}
            </>
          )}
        </SurfaceCard>
        ) : null}
      </aside>

      <section className="center-main">
        {/* ── Mobile: FotMob-style event header ── */}
        {isMobileLayout && selectedEventKey ? (
          <>
            <div className="fm-event-header">
              <div className="fm-event-header-top">
                <div className="fm-event-header-info">
                  <h2>{eventHeaderName}</h2>
                  <span className="fm-event-key">{selectedEventKey}</span>
                </div>
                <div className="fm-event-header-actions">
                  <button
                    className="fm-event-sync-btn"
                    onClick={() => void handleSyncEvent()}
                    disabled={!selectedEventKey || syncingEvent || !canSyncEvent}
                  >
                    {syncingEvent ? 'Syncing...' : <><CloudSyncIcon className="icon-inline" /> Sync</>}
                  </button>
                  <ActionOverflowMenu
                    className="compact"
                    label="More"
                    items={[
                      { label: 'Home', to: '/home' },
                    ]}
                  />
                </div>
              </div>
              <div className="fm-event-header-stats">
                <div className="fm-event-stat">
                  <strong>{eventSchedule.length}</strong>
                  <span>Matches</span>
                </div>
                <div className="fm-event-stat">
                  <strong>{effectiveTeamCount}</strong>
                  <span>Teams</span>
                </div>
                <div className="fm-event-stat">
                  <strong>{liveMatchCount}</strong>
                  <span>Live</span>
                </div>
              </div>
              {eventError ? <p className="fm-event-header-error">{eventError}</p> : null}
              {usingScheduleFallbackTeams ? (
                <p className="fm-event-header-notice">Using roster from published match schedule.</p>
              ) : null}
              <SegmentedTabs
                className="fm-tab-bar"
                itemClassName="fm-tab"
                ariaLabel="Event center tabs"
                value={activeTab}
                onChange={setActiveTab}
                items={EVENT_TABS.map((tab) => ({
                  value: tab,
                  label: titleizeKey(tab),
                }))}
              />
            </div>
          </>
        ) : null}

        {/* ── Desktop: standard SurfaceCard header ── */}
        {!isMobileLayout ? (
        <SurfaceCard
          title={eventHeaderName}
          subtitle={selectedEventKey ? selectedEventKey : 'Select an event from Finder'}
          right={
            <div className="center-actions-row primary-actions">
              <button
                className="center-btn"
                onClick={() => void handleSyncEvent()}
                disabled={!selectedEventKey || syncingEvent || !canSyncEvent}
              >
                {syncingEvent ? 'Syncing...' : <><CloudSyncIcon className="icon-inline" /> Sync Event</>}
              </button>
              <Link className="center-btn ghost" to="/home">
                Home
              </Link>
            </div>
          }
        >
          <div className="center-status-row">
            <span className="center-chip"><TagIcon className="icon-inline icon-muted" /> {statusText}</span>
            <span className="center-chip"><ClockIcon className="icon-inline icon-muted" /> {relativeFromTimestamp(lastUpdatedAt)}</span>
            <span className="center-chip"><ScoreboardIcon className="icon-inline icon-muted" /> {eventSchedule.length} matches · {effectiveTeamCount} teams</span>
            <span className="center-chip">{liveMatchCount > 0 ? <LiveDotIcon className="icon-inline icon-status-live icon-live-pulse" /> : null} {liveMatchCount} live</span>
            <span className="center-chip"><RefreshIcon className="icon-inline icon-muted" /> {effectiveEventPollSec}s refresh</span>
            <span className="center-chip"><GlobeIcon className="icon-inline icon-muted" /> {regionLabel(regionFilter)}</span>
            <span className="center-chip">
              Writes:{' '}
                {canSyncEvent
                  ? 'enabled'
                  : publicReadonlyMode
                    ? 'public read-only'
                    : writeAuthEnforced && !hasClientAdminKey
                      ? 'client admin key missing'
                      : 'restricted'}
            </span>
          </div>
          {!canSyncEvent ? (
            <p className="center-callout muted helper-text">
              {publicReadonlyMode === true
                ? 'Event sync disabled in public read-only mode.'
                : 'Event sync requires an active admin session.'}
            </p>
          ) : null}
          {eventError ? <EmptyState compact type="offline" title="Connection failed" description={eventError} /> : null}
          {usingScheduleFallbackTeams ? (
            <p className="center-callout muted helper-text">Team history unavailable, using roster from published match schedule.</p>
          ) : null}
          <div className="center-tabs-header">
            <SegmentedTabs
              className="center-tabs"
              itemClassName="center-tab-btn"
              ariaLabel="Event center tabs"
              value={activeTab}
              onChange={setActiveTab}
              items={EVENT_TABS.map((tab) => ({
                value: tab,
                label: titleizeKey(tab),
                icon: EVENT_TAB_ICONS[tab],
              }))}
            />
          </div>
        </SurfaceCard>
        ) : null}

        {/* ── Mobile: no-event-selected fallback ── */}
        {isMobileLayout && !selectedEventKey ? (
          <SurfaceCard title="No Event Selected" subtitle="Pick an event in the Finder.">
            <EmptyState compact title="No event selected" description={`${CURRENT_SEASON_YEAR} first, ${FALLBACK_SEASON_YEAR} fallback.`} />
          </SurfaceCard>
        ) : null}

        {selectedEventKey && loadingEventData && eventSchedule.length === 0 && effectiveTeamCount === 0 ? (
          <SurfaceCard title="Loading" compactable>
            <SkeletonBlock rows={6} />
          </SurfaceCard>
        ) : null}

        {!isMobileLayout && !selectedEventKey ? (
          <SurfaceCard title="No Event Selected" subtitle="Pick an event in the Finder.">
            <EmptyState compact title="No event selected" description={`${CURRENT_SEASON_YEAR} first, ${FALLBACK_SEASON_YEAR} fallback.`} />
          </SurfaceCard>
        ) : null}

        {selectedEventKey && activeTab === 'overview' ? (
          <SurfaceCardGroup groupId="event-center-overview">
            {/* ── Mobile: FotMob-style overview ── */}
            {isMobileLayout ? (
              <div className="fm-content-stack">
                {loadingEventData ? (
                  <div className="center-loading-state">
                    <SkeletonBlock rows={3} compact />
                  </div>
                ) : null}
                <div className="fm-kpi-grid">
                  <article className="fm-kpi-card">
                    <span><ScoreboardIcon className="icon-inline" /> Matches</span>
                    <strong>{eventSchedule.length}</strong>
                  </article>
                  <article className="fm-kpi-card">
                    <span><UsersIcon className="icon-inline" /> Teams</span>
                    <strong>{effectiveTeamCount}</strong>
                  </article>
                  <article className="fm-kpi-card">
                    <span><LiveDotIcon className="icon-inline" /> Live</span>
                    <strong>{liveMatchCount}</strong>
                  </article>
                  <article className="fm-kpi-card">
                    <span><TagIcon className="icon-inline" /> Key</span>
                    <strong style={{ fontSize: '0.72rem' }}>{selectedEventKey}</strong>
                  </article>
                </div>

                <div className="fm-top-card">
                  <div className="fm-top-card-header">
                    <h4>Top Rankings</h4>
                    <p>Current leaderboard</p>
                  </div>
                  {rankingRows.length === 0 ? (
                    <EmptyState compact title="Rankings unavailable" description="Rankings are not published yet." />
                  ) : (
                    <ul className="fm-top-list">
                      {rankingRows.slice(0, 6).map((row) => (
                        <li key={`fm-overview-rank-${row.team_key}`}>
                          <span className="fm-top-rank">{row.rank ?? '-'}</span>
                          <button type="button" className="fm-top-team-btn" onClick={() => openTeamCenter(row.team_key)}>
                            #{row.team_number} {row.nickname}
                          </button>
                          <span className="fm-top-value">{row.record}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {rankingRows.length > 0 ? (
                    <div className="center-actions-row compact" style={{ padding: '8px 12px 10px' }}>
                      <button type="button" className="center-btn ghost" onClick={() => setActiveTab('rankings')}>
                        Open Rankings
                      </button>
                    </div>
                  ) : null}
                </div>

                <div className="fm-top-card">
                  <div className="fm-top-card-header">
                    <h4>Top By Coverage</h4>
                    <p>Most analyzed teams</p>
                  </div>
                  {topAnalyzedTeams.length === 0 ? (
                    <EmptyState compact title="No coverage yet" description="No analyzed team history yet." />
                  ) : (
                    <ul className="fm-top-list">
                      {topAnalyzedTeams.map((team, idx) => (
                        <li key={`fm-analyzed-${team.team_key}`}>
                          <span className="fm-top-rank">{idx + 1}</span>
                          <button type="button" className="fm-top-team-btn" onClick={() => openTeamCenter(team.team_key)}>
                            #{team.team_number} {team.nickname || team.team_key}
                          </button>
                          <span className="fm-top-value">{team.history_count} matches</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div className="fm-top-card">
                  <div className="fm-top-card-header">
                    <h4>Top Fuel Rate</h4>
                    <p>Best fuel scoring pace</p>
                  </div>
                  {topFuelTeams.length === 0 ? (
                    <EmptyState compact title="No fuel metrics yet" description="Fuel-rate metrics still sparse." />
                  ) : (
                    <ul className="fm-top-list">
                      {topFuelTeams.map((team, idx) => (
                        <li key={`fm-fuel-${team.team_key}`}>
                          <span className="fm-top-rank">{idx + 1}</span>
                          <button type="button" className="fm-top-team-btn" onClick={() => openTeamCenter(team.team_key)}>
                            #{team.team_number} {team.nickname || team.team_key}
                          </button>
                          <span className="fm-top-value">{metric(team.averages?.fuel_scoring_rate ?? null, 2)}/min</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ) : null}

            {/* ── Desktop: original overview ── */}
            {!isMobileLayout ? (
            <div className="center-content-grid">
            <SurfaceCard title="Overview" compactable>
              {loadingEventData ? (
                <div className="center-loading-state">
                  <SkeletonBlock rows={3} compact />
                </div>
              ) : null}
              <div className="center-kpi-grid">
                <article className="center-kpi-card">
                  <span><TagIcon className="icon-inline icon-muted" /> Event Key</span>
                  <strong>{selectedEventKey}</strong>
                </article>
                <article className="center-kpi-card">
                  <span><ScoreboardIcon className="icon-inline icon-muted" /> Matches Published</span>
                  <strong>{eventSchedule.length}</strong>
                </article>
                <article className="center-kpi-card">
                  <span><UsersIcon className="icon-inline icon-muted" /> Teams Registered</span>
                  <strong>{effectiveTeamCount}</strong>
                </article>
                <article className="center-kpi-card">
                  <span><LiveDotIcon className="icon-inline icon-status-live" /> Live Matches</span>
                  <strong>{liveMatchCount}</strong>
                </article>
              </div>
            </SurfaceCard>

            <SurfaceCard title="Top By Coverage" compactable>
              {topAnalyzedTeams.length === 0 ? (
                <EmptyState compact title="No coverage yet" description="No analyzed team history yet." />
              ) : (
                <ul className="center-simple-list">
                  {topAnalyzedTeams.map((team) => (
                    <li key={`analyzed-${team.team_key}`}>
                      <button type="button" className="center-inline-link" onClick={() => openTeamCenter(team.team_key)}>
                        #{team.team_number} {team.nickname || team.team_key}
                      </button>
                      <span>{team.history_count} analyzed matches</span>
                    </li>
                  ))}
                </ul>
              )}
            </SurfaceCard>

            <SurfaceCard title="Top Fuel Rate" compactable>
              {topFuelTeams.length === 0 ? (
                <EmptyState compact title="No fuel metrics yet" description="Fuel-rate metrics still sparse." />
              ) : (
                <ul className="center-simple-list">
                  {topFuelTeams.map((team) => (
                    <li key={`fuel-${team.team_key}`}>
                      <button type="button" className="center-inline-link" onClick={() => openTeamCenter(team.team_key)}>
                        #{team.team_number} {team.nickname || team.team_key}
                      </button>
                      <span>{metric(team.averages?.fuel_scoring_rate ?? null, 2)} fuel/min</span>
                    </li>
                  ))}
                </ul>
              )}
            </SurfaceCard>
            </div>
            ) : null}
          </SurfaceCardGroup>
        ) : null}

        {selectedEventKey && activeTab === 'schedule' ? (
          <SurfaceCardGroup groupId="event-center-schedule">
            <SurfaceCard title="Schedule" compactable>
            {loadingEventData ? (
              <div className="center-loading-state">
                <SkeletonBlock rows={5} compact />
              </div>
            ) : null}
            {!loadingEventData && eventSchedule.length === 0 ? (
              <EmptyState compact title="No schedule rows" description="No schedule rows for this event." />
            ) : null}
            <div className="center-list-stack">
              {visibleScheduleRows.map((match) => {
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
                const winnerClass =
                  winner === 'red'
                    ? 'winner-red'
                    : winner === 'blue'
                      ? 'winner-blue'
                      : winner === 'tie'
                        ? 'winner-tie'
                        : '';
                const scoreboard = hasScores ? `${match.red_score}-${match.blue_score}` : timer.value;
                const winnerText =
                  isCompleted && winner
                    ? winner === 'tie'
                      ? 'Tie'
                      : `${titleizeKey(winner)} wins ${match.winning_score ?? ''}`.trim()
                    : null;
                const mobileExpanded = isMobileLayout ? isMobileScheduleMatchExpanded(match.match_key) : false;
                const compactRed = compactAllianceLabel(match.red);
                const compactBlue = compactAllianceLabel(match.blue);
                const allianceSplit = (
                  <div className={`center-alliance-split ${isMobileLayout ? 'event-schedule-mobile-alliances' : ''}`.trim()}>
                    <div className={`center-alliance-col red ${winner === 'red' ? 'winner' : ''}`.trim()}>
                      <label>
                        Red Alliance
                        {hasScores ? ` · ${match.red_score}` : ''}
                      </label>
                      {match.red.map((team) => {
                        const entry = liveStatusByTeam[team.team_key.toLowerCase()] || null;
                        return (
                          <button
                            type="button"
                            key={`${match.match_key}-red-${team.team_key}`}
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

                    <div className={`center-alliance-col blue ${winner === 'blue' ? 'winner' : ''}`.trim()}>
                      <label>
                        Blue Alliance
                        {hasScores ? ` · ${match.blue_score}` : ''}
                      </label>
                      {match.blue.map((team) => {
                        const entry = liveStatusByTeam[team.team_key.toLowerCase()] || null;
                        return (
                          <button
                            type="button"
                            key={`${match.match_key}-blue-${team.team_key}`}
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
                );
                return (
                  <article
                    key={match.match_key}
                    className={
                      isMobileLayout
                        ? `home-match-row event-schedule-mobile-row ${winnerClass} ${mobileExpanded ? 'mobile-expanded' : 'mobile-compact'}`.trim()
                        : `center-match-row ${winnerClass}`.trim()
                    }
                  >
                    {isMobileLayout ? (
                      <>
                        <button
                          type="button"
                          className="home-match-main event-schedule-mobile-main"
                          onClick={() => {
                            if (!mobileExpanded) {
                              expandMobileScheduleMatch(match.match_key);
                              return;
                            }
                            openMatchCenter(match.match_key);
                          }}
                          title={
                            mobileExpanded
                              ? `Open ${match.display_name} in Match Center`
                              : `Expand ${match.display_name}`
                          }
                          aria-expanded={mobileExpanded}
                        >
                          {!mobileExpanded ? (
                            <div className="home-match-compact-body">
                              <div className="home-match-compact-head">
                                <strong>{match.display_name}</strong>
                                <span className={`home-match-compact-state ${effectiveState}`}>{compactStateLabel(effectiveState)}</span>
                              </div>
                              <div className="home-match-compact-line">
                                <span className="home-match-compact-side red">{compactRed}</span>
                                <span className="home-match-compact-score">{scoreboard}</span>
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
                                  <span className={`home-match-state ${effectiveState}`}>
                                    {isCompleted ? 'Final' : timer.label}
                                  </span>
                                  <span className="fm-match-score-timer">{scoreboard}</span>
                                </div>
                              </div>
                              {winnerText ? (
                                <span className="home-winner-chip">{winnerText}</span>
                              ) : null}
                              <small className="home-match-open-hint">Tap again to open Match Center</small>
                            </>
                          )}
                        </button>

                        {mobileExpanded ? (
                          <>
                            {allianceSplit}
                            <footer className="center-match-actions event-schedule-mobile-actions">
                              <button
                                type="button"
                                className="center-btn ghost"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  collapseMobileScheduleMatch(match.match_key);
                                }}
                                title="Collapse match details"
                              >
                                <ChevronDownIcon className="icon-inline" /> Collapse
                              </button>
                              <button
                                type="button"
                                className="center-btn"
                                onClick={() => openMatchCenter(match.match_key)}
                                title={`Open ${match.display_name} details`}
                              >
                                Match Details
                              </button>
                            </footer>
                          </>
                        ) : null}
                      </>
                    ) : (
                      <>
                        <header className="center-match-head">
                          <div>
                            <strong>{match.display_name}</strong>
                          </div>
                          <div className="center-match-state-block">
                            <span className={`center-status-pill ${effectiveState}`}>
                              {isCompleted
                                ? <><CheckCircleIcon className="icon-inline" /> Final</>
                                : effectiveState === 'live'
                                  ? <><LiveDotIcon className="icon-inline icon-live-pulse" /> {timer.label}</>
                                  : <><ClockIcon className="icon-inline" /> {timer.label}</>}
                            </span>
                            <strong>{scoreboard}</strong>
                            {winnerText ? (
                              <small className="center-match-winner-chip">{winnerText}</small>
                            ) : null}
                            <small>{fmtDateShort(match.scheduled_time)}</small>
                          </div>
                        </header>

                        {allianceSplit}

                        <footer className="center-match-actions">
                          <button type="button" className="center-btn" onClick={() => openMatchCenter(match.match_key)} title={`Open ${match.display_name} details`}>
                            Match Details
                          </button>
                        </footer>
                      </>
                    )}
                  </article>
                );
              })}
            </div>
            {eventSchedule.length > visibleScheduleRows.length ? (
              <div className="center-actions-row">
                <button
                  type="button"
                  className="center-btn ghost"
                  onClick={() => setScheduleVisibleCount((prev) => prev + 40)}
                >
                  Show More Matches ({visibleScheduleRows.length}/{eventSchedule.length})
                </button>
              </div>
            ) : null}
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}

        {selectedEventKey && activeTab === 'breakdown' ? (
          <SurfaceCardGroup groupId="event-center-breakdown">
            <SurfaceCard
              title="Event Breakdown"
              compactable
            >
            <SegmentedTabs
              className="center-tabs event-breakdown-stage-tabs"
              itemClassName="center-tab-btn"
              ariaLabel="Breakdown match type tabs"
              value={breakdownStage}
              onChange={(next) => {
                setBreakdownStageUserLocked(true);
                setBreakdownStage(next);
              }}
              items={[
                {
                  value: 'qualifying',
                  label: `Qualifying (${qualificationMatches.length})`,
                  icon: <ListIcon className="icon-inline" />,
                },
                {
                  value: 'knockout',
                  label: `Knockout (${knockoutMatches.length})`,
                  icon: <BracketIcon className="icon-inline" />,
                },
              ]}
            />

            {breakdownStage === 'qualifying' ? (
              <>
                {qualificationMatches.length === 0 ? (
                  <p className="center-callout muted">No qualifying rows published yet.</p>
                ) : (
                  <>
                    <div className="center-kpi-grid">
                      <article className="center-kpi-card">
                        <span><ScoreboardIcon className="icon-inline icon-muted" /> Total Qual Matches</span>
                        <strong>{qualificationSummary.total}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><CheckCircleIcon className="icon-inline icon-muted" /> Completed</span>
                        <strong>{qualificationSummary.completed}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><LiveDotIcon className="icon-inline icon-status-live" /> Live</span>
                        <strong>{qualificationSummary.live}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><ClockIcon className="icon-inline icon-status-upcoming" /> Upcoming</span>
                        <strong>{qualificationSummary.upcoming}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><TargetIcon className="icon-inline icon-muted" /> Avg Combined Score</span>
                        <strong>{metric(qualificationSummary.avgCombinedScore, 1)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span><DeltaIcon className="icon-inline icon-muted" /> Avg Margin</span>
                        <strong>{metric(qualificationSummary.avgMargin, 1)}</strong>
                      </article>
                    </div>

                    <Table
                      columns={[
                        {
                          key: 'display_name',
                          label: 'Match',
                          render: (match) => (
                            <button
                              type="button"
                              className="center-inline-link"
                              onClick={() => openMatchCenter(match.match_key)}
                              title={`Open ${match.display_name}`}
                            >
                              {match.display_name}
                            </button>
                          ),
                        },
                        {
                          key: 'scheduled_time',
                          label: 'Time',
                          sortable: true,
                          render: (match) => fmtDateShort(match.scheduled_time),
                        },
                        {
                          key: 'status',
                          label: 'Status',
                          align: 'center',
                          render: (match) => {
                            const timer = liveTimerLabel(match.scheduled_time, nowMs);
                            const isCompleted = inferMatchCompleted(match, nowMs);
                            return (
                              <span className={`center-status-pill ${isCompleted ? 'ended' : timer.state}`}>
                                {isCompleted ? 'Final' : timer.label}
                              </span>
                            );
                          },
                        },
                        {
                          key: 'score',
                          label: 'Score',
                          numeric: true,
                          render: (match) =>
                            matchHasScores(match)
                              ? `${match.red_score}-${match.blue_score}`
                              : <span className="center-muted">N/A</span>,
                        },
                        {
                          key: 'winner',
                          label: 'Winner',
                          align: 'center',
                          render: (match) => {
                            const winner = inferWinnerAlliance(match, nowMs);
                            return winner ? (
                              <span className="center-match-winner-chip">
                                {winner === 'tie' ? 'Tie' : `${titleizeKey(winner)} wins`}
                              </span>
                            ) : (
                              <span className="center-muted">Pending</span>
                            );
                          },
                        },
                      ]}
                      rows={sortedQualificationMatches}
                      rowKey={(match) => match.match_key}
                      stickyHeader
                      empty="No qualification matches yet."
                    />
                  </>
                )}
              </>
            ) : null}

            {breakdownStage === 'knockout' ? (
              <>
                {knockoutMatches.length === 0 ? (
                  <p className="center-callout muted">
                    No knockout matches published yet.
                  </p>
                ) : (
                  <>
                    <div className="center-kpi-grid">
                      <article className="center-kpi-card">
                        <span>Total Knockout Matches</span>
                        <strong>{knockoutSummary.total}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span>Completed</span>
                        <strong>{knockoutSummary.completed}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span>Live</span>
                        <strong>{knockoutSummary.live}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span>Upcoming</span>
                        <strong>{knockoutSummary.upcoming}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span>Avg Combined Score</span>
                        <strong>{metric(knockoutSummary.avgCombinedScore, 1)}</strong>
                      </article>
                      <article className="center-kpi-card">
                        <span>Avg Margin</span>
                        <strong>{metric(knockoutSummary.avgMargin, 1)}</strong>
                      </article>
                    </div>

                    <div className="center-actions-row">
                      <button
                        type="button"
                        className="center-btn ghost"
                        onClick={() => {
                          const next: Record<string, boolean> = {};
                          allKnockoutSeriesKeys.forEach((key) => {
                            next[key] = true;
                          });
                          setExpandedKnockoutSeries(next);
                        }}
                        disabled={allKnockoutSeriesKeys.length === 0}
                      >
                        Expand All
                      </button>
                      <button
                        type="button"
                        className="center-btn ghost"
                        onClick={() => setExpandedKnockoutSeries({})}
                        disabled={allKnockoutSeriesKeys.length === 0}
                      >
                        Collapse All
                      </button>
                    </div>

                    <div className="event-knockout-bracket-board">
                      <div className="event-knockout-summary-head">
                        <div className="event-knockout-center-banner">
                          <strong>Knockout Bracket</strong>
                        </div>
                        <div className="event-knockout-center-champion">
                          <label>Champion</label>
                          <strong>{knockoutChampionLabel}</strong>
                        </div>
                      </div>

                      <div className="event-knockout-rounds-scroll">
                        <div className="event-knockout-round-columns">
                          {knockoutSeriesByRound.map(([label, seriesRows], roundIdx) => {
                            const completedSeries = seriesRows.filter(
                              (series) => series.completedMatches >= series.matches.length,
                            ).length;
                            return (
                              <section key={`knockout-round-${label}`} className="event-knockout-round-column">
                                <header className="event-knockout-round-head">
                                  <strong>{label}</strong>
                                  <small>{seriesRows.length} series</small>
                                  <span className="center-chip">{completedSeries}/{seriesRows.length} complete</span>
                                </header>
                                <div className="event-knockout-round-advance">
                                  {seriesRows.length > 0 ? (
                                    <span>
                                      Advancing: {seriesRows.map((series) => seriesWinnerLineup(series)).join(' | ')}
                                    </span>
                                  ) : (
                                    <span>Advancing: TBD</span>
                                  )}
                                </div>
                                <div className="event-knockout-series-grid">
                                  {seriesRows.map((series) => renderKnockoutSeriesCard(series, `ko-round-${roundIdx}`))}
                                </div>
                              </section>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </>
            ) : null}

            {allianceSelectionsReady ? (
              <section className="event-awards-breakdown">
                <header className="event-awards-breakdown-head">
                  <div>
                    <strong>Alliance Selections</strong>
                    <small>
                      {allianceSelectionRows.length} alliance{allianceSelectionRows.length === 1 ? '' : 's'}
                      {eventAlliancesLastModified ? ` · Updated ${eventAlliancesUpdatedLabel}` : ''}
                      {' · Auto-refresh 30s'}
                    </small>
                  </div>
                  <div className="center-actions-row compact">
                    <button
                      type="button"
                      className="center-btn ghost"
                      onClick={() => {
                        void refreshAllianceSelectionsManually();
                      }}
                      disabled={loadingEventAlliances}
                    >
                      <RefreshIcon className="icon-inline" /> Refresh
                    </button>
                    <button
                      type="button"
                      className="center-btn ghost"
                      onClick={() => {
                        void copyAllianceSelections();
                      }}
                      disabled={allianceSelectionRows.length === 0}
                    >
                      <CopyIcon className="icon-inline" /> Copy
                    </button>
                    <button
                      type="button"
                      className="center-btn ghost"
                      onClick={exportAllianceSelectionsCsv}
                      disabled={allianceSelectionRows.length === 0}
                    >
                      <DownloadIcon className="icon-inline" /> CSV
                    </button>
                  </div>
                </header>
                {allianceActionStatus ? <p className="center-callout muted">{allianceActionStatus}</p> : null}
                {loadingEventAlliances ? (
                  <div className="center-loading-state">
                    <SkeletonBlock rows={3} compact />
                  </div>
                ) : null}
                {eventAlliancesError ? (
                  <p className="center-callout warning">Alliance selections unavailable: {eventAlliancesError}</p>
                ) : null}
                {!loadingEventAlliances && !eventAlliancesError && allianceSelectionRows.length === 0 ? (
                  <EmptyState
                    compact
                    title="Alliances not posted yet"
                    description="Alliance selections will appear here once published."
                  />
                ) : null}
                {allianceSelectionRows.length > 0 ? (
                  <div className="event-awards-grid">
                    {allianceSelectionRows.map((alliance) => (
                      <article key={alliance.key} className="event-award-card">
                        <strong>{alliance.name}</strong>
                        <p>Alliance {alliance.number}</p>
                        {alliance.teams.length > 0 ? (
                          <div className="center-list-stack">
                            {alliance.teams.map((team) => (
                              <small key={`${alliance.key}-${team.slot}-${team.teamNumber}`}>
                                {team.slot}: #{team.teamNumber}{team.name ? ` · ${team.name}` : ''}
                              </small>
                            ))}
                          </div>
                        ) : (
                          <small>No team slots published yet.</small>
                        )}
                      </article>
                    ))}
                  </div>
                ) : null}
              </section>
            ) : null}

            <div className="center-divider" />
            <section className="event-awards-breakdown">
              <header className="event-awards-breakdown-head">
                <strong>Event Awards</strong>
                <small>{eventAwards?.count ?? 0} award rows loaded</small>
              </header>
              {eventAwardsError ? <EmptyState compact type="offline" title="Awards unavailable" description={eventAwardsError} /> : null}
              {!eventAwardsError && (eventAwards?.count ?? 0) === 0 ? (
                <EmptyState compact title="No awards yet" description="No awards were returned for this event yet." />
              ) : null}
              {breakdownAwardRows.length > 0 ? (
                <div className="event-awards-grid">
                  {breakdownAwardRows.map((row) => (
                    <article key={`award-${row.key}`} className="event-award-card">
                      <strong>{row.label}</strong>
                      <p>{row.winners.length > 0 ? row.winners.join(', ') : 'Not awarded yet'}</p>
                      {row.matchedNames.length > 0 ? <small>Source: {row.matchedNames.join(' · ')}</small> : null}
                    </article>
                  ))}
                </div>
              ) : null}
            </section>
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}

        {selectedEventKey && activeTab === 'rankings' ? (
          <SurfaceCardGroup groupId="event-center-rankings">
            <SurfaceCard title="Rankings" compactable>
            {loadingEventData ? (
              <div className="center-loading-state">
                <SkeletonBlock rows={5} compact />
              </div>
            ) : null}
            {!loadingEventData && rankingRows.length === 0 ? (
              <EmptyState compact title="Rankings unavailable" description="Rankings are not published yet." />
            ) : null}
            {rankingRows.length > 0 ? (
              <>
                {/* One Table owning the columns, rows and sorting. The narrow
                    view keeps its compact standings grid through renderCards —
                    30 rows of six short figures fit on a phone, 30 stacked
                    cards of six labelled lines do not. */}
                <Table
                  columns={[
                    { key: 'rank', label: 'Rank', numeric: true, sortable: true, width: '80px', render: (row) => row.rank ?? 'N/A' },
                    {
                      key: 'team_number',
                      label: 'Team',
                      sortable: true,
                      render: (row) => (
                        <button
                          type="button"
                          className="center-inline-link"
                          onClick={() => openTeamCenter(row.team_key)}
                          title={`View Team ${row.team_number} · Record: ${row.record}`}
                        >
                          #{row.team_number} {row.nickname}
                        </button>
                      ),
                    },
                    { key: 'record', label: 'Record', align: 'center', width: '110px' },
                    { key: 'matches_played', label: 'Played', numeric: true, sortable: true, width: '90px', render: (row) => row.matches_played ?? 'N/A' },
                    {
                      key: 'sort0',
                      label: rankingSortLabels[0] || 'S1',
                      numeric: true,
                      render: (row) => row.sort_orders[0] ?? 'N/A',
                    },
                    {
                      key: 'sort1',
                      label: rankingSortLabels[1] || 'S2',
                      numeric: true,
                      render: (row) => row.sort_orders[1] ?? 'N/A',
                    },
                  ]}
                  rows={visibleRankingRows}
                  rowKey={(row) => row.team_key}
                  stickyHeader
                  empty="No rankings published yet."
                  renderCards={(narrowRows) => (
                    <div className="fm-standings-card">
                      <div className="fm-standings-header">
                        <span>#</span>
                        <span>Team</span>
                        <span>PL</span>
                        <span>Rec</span>
                        <span>{rankingSortLabels[0]?.slice(0, 4) || 'S1'}</span>
                        <span>{rankingSortLabels[1]?.slice(0, 4) || 'S2'}</span>
                      </div>
                      {narrowRows.map((row) => (
                        <button
                          key={`ranking-fm-${row.team_key}`}
                          type="button"
                          className="fm-standings-row"
                          onClick={() => openTeamCenter(row.team_key)}
                          title={`View Team ${row.team_number} · Record: ${row.record}`}
                        >
                          <span className="fm-standings-rank">{row.rank ?? '-'}</span>
                          <div className="fm-standings-team">
                            <strong>#{row.team_number}</strong>
                            <small>{row.nickname}</small>
                          </div>
                          <span className="fm-standings-stat">{row.matches_played ?? '-'}</span>
                          <span className="fm-standings-stat">{row.record}</span>
                          <span className="fm-standings-pts">{row.sort_orders[0] != null ? metric(row.sort_orders[0], 1) : '-'}</span>
                          <span className="fm-standings-stat">{row.sort_orders[1] != null ? metric(row.sort_orders[1], 1) : '-'}</span>
                        </button>
                      ))}
                    </div>
                  )}
                />
              </>
            ) : null}
            {sortedRankingRows.length > visibleRankingRows.length ? (
              <div className="center-actions-row">
                <button
                  type="button"
                  className="center-btn ghost"
                  onClick={() => setRankingsVisibleCount((prev) => prev + 60)}
                >
                  Show More Rankings ({visibleRankingRows.length}/{sortedRankingRows.length})
                </button>
              </div>
            ) : null}
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}

        {selectedEventKey && activeTab === 'teams' ? (
          <SurfaceCardGroup groupId="event-center-teams">
            <SurfaceCard title="Teams" compactable>
            {loadingEventData && eventTeamRows.length === 0 ? (
              <div className="center-loading-state">
                <SkeletonBlock rows={5} compact />
              </div>
            ) : null}
            {!loadingEventData && eventTeamRows.length === 0 ? (
              <EmptyState compact title="No teams yet" description="No team rows available." />
            ) : null}
            {eventTeamRows.length > 0 ? (
              <>
                <Table
                  columns={[
                    {
                      key: 'team_number',
                      label: 'Team',
                      sortable: true,
                      render: (team) => (
                        <button
                          type="button"
                          className="center-inline-link"
                          onClick={() => openTeamCenter(team.team_key)}
                        >
                          #{team.team_number} {team.nickname || team.team_key}
                        </button>
                      ),
                    },
                    {
                      key: 'live',
                      label: 'Live',
                      align: 'center',
                      render: (team) => {
                        const entry = liveStatusByTeam[team.team_key.toLowerCase()] || null;
                        return entry?.is_live ? (
                          <span className="center-live-chip">
                            <i className="center-live-dot" aria-hidden="true" />
                            Live
                          </span>
                        ) : (
                          <span className="center-muted">-</span>
                        );
                      },
                    },
                    {
                      key: 'form',
                      label: 'Form',
                      render: (team) => teamFormStrip(liveStatusByTeam[team.team_key.toLowerCase()] || null),
                    },
                    { key: 'history_count', label: 'Analyzed', numeric: true, sortable: true },
                    {
                      key: 'fuel',
                      label: 'Fuel',
                      numeric: true,
                      render: (team) => metric(team.averages?.fuel_scoring_rate ?? null, 2),
                    },
                    {
                      key: 'cycle',
                      label: 'Cycle',
                      numeric: true,
                      render: (team) => metric(team.averages?.cycle_time_sec ?? null, 1),
                    },
                    {
                      key: 'auto',
                      label: 'Auto',
                      numeric: true,
                      render: (team) => metric(team.averages?.auto_contribution ?? null, 2),
                    },
                    {
                      key: 'climb',
                      label: 'Climb',
                      numeric: true,
                      render: (team) => metric(team.averages?.climb_success_prob ?? null, 2),
                    },
                  ]}
                  rows={visibleEventTeamRows}
                  rowKey={(team) => team.team_key}
                  stickyHeader
                  empty="No team rows available."
                />
              </>
            ) : null}
            {eventTeamRows.length > visibleEventTeamRows.length ? (
              <div className="center-actions-row">
                <button
                  type="button"
                  className="center-btn ghost"
                  onClick={() => setTeamsVisibleCount((prev) => prev + 60)}
                >
                  Show More Teams ({visibleEventTeamRows.length}/{eventTeamRows.length})
                </button>
              </div>
            ) : null}
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}
      </section>
    </div>

    {calendarModalOpen ? (
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
              <h2>{calendarMonthLabel}</h2>
              <small>{visibleCalendarEvents.length} events</small>
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
              <div key={`events-weekday-${label}`} className="home-calendar-modal-weekday">{label}</div>
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
                  key={`events-modal-day-${day.token}`}
                  className={`home-calendar-modal-day ${day.inMonth ? '' : 'off-month'} ${isExpanded ? 'expanded' : ''}`.trim()}
                >
                  <div className="home-calendar-modal-day-num">{day.dayNum}</div>
                  <div className="home-calendar-modal-day-events">
                    {visible.map((event) => {
                      const key = event.event_key.toLowerCase();
                      return (
                        <button
                          key={`events-modal-chip-${day.token}-${event.event_key}`}
                          type="button"
                          className={`home-calendar-modal-chip ${selectedEventKey === key ? 'active' : ''}`.trim()}
                          onClick={() => {
                            openEvent(event.event_key);
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
