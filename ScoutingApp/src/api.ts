import { API_CACHE_BUDGET_CHARS, API_CACHE_PREFIX, freeApiCacheSpace } from "./utils/apiCacheSpace";
import { liveFreshRead, noteLiveSchedule, stripLiveVersion, withLiveVersion } from "./features/live/liveVersions";
import { isNativeApp } from './platform/runtime';
import { resolveApiBaseUrl, resolveWebSocketBaseUrl } from './platform/runtime';

const API = resolveApiBaseUrl();
const WS_API = resolveWebSocketBaseUrl(API);
const REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 12000);
const NULL_BODY_STATUSES = new Set([101, 204, 205, 304]);
import { enqueue as enqueueOffline } from "./utils/offlineQueue";
import { getStoredResponse, putStoredResponse } from "./utils/apiResponseStore";
import {
  SUGGESTED_EVENTS_SHARED_LIMIT,
  SUGGESTED_EVENTS_SHARED_TEAM_COUNT_FETCHES,
  suggestedEventsQuery,
} from "./bootPath";
import { takeBootFetch } from "./bootPrefetch";
import { markData, type DataSource } from "./perf/routeTiming";

// The write didn't reach the server but is safely queued and will replay.
// Callers that report sync results use this to say "will sync" instead of
// showing a queued save as a failure.
export class QueuedForSyncError extends Error {
  readonly queued = true;
}
import type { AutoScoutDraftRecord } from "./pages/scoutingPage.types";
import { SEASON } from './config/season';
import {
  WORKSPACE_ACCESS_HEADER,
  WORKSPACE_ACCESS_QUERY_PARAM,
  clearWorkspaceSession,
  getWorkspaceSession,
  getWorkspaceToken,
  rememberIssuedJoinCode,
  setWorkspaceSession,
  type WorkspaceMe,
  type WorkspaceSummary,
} from './features/workspace/workspaceSession';
const DEFAULT_GET_CACHE_TTL_MS = Number(import.meta.env.VITE_API_CACHE_TTL_MS || 8000);
const DEFAULT_GET_STALE_WHILE_REVALIDATE_MS = Number(import.meta.env.VITE_API_STALE_WHILE_REVALIDATE_MS || 45000);
// How long saved responses stay usable offline (labelled as saved data, never as fresh).
// Four days: an event runs three, often with no signal, and data loaded the evening
// before still has to be there on Saturday afternoon. It was 6 h.
const PERSIST_GET_CACHE_TTL_MS = Number(import.meta.env.VITE_API_PERSIST_CACHE_TTL_MS || 345600000);
// On a weak signal ("connected" but barely), wait this long for the network before
// showing the saved copy instead; the request keeps going and updates it in the background.
const WEAK_SIGNAL_FALLBACK_MS = 4000;
// Room for an event's worth of team pages (75+) next to its schedules and ratings.
const PERSIST_GET_CACHE_MAX_ENTRIES = Number(import.meta.env.VITE_API_PERSIST_CACHE_MAX_ENTRIES || 300);
const PERSIST_GET_CACHE_MAX_BODY_BYTES = Number(import.meta.env.VITE_API_PERSIST_CACHE_MAX_BODY_BYTES || 450000);
const TEAM_INTEL_CACHE_TTL_MS = Number(import.meta.env.VITE_API_TEAM_INTEL_CACHE_TTL_MS || 25000);
const EVENT_INTEL_CACHE_TTL_MS = Number(import.meta.env.VITE_API_EVENT_INTEL_CACHE_TTL_MS || 20000);
const EVENT_SCHEDULE_CACHE_TTL_MS = Number(import.meta.env.VITE_API_EVENT_SCHEDULE_CACHE_TTL_MS || 10000);
const EVENT_LIVE_FORM_CACHE_TTL_MS = Number(import.meta.env.VITE_API_EVENT_LIVE_FORM_CACHE_TTL_MS || 4000);
const STATIC_MEDIA_CACHE_TTL_MS = Number(import.meta.env.VITE_API_STATIC_MEDIA_CACHE_TTL_MS || 21600000);
const TEAM_MEDIA_CACHE_VERSION = "20260308a";
const SUGGESTED_EVENTS_CACHE_TTL_MS = Number(import.meta.env.VITE_API_SUGGESTED_EVENTS_CACHE_TTL_MS || 600000);
const SUGGESTED_EVENTS_TIMEOUT_MS = Number(import.meta.env.VITE_API_SUGGESTED_TIMEOUT_MS || 4500);
const SUGGESTED_EVENTS_FAILURE_COOLDOWN_MS = Number(
  import.meta.env.VITE_API_SUGGESTED_FAILURE_COOLDOWN_MS || 5 * 60 * 1000,
);
const SEARCH_CACHE_TTL_MS = Number(import.meta.env.VITE_API_SEARCH_CACHE_TTL_MS || 12000);
const LONG_CONTEXT_CACHE_TTL_MS = Number(import.meta.env.VITE_API_LONG_CONTEXT_CACHE_TTL_MS || 120000);
const ENABLE_REQUEST_LOGS =
  String(import.meta.env.VITE_API_DEBUG || "").toLowerCase() === "true" || import.meta.env.DEV;
const ADMIN_SESSION_HEADER = String(import.meta.env.VITE_ADMIN_SESSION_HEADER || "X-Admin-Session").trim() || "X-Admin-Session";
const ADMIN_SESSION_TOKEN_STORAGE = "scouting_admin_session_token_v1";
const ADMIN_SESSION_EXPIRES_AT_STORAGE = "scouting_admin_session_expires_at_v1";
const ADMIN_MODE_ENABLED_STORAGE = "scouting_admin_mode_enabled";
const ROOM_ACCESS_TOKEN_STORAGE_PREFIX = "scouting_room_access_token_v1:";
const ROOM_ACCESS_EXPIRES_AT_STORAGE_PREFIX = "scouting_room_access_token_expires_v1:";
const ROOM_ACCESS_HEADER = "X-Room-Access-Token";

function readStorageString(key: string): string {
  if (typeof window === "undefined") return "";
  try {
    return String(window.localStorage.getItem(key) || "").trim();
  } catch {
    return "";
  }
}

function writeStorageString(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    // ignore storage write failures
  }
}

const nativeSessionValues = new Map<string, string>();
function readSessionString(key: string): string {
  if (isNativeApp()) return nativeSessionValues.get(key) ?? "";
  if (typeof window === "undefined") return "";
  try {
    return String(window.sessionStorage.getItem(key) || "").trim();
  } catch {
    return "";
  }
}

function writeSessionString(key: string, value: string): void {
  if (isNativeApp()) {
    if (value) nativeSessionValues.set(key, value);
    else nativeSessionValues.delete(key);
    return;
  }
  if (typeof window === "undefined") return;
  try {
    if (value) window.sessionStorage.setItem(key, value);
    else window.sessionStorage.removeItem(key);
  } catch {
    // ignore storage write failures
  }
}

function clearExpiredAdminSessionToken(): void {
  const token = readSessionString(ADMIN_SESSION_TOKEN_STORAGE);
  if (!token) return;
  const expiresAtUnix = Number(readSessionString(ADMIN_SESSION_EXPIRES_AT_STORAGE) || 0);
  if (Number.isFinite(expiresAtUnix) && expiresAtUnix > 0 && Date.now() >= expiresAtUnix * 1000) {
    writeSessionString(ADMIN_SESSION_TOKEN_STORAGE, "");
    writeSessionString(ADMIN_SESSION_EXPIRES_AT_STORAGE, "");
  }
}

function runtimeAdminModeEnabled(): boolean {
  const stored = readStorageString(ADMIN_MODE_ENABLED_STORAGE);
  if (stored === "true") return true;
  if (stored === "false") return false;
  return false;
}

function effectiveAdminSessionToken(): string {
  if (!runtimeAdminModeEnabled()) return "";
  clearExpiredAdminSessionToken();
  return readSessionString(ADMIN_SESSION_TOKEN_STORAGE);
}

function normalizeRoomKeyForStorage(roomKey: string): string {
  return String(roomKey || "").trim().toLowerCase();
}

function roomAccessStorageTokenKey(roomKey: string): string {
  return `${ROOM_ACCESS_TOKEN_STORAGE_PREFIX}${normalizeRoomKeyForStorage(roomKey)}`;
}

function roomAccessStorageExpiresKey(roomKey: string): string {
  return `${ROOM_ACCESS_EXPIRES_AT_STORAGE_PREFIX}${normalizeRoomKeyForStorage(roomKey)}`;
}

function clearExpiredRoomAccessToken(roomKey: string): void {
  const normalized = normalizeRoomKeyForStorage(roomKey);
  if (!normalized) return;
  const tokenKey = roomAccessStorageTokenKey(normalized);
  const expiresKey = roomAccessStorageExpiresKey(normalized);
  const token = readSessionString(tokenKey);
  if (!token) return;
  const expiresAtUnix = Number(readSessionString(expiresKey) || 0);
  if (Number.isFinite(expiresAtUnix) && expiresAtUnix > 0 && Date.now() >= expiresAtUnix * 1000) {
    writeSessionString(tokenKey, "");
    writeSessionString(expiresKey, "");
  }
}

export function getStoredRoomAccessToken(roomKey: string): string {
  const normalized = normalizeRoomKeyForStorage(roomKey);
  if (!normalized) return "";
  clearExpiredRoomAccessToken(normalized);
  return readSessionString(roomAccessStorageTokenKey(normalized));
}

export function setStoredRoomAccessToken(roomKey: string, token: string, expiresAtUnix?: number): void {
  const normalized = normalizeRoomKeyForStorage(roomKey);
  if (!normalized) return;
  writeSessionString(roomAccessStorageTokenKey(normalized), String(token || "").trim());
  if (typeof expiresAtUnix === "number" && Number.isFinite(expiresAtUnix) && expiresAtUnix > 0) {
    writeSessionString(roomAccessStorageExpiresKey(normalized), String(Math.floor(expiresAtUnix)));
  } else {
    writeSessionString(roomAccessStorageExpiresKey(normalized), "");
  }
}

export function clearStoredRoomAccessToken(roomKey: string): void {
  const normalized = normalizeRoomKeyForStorage(roomKey);
  if (!normalized) return;
  writeSessionString(roomAccessStorageTokenKey(normalized), "");
  writeSessionString(roomAccessStorageExpiresKey(normalized), "");
}

export function getStoredClientAdminSessionToken(): string {
  return effectiveAdminSessionToken();
}

export function getStoredClientAdminSessionExpiresAtUnix(): number | null {
  clearExpiredAdminSessionToken();
  const value = Number(readSessionString(ADMIN_SESSION_EXPIRES_AT_STORAGE) || 0);
  if (!Number.isFinite(value) || value <= 0) return null;
  return Math.floor(value);
}

export function setStoredClientAdminSessionToken(sessionToken: string, expiresAtUnix?: number): void {
  writeSessionString(ADMIN_SESSION_TOKEN_STORAGE, String(sessionToken || "").trim());
  if (typeof expiresAtUnix === "number" && Number.isFinite(expiresAtUnix) && expiresAtUnix > 0) {
    writeSessionString(ADMIN_SESSION_EXPIRES_AT_STORAGE, String(Math.floor(expiresAtUnix)));
  } else {
    writeSessionString(ADMIN_SESSION_EXPIRES_AT_STORAGE, "");
  }
}

export function clearStoredClientAdminSessionToken(): void {
  writeSessionString(ADMIN_SESSION_TOKEN_STORAGE, "");
  writeSessionString(ADMIN_SESSION_EXPIRES_AT_STORAGE, "");
}

export function isClientAdminModeEnabled(): boolean {
  return runtimeAdminModeEnabled();
}

export function setClientAdminModeEnabled(enabled: boolean): void {
  writeStorageString(ADMIN_MODE_ENABLED_STORAGE, enabled ? "true" : "false");
  if (!enabled) {
    clearStoredClientAdminSessionToken();
  }
}

export function clientAdminKeyAvailable(): boolean {
  return effectiveAdminSessionToken().length > 0;
}

type ApiFetchInit = RequestInit & {
  cacheTtlMs?: number;
  staleWhileRevalidateMs?: number;
  bypassCache?: boolean;
  timeoutMs?: number;
  // Never answer with (or save) a stored copy. For reads whose caller keeps
  // its own offline copy, where an old answer could undo a newer one.
  liveOnly?: boolean;
  // With no fresh copy, answer at once with a saved one up to this old and refresh
  // in the background; pages re-render when the refresh brings something new
  // ("api:updated", see useSingleFlightPolling). 0 = wait for the network.
  showSavedMaxAgeMs?: number;
};

type ApiRequestOptions = Pick<
  ApiFetchInit,
  "cacheTtlMs" | "staleWhileRevalidateMs" | "bypassCache" | "timeoutMs" | "showSavedMaxAgeMs"
>;

type CachedResponse = {
  storedAtMs: number;
  expiresAtMs: number;
  response: Response;
};

type PersistentCachedResponse = {
  storedAtMs: number;
  // Fresh until expiresAtMs (the endpoint's own TTL). Kept until retainUntilMs
  // only as a last resort when there is no network (offlineStaleFallback).
  expiresAtMs: number;
  retainUntilMs?: number;
  status: number;
  statusText: string;
  headers: Array<[string, string]>;
  bodyText: string;
};

type CacheLookupResult = {
  state: "fresh" | "stale";
  response: Response;
  ageSec: number;
};

const responseCache = new Map<string, CachedResponse>();
const MAX_RESPONSE_CACHE_ENTRIES = 200;
function cacheResponse(requestKey: string, cached: CachedResponse): void {
  const now = Date.now();
  for (const [key, value] of responseCache) {
    if (value.expiresAtMs <= now) responseCache.delete(key);
  }
  responseCache.delete(requestKey);
  responseCache.set(requestKey, cached);
  while (responseCache.size > MAX_RESPONSE_CACHE_ENTRIES) {
    responseCache.delete(responseCache.keys().next().value!);
  }
}
const inflightGetRequests = new Map<string, Promise<Response>>();
const PERSIST_CACHE_PREFIX = API_CACHE_PREFIX;
let suggestedEventsCircuitOpenUntilMs = 0;

export type MetricAverages = {
  fuel_scoring_rate: number | null;
  cycle_time_sec: number | null;
  auto_contribution: number | null;
  climb_success_prob: number | null;
  defensive_engagement_sec: number | null;
  reliability_score: number | null;
};

type MetricCoverageInfo = {
  observed_matches: number;
  total_matches: number;
  coverage_0_1: number;
  missing_reason: string | null;
  estimated_from_model?: boolean;
  estimate_source?: string;
  official_record?: boolean;
  official_matches?: number;
  official_climbs?: number;
};

// Per-robot numbers from official results, for teams nobody has scouted or
// filmed: fuel and auto are TBA's component OPRs, climb is the team's record.
export type OfficialTeamStats = {
  available: boolean;
  matches: number;
  copr_matches: number;
  fuel_per_match: number | null;
  fuel_per_active_minute: number | null;
  auto_points_per_match: number | null;
  events: Array<{ event_key: string; matches: number; copr: boolean }>;
  climb: { matches: number; climbs: number; rate: number | null } | null;
};

type ClimbSourceSummary = {
  source: string;
  climb_success_prob: number | null;
  matches_considered: number;
  matches_with_signal: number;
};

type ClimbCapabilityLevel = "level1" | "level2" | "level3";

export type ClimbLevelCapability = {
  best_level: ClimbCapabilityLevel | null;
  best_level_label: string;
  best_level_score_0_100: number | null;
  level_counts: Record<ClimbCapabilityLevel, number>;
  matches_with_level: number;
  matches_with_success_no_level: number;
  matches_considered: number;
};

type SeasonScope = {
  season_year: number;
  active_season_year: number;
  source: string;
  uses_active_season: boolean;
};

type TeamHistoryFinding = {
  match_key: string;
  event_key: string;
  match_time: number | null;
  alliance: string;
  station: string | null;
  fuel_scoring_rate: number | null;
  cycle_time_sec: number | null;
  auto_contribution: number | null;
  climb_success_prob: number | null;
  defensive_engagement_sec: number | null;
  reliability_score: number | null;
  summary: Record<string, unknown>;
};

export type EventSearchItem = {
  event_key: string;
  name: string;
  year: number;
  start_date?: string | null;
  end_date?: string | null;
  city?: string | null;
  state_prov?: string | null;
  country?: string | null;
  region?: string;
};

export type EventSearchResponse = {
  ok: boolean;
  query: string;
  count: number;
  events: EventSearchItem[];
};

export type SuggestedEventsResponse = {
  ok: boolean;
  preferred_year: number;
  fallback_year: number;
  selected_year: number | null;
  source: string;
  count: number;
  events: EventSearchItem[];
};

type TeamSearchItem = {
  team_key: string;
  team_number: number;
  nickname: string | null;
  analyzed_matches: number;
  source?: "local" | "global";
  country?: string | null;
  state?: string | null;
  registration_year?: number | null;
  registered_events_count?: number;
  registered_events_source?: string;
  registered_events?: Array<{
    event_key: string;
    name: string;
    year: number;
    start_date?: string | null;
    end_date?: string | null;
    city?: string | null;
    state_prov?: string | null;
    country?: string | null;
  }>;
};

export type TeamSearchResponse = {
  ok: boolean;
  query: string;
  count: number;
  teams: TeamSearchItem[];
};

export type TeamCompetitionsResponse = {
  ok: boolean;
  team_key: string;
  event_key: string | null;
  registration_year: number | null;
  registered_events_count: number;
  registered_events_source: string;
  registered_events: Array<{
    event_key: string;
    name: string;
    year: number;
    start_date?: string | null;
    end_date?: string | null;
    city?: string | null;
    state_prov?: string | null;
    country?: string | null;
  }>;
};

export type TeamRobotImageResponse = {
  ok: boolean;
  team_key: string;
  event_key: string | null;
  available: boolean;
  year: number | null;
  image_url: string | null;
  media_type: string | null;
  view_url: string | null;
  source: string;
  reason: string | null;
};

export type TeamLogoResponse = {
  ok: boolean;
  team_key: string;
  event_key: string | null;
  available: boolean;
  year: number | null;
  image_url: string | null;
  media_type: string | null;
  view_url: string | null;
  source: string;
  reason: string | null;
};

type EventTeamsHistoryTeam = {
  team_key: string;
  team_number: number;
  nickname: string | null;
  region: string;
  state_prov: string | null;
  country: string | null;
  history_count: number;
  averages: MetricAverages | null;
  previous_games: TeamHistoryFinding[];
};

export type EventTeamsHistoryResponse = {
  ok: boolean;
  event_key: string;
  event_name: string | null;
  season_scope?: SeasonScope;
  teams_count: number;
  teams: EventTeamsHistoryTeam[];
};

export type TeamBreakdownResponse = {
  ok: boolean;
  team: {
    team_key: string;
    team_number: number;
    nickname: string | null;
  };
  event_key: string | null;
  season_scope?: SeasonScope;
  /* Official matches in the breakdown (Data Dashboard). */
  matches_analyzed: number;
  averages: MetricAverages | null;
  climb_sources?: {
    overall_climb_success_prob: number | null;
    official_score_breakdown: ClimbSourceSummary;
    level_capability?: ClimbLevelCapability;
  };
  metric_coverage?: Record<string, MetricCoverageInfo>;
  official_stats?: OfficialTeamStats | null;
  event_type_counts: Array<{ event_type: string; count: number }>;
  recent_matches: Array<
    TeamHistoryFinding & {
      analysis_run_id: number;
      source_version: string;
    }
  >;
  recent_track_points: Array<{
    id: number;
    analysis_run_id: number;
    match_key: string;
    track_id: number;
    frame_index: number;
    time_sec: number;
    field_x: number | null;
    field_y: number | null;
    speed_mps: number | null;
    zone_key: string | null;
    bbox: [number, number, number, number];
  }>;
};

type RatingSignalEvidence = {
  match_key: string;
  metric: string;
  value: number;
};

type RatingSignal = {
  label: string;
  /* One plain sentence with the numbers behind it (rating_v15). */
  rationale?: string;
  metric_value: number;
  /* Only on older (rating_v14) signals. */
  percentile?: number;
  evidence: RatingSignalEvidence[];
};

export type RatingChange = { match_key: string; points_change: number; rating_change: number };
export type RatingExpectedPoints = { total: number; teleop: number; auto: number; climb: number };
export type RatingBasis = 'this_event' | 'earlier_events' | 'last_season' | 'rookie' | 'season_typical';

/* Where an event's season stands (backend services/seasons.py). */
export type SeasonInfo = { season: number | null; game: string | null; current: boolean; rated: boolean; frozen: boolean };

export type EventTeamRatingItem = {
  event_key: string;
  team_key: string;
  team_number: number | null;
  nickname: string | null;
  rating_0_100: number;
  confidence_0_1: number;
  /* Scoring rating: teleop + auto on the same scale as the overall rating. */
  robot_level_0_100: number;
  /* Null since rating_v15: official results can't separate driving from the robot. */
  driver_skill_0_100: number | null;
  /* On the rating's own scale (the season's typical robot = 50). Null when official
     results can't measure it (results_anchor, shift_productivity, capacity_utilization,
     manual_points_impact, rp_contribution, defense_presence, penalty_discipline), or
     consistency before a team has 3 matches at the event. */
  subscores: {
    results_anchor: number | null;
    throughput: number | null;
    shift_productivity: number | null;
    capacity_utilization: number | null;
    endgame: number | null;
    auto_contribution?: number | null;
    manual_points_impact?: number | null;
    rp_contribution?: number | null;
    defense_presence?: number | null;
    consistency: number | null;
    penalty_discipline?: number | null;
  };
  /* What the rating stands for: points the team adds to its alliance per match. */
  expected_points?: RatingExpectedPoints | null;
  /* this_event: rated from matches here; earlier_events: hasn't played here yet;
     season_typical: no official matches this season. */
  basis?: RatingBasis | null;
  matches_here?: number | null;
  matches_before?: number | null;
  /* Where the rating likely is (80%): [low, high]. Narrow with many matches. */
  rating_range_80?: [number, number] | null;
  /* Share of matches this robot is expected to score ~nothing (dead, tipped, disconnected). */
  dud_rate?: number | null;
  /* The last matches here that moved the rating, newest first. */
  recent_changes?: RatingChange[];
  pros: RatingSignal[];
  cons: RatingSignal[];
  evidence: RatingSignalEvidence[];
  details: Record<string, unknown>;
  model_version: string;
  updated_at: string | null;
  rating_trend?: RatingTrend | null;
  /* Where this rating sits in its own event's field. `rank_event_key` is the
     event the rating came from, which is not always the event that was asked
     for — a team with no rating at the selected event falls back to their most
     recent one, and ranking that against a field they were never in would be a
     made-up baseline. Show the event when it differs. */
  rank?: number | null;
  field_size?: number | null;
  rank_event_key?: string | null;
};

export type RatingTrend = {
  latest_0_100: number;
  previous_0_100: number;
  delta: number;
  direction: "up" | "down" | "flat";
  snapshot_count: number;
  sparkline: number[];
  min_0_100: number;
  max_0_100: number;
};

export type EventRatingsResponse = {
  ok: boolean;
  event_key: string;
  event_name: string | null;
  model_version: string;
  source: string;
  count: number;
  ratings: EventTeamRatingItem[];
  last_updated_at?: string | null;
};

export type EventScheduleTeam = {
  team_key: string;
  team_number: number;
  nickname: string | null;
  station: string | null;
};

export type EventScheduleItem = {
  match_key: string;
  display_name: string;
  comp_level: string;
  set_number: number;
  match_number: number;
  scheduled_time: number | null;
  // TBA's predicted start (tracks the field running late) and actual start.
  predicted_time?: number | null;
  actual_time?: number | null;
  has_time: boolean;
  red_score?: number | null;
  blue_score?: number | null;
  winner_alliance?: "red" | "blue" | "tie" | null;
  is_completed?: boolean;
  winning_score?: number | null;
  losing_score?: number | null;
  red: EventScheduleTeam[];
  blue: EventScheduleTeam[];
};

export type EventScheduleResponse = {
  ok: boolean;
  event_key: string;
  event_name: string | null;
  source: string;
  first_last_modified?: string | null;
  published: boolean;
  times_published: boolean;
  count: number;
  total_count?: number;
  limit?: number | null;
  offset?: number;
  matches: EventScheduleItem[];
};

type EventLiveStreamItem = {
  type: string;
  channel: string | null;
  watch_url: string | null;
  embed_url: string | null;
  supports_embed: boolean;
};

export type EventLiveStreamResponse = {
  ok: boolean;
  event_key: string;
  event_name: string | null;
  source: string;
  available: boolean;
  preferred_stream: EventLiveStreamItem | null;
  streams: EventLiveStreamItem[];
  game_day_url: string;
  detail: string | null;
};

export type EventTeamLiveFormEntry = {
  team_key: string;
  is_live: boolean;
  live_match_key: string | null;
  live_match_display: string | null;
  live_match_start_unix: number | null;
  recent_form: Array<"W" | "L" | "T">;
  recent_form_matches: Array<{
    match_key: string;
    match_display: string | null;
    time: number | null;
    result: "W" | "L" | "T";
  }>;
  recent_form_winrate_0_1: number | null;
  in_form_label: "in_form" | "mixed" | "cold" | "insufficient_data";
  completed_matches_considered: number;
};

export type EventTeamLiveFormResponse = {
  ok: boolean;
  event_key: string;
  source: string;
  available: boolean;
  detail: string | null;
  as_of_unix: number;
  form_window: number;
  live_window_sec: number;
  team_statuses: Record<string, EventTeamLiveFormEntry>;
};

type MatchPredictionPayload = {
  available: boolean;
  source_label: string;
  red_win_prob: number | null;
  blue_win_prob: number | null;
  favored_alliance: 'red' | 'blue' | null;
  edge_confidence_0_1: number | null;
  /* Quals: each alliance's chance of each bonus RP, and its expected RP (3 for a win plus bonuses). */
  bonus_rp_chances?: {
    red: Record<string, number>;
    blue: Record<string, number>;
    red_expected_rp: number;
    blue_expected_rp: number;
  } | null;
};

export type PredictedScheduleMatch = {
  match_key: string;
  comp_level: string;
  set_number: number;
  match_number: number;
  scheduled_time: number | null;
  red: { teams: EventScheduleTeam[] };
  blue: { teams: EventScheduleTeam[] };
  prediction?: MatchPredictionPayload;
};

export type EventPredictionScheduleResponse = {
  ok: boolean;
  event_key: string;
  event_name: string | null;
  season?: SeasonInfo | null;
  count: number;
  matches: PredictedScheduleMatch[];
};

// ── Heatmap types ────────────────────────────────────────────────

export type TeamHeatmapResponse = {
  ok: boolean;
  team_key: string;
  event_key: string;
  match_key: string | null;
  total_points: number;
  match_count: number;
  // Matches covered only by your team's own recordings, which nobody has reviewed
  // and only your team sees (asked for with include_workspace).
  own_unreviewed_matches?: number;
  field_length_m: number;
  field_width_m: number;
  grid_cols: number;
  grid_rows: number;
  sigma: number;
  grid: number[][];
};

type EventAllianceTeam = {
  teamNumber: number;
  pick?: number;
  backup?: boolean;
  in?: number;
  out?: number;
  name?: string;
};

export type EventAllianceItem = {
  number?: number;
  name?: string;
  captain?: EventAllianceTeam;
  round1?: EventAllianceTeam;
  round2?: EventAllianceTeam;
  round3?: EventAllianceTeam;
  backup?: EventAllianceTeam;
} & Record<string, unknown>;

export type EventAlliancesResponse = {
  ok: boolean;
  event_key: string;
  season: number;
  event_code: string;
  source: string;
  not_modified: boolean;
  last_modified: string | null;
  count: number;
  alliances: EventAllianceItem[];
};

export type EventRankingsResponse = {
  ok: boolean;
  event_key: string;
  source: string;
  rankings: Record<string, unknown> | null;
};

export type EventPredictionsResponse = {
  ok: boolean;
  event_key: string;
  source: string;
  predictions: Record<string, unknown> | null;
};

export type EventAwardsResponse = {
  ok: boolean;
  event_key: string;
  source: string;
  count: number;
  awards: Array<Record<string, unknown>>;
};

export type TeamIntelResponse = {
  ok: boolean;
  team_key: string;
  event_key: string | null;
  generated_at: string;
  cache?: {
    key: string;
    hit: boolean;
    age_sec: number;
    ttl_sec: number;
  };
  team: {
    team_key: string;
    team_number: number;
    nickname: string | null;
  };
  competitions: Record<string, unknown>;
  analysis: Record<string, unknown>;
  rating: Record<string, unknown>;
  auto_scout_profile?: AutoScoutProfile;
  tba: Record<string, unknown>;
  fallbacks: Array<Record<string, unknown>>;
  warnings: string[];
};

// Per-team rollup of the video-derived auto-scout fields (Team Center "robot info").
export type AutoScoutProfileField =
  | {
      type: 'rate';
      samples: number;
      true_rate_0_1: number | null;
      avg_confidence_0_1: number | null;
      last: boolean;
    }
  | {
      type: 'numeric';
      samples: number;
      median: number | null;
      mean: number | null;
      min: number | null;
      max: number | null;
      last: number | null;
      trend: number[];
      avg_confidence_0_1: number | null;
    }
  | {
      type: 'categorical';
      samples: number;
      mode: string;
      distribution: Record<string, number>;
      last: string;
      avg_confidence_0_1: number | null;
    };

export type AutoScoutProfile = {
  available: boolean;
  team_key: string;
  scope: { event_key: string | null; season_year: number | null };
  is_last_season: boolean;
  sample_matches: number;
  fields: Record<string, AutoScoutProfileField>;
  generated_at?: string;
};

export type EventTeamsIntelResponse = {
  ok: boolean;
  event_key: string;
  event_name: string | null;
  season_year: number;
  source: string;
  generated_at: string;
  cache?: {
    key: string;
    hit: boolean;
    age_sec: number;
    ttl_sec: number;
  };
  teams_count: number;
  teams_with_event_analysis: number;
  teams_with_fallback_analysis: number;
  teams_with_event_rating: number;
  teams: Array<Record<string, unknown>>;
  warnings: string[];
};

export type TheoreticalSelectionTopTeam = {
  team_key: string;
  rank: number;
  strength_score: number;
  strength_source: string;
  selection_desirability: number;
  is_captain: boolean;
  first_round_pick_probability_0_1: number;
  second_round_pick_probability_0_1: number;
};

export type TheoreticalSelectionModel = {
  enabled: boolean;
  formula: string;
  strength_source_priority: string[];
  rank_source: string;
  rank_source_detail?: {
    requested?: string;
    tba_status?: string;
  };
  rank_weight: number;
  scale: number;
  captain_count: number;
  simulations: number;
  declines_mode: string;
  notes: string[];
  captains: string[];
  expected_alliance_board?: Record<string, string[]>;
  top_desirability: TheoreticalSelectionTopTeam[];
  first_round_seed_pick_probabilities?: Record<
    string,
    Array<{
      team_key: string;
      probability_0_1: number;
      selection_desirability: number;
    }>
  >;
  team_pick_probability_0_1?: Record<string, number>;
};

export type SelectionModelResponse = {
  ok: boolean;
  event_key: string;
  selection_model: TheoreticalSelectionModel;
};

export type AllianceOutlookTeam = {
  team_key: string;
  rating_0_100: number;
  points_per_match: number;
  climb_points: number;
  dud_rate: number;
  event_rank_by_points: number;
  strengths: string[];
  risks: string[];
};

export type AllianceOutlookOpponent = { teams: string[]; points: number; win_prob: number };

/* How good a chosen three-team alliance is, from the team model (same as pick values). */
export type AllianceOutlookResponse = {
  ok: boolean;
  event_key: string;
  teams: AllianceOutlookTeam[];
  alliance_points?: number;
  alliance_points_range_80?: [number, number];
  vs_strong?: AllianceOutlookOpponent;
  vs_typical?: AllianceOutlookOpponent;
  bonus_rp_chances?: Record<string, number>;
  team_count?: number;
  /* "season_frozen" / "season_not_rated" / "too_few_teams". */
  unavailable?: string;
};

export type ApiHealthResponse = {
  ok: boolean;
  public_readonly_mode?: boolean;
  write_auth?: {
    enforced?: boolean;
    admin_key_configured?: boolean;
    header?: string;
    session_header?: string;
  };
};

export type AdminSessionCreateResponse = {
  ok: boolean;
  authorized: boolean;
  status: number;
  detail?: string;
  session_token?: string;
  expires_at?: string;
  expires_at_unix?: number;
  ttl_sec?: number;
  header?: string;
};

export type OpsDashboardResponse = {
  ok: boolean;
  generated_at: string;
  sample_days: number;
  alert_count: number;
  alerts: Array<{
    level: string;
    code: string;
    message: string;
    value?: number;
    threshold?: number;
  }>;
  automation: {
    ok: boolean;
    season: number;
    enabled?: boolean;
    locked?: boolean;
    due_now?: boolean;
    last_run_at?: string | null;
    next_run_earliest_at?: string | null;
    detail?: string;
  };
  media_usage: {
    total_gb?: number;
    disk_free_gb?: number;
  };
  climb_signal_coverage?: {
    ok?: boolean;
    summary?: {
      events: number;
      teams_total: number;
      teams_with_any_signal: number;
      teams_with_official_signal: number;
      teams_with_video_signal: number;
      coverage_any_pct: number;
      coverage_official_pct: number;
      coverage_video_pct: number;
    };
    events?: Array<{
      event_key: string;
      teams_total: number;
      teams_with_any_signal: number;
      teams_with_official_signal: number;
      teams_with_video_signal: number;
      coverage_any_pct: number;
      coverage_official_pct: number;
      coverage_video_pct: number;
    }>;
  };
  metrics: Record<string, unknown>;
};

// What a person sees when a request fails. Server "detail" strings are written for people
// (e.g. "That scout already has this match"), so they pass through; raw bodies, HTML error
// pages and stack traces don't.
function friendlyStatusMessage(status: number): string {
  if (status === 401 || status === 403) return "You don't have access to this. If you're on a team, rejoin its workspace.";
  if (status === 404) return "That wasn't found. It may not be published yet.";
  if (status === 408 || status === 504) return "The server took too long to answer. Try again.";
  if (status === 429) return "Too many requests at once. Wait a moment and try again.";
  if (status >= 500) return "The server had a problem. Try again in a minute.";
  return "That didn't work. Try again.";
}

// Only real stack traces and markup. Server "detail" text is written for people, including
// long recovery instructions, so it isn't judged by length or by ordinary words.
function looksTechnical(text: string): boolean {
  return /Traceback \(most recent call last\)|File "[^"]+", line \d+|\n\s+at \S+ \(|<!doctype|<html|<body|\b(sqlalchemy|psycopg)\.\w+/i.test(text);
}

async function readError(response: Response): Promise<string> {
  const fallback = friendlyStatusMessage(response.status);
  if (response.bodyUsed) return fallback;
  let bodyText: string;
  try {
    // Read from a clone to avoid consuming the original response stream.
    bodyText = await response.clone().text();
  } catch {
    try {
      bodyText = await response.text();
    } catch {
      return fallback;
    }
  }

  const trimmed = String(bodyText || "").trim();
  if (!trimmed) return fallback;

  try {
    const data = JSON.parse(trimmed) as { detail?: unknown; message?: unknown };
    const detail = typeof data.detail === "string" ? data.detail.trim() : typeof data.message === "string" ? data.message.trim() : "";
    if (detail && !looksTechnical(detail)) return detail;
  } catch {
    // Plain-text and HTML bodies (proxy error pages, stack traces) aren't shown to people.
  }
  return fallback;
}

function absoluteApiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${API}${path}`;
}

function normalizeMethod(method: string | undefined): string {
  return (method || "GET").toUpperCase();
}

// Pull-to-refresh opens a short window in which reads skip the in-memory and saved copies, so
// "refresh" means the network. Offline it isn't opened: the saved copies are all there is.
let freshReadsUntilMs = 0;

export function readFreshFor(ms: number): () => void {
  const until = Date.now() + Math.max(0, ms);
  freshReadsUntilMs = until;
  return () => {
    if (freshReadsUntilMs === until) freshReadsUntilMs = 0;
  };
}

function shouldBypassCache(url: string, init?: ApiFetchInit): boolean {
  if (init?.bypassCache) return true;
  const method = normalizeMethod(init?.method);
  if (method !== "GET") return true;
  if (Date.now() < freshReadsUntilMs) return true;
  if (url.includes("refresh=true")) return true;
  if (url.includes("onlyModifiedSince=")) return true;
  // An event's results just changed (live results socket): read past cached copies.
  if (liveFreshRead(url)) return true;
  return false;
}

function cacheKeyFor(url: string, init?: ApiFetchInit): string {
  // Keyed by workspace too: picklists and pit notes share URLs across teams, so
  // a URL-only key would show one team's cached data after switching to another.
  const workspaceId = getWorkspaceSession()?.workspace.id ?? 0;
  // Without the live results version: saved copies stay under the plain URL (offline).
  return `${normalizeMethod(init?.method)}:w${workspaceId}:${stripLiveVersion(url)}`;
}

function _cacheValueInt(value: number | undefined, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return Math.max(0, Math.floor(fallback));
  return Math.max(0, Math.floor(value));
}

function _cacheRequestOptions(
  base: ApiRequestOptions | undefined,
  defaults: ApiRequestOptions,
): ApiFetchInit {
  return {
    cacheTtlMs:
      typeof base?.cacheTtlMs === "number"
        ? Math.max(0, Math.floor(base.cacheTtlMs))
        : Math.max(0, Math.floor(defaults.cacheTtlMs ?? DEFAULT_GET_CACHE_TTL_MS)),
    staleWhileRevalidateMs:
      typeof base?.staleWhileRevalidateMs === "number"
        ? Math.max(0, Math.floor(base.staleWhileRevalidateMs))
        : Math.max(0, Math.floor(defaults.staleWhileRevalidateMs ?? DEFAULT_GET_STALE_WHILE_REVALIDATE_MS)),
    bypassCache: Boolean(base?.bypassCache ?? defaults.bypassCache ?? false),
    timeoutMs:
      typeof base?.timeoutMs === "number"
        ? Math.max(2000, Math.floor(base.timeoutMs))
        : defaults.timeoutMs,
    showSavedMaxAgeMs: Math.max(0, Math.floor(base?.showSavedMaxAgeMs ?? defaults.showSavedMaxAgeMs ?? 0)),
  };
}

type TimeoutState = {
  didTimeout: boolean;
  clear?: () => void;
};

// Writes a replay can't duplicate: room entries carry client_entry_id, pit
// entries are upserts, and picklist updates carry a version (a stale replay gets
// a conflict back, never a second copy).
const REPLAY_SAFE_WRITES: Array<[string, RegExp]> = [
  ["POST", /\/scouting\/rooms\/[^/?]+\/entries(?:\?|$)/],
  ["POST", /\/pit-scouting(?:\?|$)/],
  ["PUT", /\/picklists\/\d+(?:\?|$)/],
];

export function isReplaySafeWrite(method: string, url: string): boolean {
  let path = url;
  try {
    path = new URL(url, "http://local").pathname;
  } catch {
    // keep the raw string
  }
  return REPLAY_SAFE_WRITES.some(([m, pattern]) => m === method && pattern.test(path));
}

function isConnectionFailure(error: unknown): boolean {
  if (error instanceof TypeError) return true;
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /timed out|failed to fetch|networkerror|load failed|network connection/i.test(message);
}

function timeoutErrorMessage(timeoutMs: number): string {
  const seconds = Math.max(1, Math.round(timeoutMs / 1000));
  return `Request timed out after ${seconds}s.`;
}

function isAbortLikeMessage(message: string): boolean {
  const normalized = String(message || "").trim().toLowerCase();
  if (!normalized) return false;
  return (
    normalized.includes("aborted") ||
    normalized.includes("aborterror") ||
    normalized.includes("signal is aborted") ||
    normalized.includes("request was aborted") ||
    normalized.includes("the user aborted a request") ||
    normalized.includes("without reason")
  );
}

function normalizeApiFetchError(
  error: unknown,
  timeoutMs: number,
  timeoutState: TimeoutState,
  requestSignal: AbortSignal,
): Error {
  if (timeoutState.didTimeout) {
    return new Error(timeoutErrorMessage(timeoutMs));
  }

  const signalReason = requestSignal.aborted ? requestSignal.reason : undefined;
  if (signalReason instanceof Error && signalReason.message) {
    return signalReason;
  }
  if (typeof signalReason === "string" && signalReason.trim()) {
    return new Error(signalReason.trim());
  }

  if (error instanceof Error) {
    if (isAbortLikeMessage(error.message)) {
      return new Error("Request was aborted.");
    }
    return error;
  }

  const message = String(error || "").trim();
  if (isAbortLikeMessage(message)) {
    return new Error("Request was aborted.");
  }
  return new Error(message || "Request failed.");
}

function withTimeout(
  signal: AbortSignal | null | undefined,
  timeoutMs: number,
  timeoutState?: TimeoutState,
): AbortSignal {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    return signal || new AbortController().signal;
  }
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => {
    if (timeoutState) timeoutState.didTimeout = true;
    controller.abort(new Error(timeoutErrorMessage(timeoutMs)));
  }, Math.floor(timeoutMs));
  if (signal) {
    if (signal.aborted) {
      controller.abort(signal.reason ?? new Error("Request was aborted."));
    }
    else {
      signal.addEventListener(
        "abort",
        () => {
          controller.abort(signal.reason ?? new Error("Request was aborted."));
        },
        { once: true },
      );
    }
  }
  controller.signal.addEventListener(
    "abort",
    () => {
      globalThis.clearTimeout(timeout);
    },
    { once: true },
  );
  if (timeoutState) timeoutState.clear = () => globalThis.clearTimeout(timeout);
  return controller.signal;
}

function persistentCacheKey(requestKey: string): string {
  return `${PERSIST_CACHE_PREFIX}${requestKey}`;
}

function localStorageAvailable(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const probe = "__scouting_cache_probe__";
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}

let lastPersistentPruneAtMs = 0;

function prunePersistentCache(maxEntries: number) {
  if (!localStorageAvailable() || maxEntries <= 0) return;
  // A full scan parses every cached body; once a minute is plenty.
  if (Date.now() - lastPersistentPruneAtMs < 60_000) return;
  lastPersistentPruneAtMs = Date.now();
  const rows: Array<{ key: string; storedAtMs: number; expiresAtMs: number }> = [];
  try {
    for (let idx = 0; idx < window.localStorage.length; idx += 1) {
      const key = window.localStorage.key(idx);
      if (!key || !key.startsWith(PERSIST_CACHE_PREFIX)) continue;
      const raw = window.localStorage.getItem(key);
      if (!raw) continue;
      try {
        const parsed = JSON.parse(raw) as Partial<PersistentCachedResponse>;
        const storedAtMs = Number(parsed.storedAtMs || 0);
        const expiresAtMs = Number(parsed.retainUntilMs || parsed.expiresAtMs || 0);
        if (!Number.isFinite(expiresAtMs) || expiresAtMs <= Date.now()) {
          window.localStorage.removeItem(key);
          continue;
        }
        rows.push({
          key,
          storedAtMs: Number.isFinite(storedAtMs) ? storedAtMs : 0,
          expiresAtMs,
        });
      } catch {
        window.localStorage.removeItem(key);
      }
    }
    if (rows.length <= maxEntries) return;
    rows.sort((a, b) => a.storedAtMs - b.storedAtMs);
    const toRemove = rows.length - maxEntries;
    for (let i = 0; i < toRemove; i += 1) {
      window.localStorage.removeItem(rows[i].key);
    }
  } catch {
    // ignore local storage pruning errors
  }
}

function readMemoryGetCache(
  requestKey: string,
  nowMs: number,
  staleWhileRevalidateMs: number,
): CacheLookupResult | null {
  const cached = responseCache.get(requestKey);
  if (!cached) return null;
  const withinFreshWindow = cached.expiresAtMs > nowMs;
  const withinStaleWindow =
    staleWhileRevalidateMs > 0 && nowMs <= cached.expiresAtMs + staleWhileRevalidateMs;
  if (!withinFreshWindow && !withinStaleWindow) {
    responseCache.delete(requestKey);
    return null;
  }
  const ageSec = Math.max(0, Math.floor((nowMs - cached.storedAtMs) / 1000));
  return {
    state: withinFreshWindow ? "fresh" : "stale",
    response: cached.response.clone(),
    ageSec,
  };
}

function readPersistentGetCache(
  requestKey: string,
  nowMs: number,
  staleWhileRevalidateMs: number,
): CacheLookupResult | null {
  if (!localStorageAvailable()) return null;
  const storageKey = persistentCacheKey(requestKey);
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistentCachedResponse>;
    const expiresAtMs = Number(parsed.expiresAtMs || 0);
    const storedAtMs = Number(parsed.storedAtMs || 0);
    const withinFreshWindow = Number.isFinite(expiresAtMs) && expiresAtMs > nowMs;
    const withinStaleWindow =
      Number.isFinite(expiresAtMs) &&
      staleWhileRevalidateMs > 0 &&
      nowMs <= expiresAtMs + staleWhileRevalidateMs;
    if (!withinFreshWindow && !withinStaleWindow) {
      const retainUntilMs = Number(parsed.retainUntilMs || 0);
      if (!Number.isFinite(retainUntilMs) || retainUntilMs <= nowMs) window.localStorage.removeItem(storageKey);
      return null;
    }
    const status = Number(parsed.status || 200);
    const statusText = typeof parsed.statusText === "string" ? parsed.statusText : "OK";
    const headers = Array.isArray(parsed.headers) ? parsed.headers : [];
    const bodyText = typeof parsed.bodyText === "string" ? parsed.bodyText : "";
    const response = new Response(bodyText, {
      status,
      statusText,
      headers,
    });
    const ageSec = Math.max(0, Math.floor((nowMs - (Number.isFinite(storedAtMs) ? storedAtMs : nowMs)) / 1000));
    return {
      state: withinFreshWindow ? "fresh" : "stale",
      response,
      ageSec,
    };
  } catch {
    try {
      window.localStorage.removeItem(storageKey);
    } catch {
      // ignore
    }
    return null;
  }
}

// Last-resort lookup that ignores all TTL/expiry. Used only when there is no
// network at all (offline) so the app can keep showing the last data it saw.
// Returns whichever of the memory/persistent caches was stored most recently.
function readCacheAnyAge(requestKey: string): CacheLookupResult | null {
  const nowMs = Date.now();
  let best: { storedAtMs: number; response: Response } | null = null;

  const mem = responseCache.get(requestKey);
  if (mem) {
    best = { storedAtMs: mem.storedAtMs, response: mem.response.clone() };
  }

  if (localStorageAvailable()) {
    const storageKey = persistentCacheKey(requestKey);
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<PersistentCachedResponse>;
        const storedAtMsRaw = Number(parsed.storedAtMs || 0);
        const persistStoredAtMs = Number.isFinite(storedAtMsRaw) ? storedAtMsRaw : 0;
        if (!best || persistStoredAtMs > best.storedAtMs) {
          const status = Number(parsed.status || 200);
          const statusText = typeof parsed.statusText === "string" ? parsed.statusText : "OK";
          const headers = Array.isArray(parsed.headers) ? parsed.headers : [];
          const bodyText = typeof parsed.bodyText === "string" ? parsed.bodyText : "";
          best = {
            storedAtMs: persistStoredAtMs,
            response: new Response(bodyText, { status, statusText, headers }),
          };
        }
      }
    } catch {
      // ignore parse/storage errors — treat as cache miss
    }
  }

  if (!best) return null;
  const ageSec = Math.max(0, Math.floor((nowMs - best.storedAtMs) / 1000));
  return { state: "stale", response: best.response, ageSec };
}

// Builds an offline-stale Response from the age-agnostic caches (memory, localStorage,
// and the large-response store) and signals the UI (via a window event) that the user
// is now looking at saved data.
// A server having a bad moment (5xx, rate limit, timeout) is no different for a reader
// than a dropped connection: show the last saved copy rather than "no matches" or
// "0 matches", which read as facts. A dropped connection already did this.
function serverTrouble(response: Response): boolean {
  return response.status >= 500 || response.status === 429 || response.status === 408;
}

async function offlineStaleFallback(requestKey: string): Promise<Response | null> {
  let hit = readCacheAnyAge(requestKey);
  const stored = await getStoredResponse(requestKey);
  if (stored && (!hit || stored.storedAtMs > Date.now() - hit.ageSec * 1000)) {
    hit = { state: "stale", response: stored.response, ageSec: Math.max(0, Math.floor((Date.now() - stored.storedAtMs) / 1000)) };
  }
  if (!hit) return null;
  const headers = new Headers(hit.response.headers);
  headers.set("X-From-Cache", "offline-stale");
  if (typeof window !== "undefined") {
    try {
      window.dispatchEvent(new CustomEvent("api:offline-stale"));
    } catch {
      // ignore — non-DOM env or CustomEvent unsupported
    }
  }
  return new Response(hit.response.body, {
    status: hit.response.status,
    statusText: hit.response.statusText,
    headers,
  });
}

async function writePersistentGetCache(requestKey: string, response: Response, freshTtlMs: number): Promise<boolean> {
  if (freshTtlMs <= 0) return false;
  try {
    const bodyText = await response.text();
    const nowMs = Date.now();
    const retainUntilMs = nowMs + Math.max(freshTtlMs, Math.floor(PERSIST_GET_CACHE_TTL_MS));
    if (isNativeApp() || bodyText.length > PERSIST_GET_CACHE_MAX_BODY_BYTES) {
      // Native responses use IndexedDB; web large responses use Cache Storage.
      const saved = await putStoredResponse(requestKey, bodyText, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
        retainUntilMs,
      });
      if (saved && isNativeApp()) window.localStorage.removeItem(persistentCacheKey(requestKey));
      return saved;
    }
    // Fresh only for the endpoint's TTL, so a reload never serves a live schedule
    // from hours ago as current; the long retention is for offline use only.
    const payload: PersistentCachedResponse = {
      storedAtMs: nowMs,
      expiresAtMs: nowMs + freshTtlMs,
      retainUntilMs,
      status: response.status,
      statusText: response.statusText,
      headers: Array.from(response.headers.entries()),
      bodyText,
    };
    try {
      // Within the cache's budget (utils/apiCacheSpace.ts), so it can't crowd out
      // scouting work: the oldest copies make room, and beyond that Cache Storage.
      const storageKey = persistentCacheKey(requestKey);
      const entry = JSON.stringify(payload);
      const room = API_CACHE_BUDGET_CHARS - storageKey.length - entry.length;
      if (room < 0) throw new Error('over the saved-copy budget');
      freeApiCacheSpace(room);
      window.localStorage.setItem(storageKey, entry);
    } catch {
      // localStorage full (~5 MB per site): keep it in Cache Storage instead
      return await putStoredResponse(requestKey, bodyText, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
        retainUntilMs,
      });
    }
    prunePersistentCache(Math.max(20, Math.floor(PERSIST_GET_CACHE_MAX_ENTRIES)));
    return window.localStorage.getItem(persistentCacheKey(requestKey)) !== null;
  } catch {
    // ignore cache write failures (unavailable, serialization)
    return false;
  }
}

type OfflineAuditCall = { key: string; url: string; saved: boolean };
let offlineCacheAudit: { calls: OfflineAuditCall[]; accepts: (url: string) => boolean } | null = null;

// Preparing an event bypasses short-lived memory caches and waits for each disk write.
// The caller can report exactly which requests were stored, including team pages.
export function beginOfflineCacheAudit(accepts: (url: string) => boolean = () => true): { calls: OfflineAuditCall[]; stop: () => void } {
  if (offlineCacheAudit) throw new Error('An offline download is already running');
  const audit = { calls: [] as OfflineAuditCall[], accepts };
  offlineCacheAudit = audit;
  return { calls: audit.calls, stop: () => { if (offlineCacheAudit === audit) offlineCacheAudit = null; } };
}

// Stores a payload that arrived inside a combined response under its own URL, as if
// that URL had been fetched: memory for this session, the saved copy for offline.
function seedGetCache(url: string, payload: unknown, ttlMs: number): void {
  const requestKey = cacheKeyFor(url);
  const body = JSON.stringify(payload);
  const headers = { "content-type": "application/json" };
  const now = Date.now();
  cacheResponse(requestKey, {
    storedAtMs: now,
    response: new Response(body, { status: 200, headers }),
    expiresAtMs: now + ttlMs,
  });
  void writePersistentGetCache(requestKey, new Response(body, { status: 200, headers }), ttlMs);
}

export async function hasSavedApiResponse(requestKey: string): Promise<boolean> {
  try {
    const raw = window.localStorage.getItem(persistentCacheKey(requestKey));
    if (raw) {
      const saved = JSON.parse(raw) as Partial<PersistentCachedResponse>;
      if (Number(saved.retainUntilMs) > Date.now()) return true;
    }
  } catch { /* use Cache Storage */ }
  return Boolean(await getStoredResponse(requestKey));
}

// A saved copy no older than maxAgeMs, from memory, localStorage or (for big
// responses) Cache Storage. Used to answer at once while the network catches up.
async function savedCopyWithin(requestKey: string, maxAgeMs: number): Promise<CacheLookupResult | null> {
  const quick = readCacheAnyAge(requestKey);
  if (quick && quick.ageSec * 1000 <= maxAgeMs) return quick;
  try {
    const stored = await getStoredResponse(requestKey);
    if (!stored) return null;
    const ageMs = Date.now() - stored.storedAtMs;
    if (stored.storedAtMs <= 0 || ageMs > maxAgeMs) return null;
    return { state: "stale", response: stored.response, ageSec: Math.max(0, Math.floor(ageMs / 1000)) };
  } catch {
    return null;
  }
}

// Tells mounted pages that a refresh replaced data they may be showing.
function notifyApiUpdated(url: string): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new CustomEvent("api:updated", { detail: { url } }));
  } catch {
    // non-DOM environment
  }
}

// Page-speed timing (perf/rum.ts): a route's first data. The shell's own startup
// requests (event list, search index, health) aren't the page's content.
const SHELL_REQUEST_RE = /\/(events\/suggested|events\/search|health)\b/;
function noteFirstData(url: string, source: DataSource): void {
  if (!SHELL_REQUEST_RE.test(url)) markData(source);
}

export function hasDotSegment(url: string): boolean {
  const path = url.replace(/^https?:\/\/[^/]+/i, "").split(/[?#]/)[0];
  return path.split("/").some((part) => /^(\.|%2e){1,2}$/i.test(part));
}

async function apiFetch(input: string, init?: ApiFetchInit): Promise<Response> {
  const normalizedInput = String(input || "");
  const hasAbsoluteScheme = normalizedInput.startsWith("http://") || normalizedInput.startsWith("https://");
  const isRootRelative = normalizedInput.startsWith("/");
  const url = hasAbsoluteScheme || isRootRelative ? normalizedInput : `${API}/${normalizedInput.replace(/^\/+/, "")}`;
  // Path values are encoded where URLs are built, so a dot segment can only come from
  // injected data (a crafted room or match key) aiming at another endpoint with this
  // user's workspace headers.
  if (hasDotSegment(url)) throw new Error("Refused an API path with a dot segment.");
  const method = normalizeMethod(init?.method);
  const bypassCache = shouldBypassCache(url, init);
  const cacheTtlMs = bypassCache ? 0 : _cacheValueInt(init?.cacheTtlMs, DEFAULT_GET_CACHE_TTL_MS);
  const staleWhileRevalidateMs = bypassCache
    ? 0
    : _cacheValueInt(init?.staleWhileRevalidateMs, DEFAULT_GET_STALE_WHILE_REVALIDATE_MS);
  const requestKey = cacheKeyFor(url, init);
  const auditCall: OfflineAuditCall | null = method === "GET" && offlineCacheAudit?.accepts(url)
    ? { key: requestKey, url, saved: false } : null;
  if (auditCall) offlineCacheAudit!.calls.push(auditCall);
  const shouldUseCache = method === "GET" && cacheTtlMs > 0 && !auditCall;
  const now = Date.now();
  const timeoutMs = Math.max(2000, Math.floor(init?.timeoutMs ?? REQUEST_TIMEOUT_MS));
  let staleCandidate: CacheLookupResult | null = null;

  if (shouldUseCache) {
    const memCached = readMemoryGetCache(requestKey, now, staleWhileRevalidateMs);
    if (memCached && memCached.state === "fresh") {
      noteFirstData(url, "saved");
      return memCached.response.clone();
    }
    if (memCached && memCached.state === "stale") {
      staleCandidate = memCached;
    }
    const persisted = readPersistentGetCache(requestKey, now, staleWhileRevalidateMs);
    if (persisted) {
      const persistedStoredAtMs = Math.max(0, now - persisted.ageSec * 1000);
      cacheResponse(requestKey, {
        storedAtMs: persistedStoredAtMs,
        response: persisted.response.clone(),
        expiresAtMs: persisted.state === "fresh" ? now + cacheTtlMs : now - 1,
      });
      if (persisted.state === "fresh") {
        if (ENABLE_REQUEST_LOGS) {
          console.debug(`[api] ${method} ${url} -> persisted-cache hit`);
        }
        noteFirstData(url, "saved");
        return persisted.response.clone();
      }
      if (!staleCandidate) {
        staleCandidate = persisted;
      }
    }
  }

  const showSavedMaxAgeMs = shouldUseCache ? _cacheValueInt(init?.showSavedMaxAgeMs, 0) : 0;
  if (shouldUseCache && !staleCandidate && showSavedMaxAgeMs > 0) {
    staleCandidate = await savedCopyWithin(requestKey, showSavedMaxAgeMs);
  }

  if (shouldUseCache) {
    const inFlight = inflightGetRequests.get(requestKey);
    if (inFlight) {
      if (staleCandidate) {
        if (ENABLE_REQUEST_LOGS) {
          console.debug(`[api] ${method} ${url} -> stale-cache hit (inflight refresh)`);
        }
        noteFirstData(url, "saved");
        return staleCandidate.response.clone();
      }
      const shared = await inFlight;
      return shared.clone();
    }
  }

  // No network at all and no stale-while-revalidate candidate: serve the last
  // data we ever saw (any age) rather than failing. Separate from staleCandidate.
  if (
    shouldUseCache &&
    !staleCandidate &&
    typeof navigator !== "undefined" &&
    !navigator.onLine
  ) {
    const offlineStale = await offlineStaleFallback(requestKey);
    if (offlineStale) {
      if (ENABLE_REQUEST_LOGS) {
        console.debug(`[api] ${method} ${url} -> offline-stale cache hit (no network)`);
      }
      return offlineStale;
    }
  }

  const headers = new Headers(init?.headers || {});
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  const adminSessionToken = effectiveAdminSessionToken();
  if (adminSessionToken && !headers.has(ADMIN_SESSION_HEADER)) {
    headers.set(ADMIN_SESSION_HEADER, adminSessionToken);
  }
  const workspaceToken = getWorkspaceToken();
  if (workspaceToken && !headers.has(WORKSPACE_ACCESS_HEADER)) {
    headers.set(WORKSPACE_ACCESS_HEADER, workspaceToken);
  }
  const start = performance.now();
  const run = async (forBackground = false) => {
    const timeoutState: TimeoutState = { didTimeout: false };
    const requestSignal = withTimeout(forBackground ? undefined : init?.signal, timeoutMs, timeoutState);
    let response: Response;
    try {
      // index.html may already have started this exact request (bootPrefetch.ts).
      const boot = method === "GET" && !forBackground ? takeBootFetch(url) : null;
      let fetched: Response | null = null;
      if (boot) {
        try {
          // Same time limit as the request itself: a stalled early fetch (a dying
          // connection on venue wifi) kept Home on "Loading matches…" for good.
          let limit: ReturnType<typeof setTimeout> | undefined;
          const early = await Promise.race([
            boot,
            new Promise<null>((resolve) => { limit = setTimeout(() => resolve(null), timeoutMs); }),
          ]);
          clearTimeout(limit);
          fetched = early?.ok ? early : null;
        } catch {
          fetched = null;
        }
      }
      if (!fetched) {
        fetched = await fetch(url, {
          ...init,
          method,
          headers,
          signal: requestSignal,
        });
      }
      // The timeout covers the whole download. Once it is in, the timer must not
      // fire: in Chrome its abort breaks every unread clone, including the copy
      // kept in the cache, so a cache hit after 12 s failed with "timed out".
      const body = NULL_BODY_STATUSES.has(fetched.status) ? null : await fetched.arrayBuffer();
      response = new Response(body, {
        status: fetched.status,
        statusText: fetched.statusText,
        headers: fetched.headers,
      });
    } catch (error) {
      throw normalizeApiFetchError(error, timeoutMs, timeoutState, requestSignal);
    } finally {
      timeoutState.clear?.();
    }
    if (ENABLE_REQUEST_LOGS) {
      const tookMs = Math.round(performance.now() - start);
      const cacheStatus = shouldUseCache ? "cacheable" : "direct";
      console.debug(`[api] ${method} ${url} -> ${response.status} (${tookMs}ms, ${cacheStatus})`);
    }
    // 401 only ever means "workspace access is gone" (admin checks answer 403).
    // Clear it only if it is still the token this request carried.
    const sentWorkspaceToken = headers.get(WORKSPACE_ACCESS_HEADER);
    if (response.status === 401 && sentWorkspaceToken && sentWorkspaceToken === getWorkspaceToken()) {
      clearWorkspaceSession("revoked");
    }
    if (method === "GET" && response.ok) noteFirstData(url, "network");
    if (auditCall && response.ok) {
      auditCall.saved = await writePersistentGetCache(requestKey, response.clone(), Math.max(1, cacheTtlMs));
    } else if (shouldUseCache && response.ok) {
      cacheResponse(requestKey, {
        storedAtMs: Date.now(),
        response: response.clone(),
        expiresAtMs: Date.now() + cacheTtlMs,
      });
      void writePersistentGetCache(requestKey, response.clone(), cacheTtlMs);
    } else if (method === "GET" && response.ok && !url.includes("refresh=true") && !init?.liveOnly) {
      // Live reads skip the cache on purpose, but at an event with no signal the last
      // copy beats an error. Saved as never-fresh (1 ms), so it only serves offline.
      void writePersistentGetCache(requestKey, response.clone(), 1);
      // Nor may the in-memory copy stay older than what the page just showed: once a live
      // match ended and polling went back to cached reads, the pre-match copy in memory
      // came back and the final score vanished for a poll. Dropped, so the next cached
      // read falls through to the copy saved just above.
      responseCache.delete(requestKey);
    }
    return response;
  };

  if (!shouldUseCache && method === "GET" && init?.liveOnly) {
    return run();
  }

  if (!shouldUseCache && method === "GET") {
    // Uncached (live) reads still fall back to the last saved copy: offline, on a weak
    // signal after WEAK_SIGNAL_FALLBACK_MS, or when the request fails.
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      const saved = await offlineStaleFallback(requestKey);
      if (saved) return saved;
    }
    const live = run();
    const slow = await Promise.race([
      live.then(() => false, () => false),
      new Promise<boolean>((resolve) => setTimeout(() => resolve(true), WEAK_SIGNAL_FALLBACK_MS)),
    ]);
    if (slow) {
      const saved = await offlineStaleFallback(requestKey);
      if (saved) {
        live.catch(() => undefined);
        return saved;
      }
    }
    try {
      const response = await live;
      if (serverTrouble(response)) {
        const saved = await offlineStaleFallback(requestKey);
        if (saved) return saved;
      }
      return response;
    } catch (err) {
      const saved = await offlineStaleFallback(requestKey);
      if (saved) return saved;
      throw err;
    }
  }

  if (!shouldUseCache) {
    try {
      return await run();
    } catch (err) {
      // Queue a mutation for replay when offline, or when the connection
      // dropped on a write that is safe to send twice.
      const offline = !navigator.onLine;
      if (method !== "GET" && isReplaySafeWrite(method, url) && (offline || isConnectionFailure(err))) {
        const headerEntries: Record<string, string> = {};
        headers.forEach((v, k) => { headerEntries[k] = v; });
        await enqueueOffline({
          url,
          method,
          body: typeof init?.body === "string" ? init.body : null,
          headers: headerEntries,
          label: (() => {
            const path = new URL(url, window.location.origin).pathname;
            let team = "";
            try {
              const payload = typeof init?.body === "string" ? JSON.parse(init.body) : null;
              const key = String(payload?.team_key || payload?.entry?.team_key || "");
              if (/^frc\d+$/i.test(key)) team = ` for Team ${key.slice(3)}`;
            } catch {
              // A label still works without a readable body.
            }
            return path.includes("pit-scouting") ? `Pit notes${team}`
              : path.includes("picklist") ? "Picklist changes"
              : path.includes("assignment") ? "Scouting assignments"
              : `Scouting report${team}`;
          })(),
        });
        throw new QueuedForSyncError(
          offline
            ? "You are offline. The request has been queued and will be sent when you reconnect."
            : "The connection dropped. This was queued and will be sent automatically.",
        );
      }
      throw err;
    }
  }

  if (staleCandidate) {
    if (ENABLE_REQUEST_LOGS) {
      console.debug(
        `[api] ${method} ${url} -> stale-cache hit (age=${staleCandidate.ageSec}s), background refresh`,
      );
    }
    noteFirstData(url, "saved");
    const staleBody = staleCandidate.response.clone().text().catch(() => "");
    const backgroundPromise = run(true)
      .then(async (fresh) => {
        if (fresh.ok) {
          const [before, after] = await Promise.all([staleBody, fresh.clone().text().catch(() => "")]);
          if (after && before !== after) notifyApiUpdated(url);
        }
        return fresh;
      })
      .catch(() => staleCandidate!.response.clone())
      .finally(() => {
        inflightGetRequests.delete(requestKey);
      });
    inflightGetRequests.set(requestKey, backgroundPromise);
    return staleCandidate.response.clone();
  }

  const promise = run().finally(() => {
    inflightGetRequests.delete(requestKey);
  });
  inflightGetRequests.set(requestKey, promise);
  // Weak signal: if the network is slow and a saved copy exists, show that rather than
  // a spinner; the request carries on and refreshes the cache when it lands.
  const slow = await Promise.race([
    promise.then(
      () => false,
      () => false,
    ),
    new Promise<boolean>((resolve) => setTimeout(() => resolve(true), WEAK_SIGNAL_FALLBACK_MS)),
  ]);
  if (slow) {
    const saved = await offlineStaleFallback(requestKey);
    if (saved) {
      promise.catch(() => undefined);
      if (ENABLE_REQUEST_LOGS) {
        console.debug(`[api] ${method} ${url} -> saved copy (network slower than ${WEAK_SIGNAL_FALLBACK_MS}ms)`);
      }
      return saved;
    }
  }
  try {
    const response = await promise;
    if (serverTrouble(response)) {
      const saved = await offlineStaleFallback(requestKey);
      if (saved) return saved;
    }
    return response.clone();
  } catch (err) {
    // Network fetch failed (offline / DNS / connection refused): fall back to
    // the last data we ever saw, regardless of age, as a last resort.
    const offlineStale = await offlineStaleFallback(requestKey);
    if (offlineStale) {
      if (ENABLE_REQUEST_LOGS) {
        console.debug(`[api] ${method} ${url} -> offline-stale cache hit (fetch failed)`);
      }
      return offlineStale;
    }
    throw err;
  }
}

export async function getApiHealth(): Promise<ApiHealthResponse> {
  // Server settings (read-only mode, write auth), not liveness: that is
  // useOnlineStatus's own uncached ping.
  const res = await apiFetch(`${API}/health`, _cacheRequestOptions(undefined, { cacheTtlMs: 5 * 60_000 }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ApiHealthResponse;
}

export async function createAdminSession(
  candidateKey: string,
  ttlSec = 7200,
): Promise<AdminSessionCreateResponse> {
  const key = String(candidateKey || "").trim();
  if (!key) {
    return {
      ok: false,
      authorized: false,
      status: 400,
      detail: "Admin API key is required.",
    };
  }
  const normalizedTtl = Math.max(60, Math.min(86400, Math.round(ttlSec)));
  const res = await apiFetch(`${API}/maintenance/admin/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      admin_api_key: key,
      ttl_sec: normalizedTtl,
    }),
    bypassCache: true,
    cacheTtlMs: 0,
    timeoutMs: 10000,
  });
  const payload = (await res.json()) as AdminSessionCreateResponse;
  if (!res.ok) {
    return {
      ok: false,
      authorized: false,
      status: res.status,
      detail: payload?.detail || `Unable to create admin session (status ${res.status}).`,
    };
  }
  return {
    ...payload,
    status: Number(payload?.status || res.status),
  };
}

export async function getOpsDashboard(sampleDays: number = 14): Promise<OpsDashboardResponse> {
  const normalized = Math.max(1, Math.min(90, Math.round(sampleDays)));
  const res = await apiFetch(`${API}/maintenance/ops/dashboard?sample_days=${normalized}`, {
    method: "GET",
    bypassCache: true,
    cacheTtlMs: 0,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as OpsDashboardResponse;
}

export async function ingestEvent(eventKey: string): Promise<{
  ok: boolean;
  event_key: string;
  teams: number;
  matches: number;
  match_team_links: number;
  event_team_stats_upserted?: number;
  event_team_stats_source?: string;
  run_post_compute?: boolean;
  ratings_recompute?: {
    triggered: boolean;
    ok: boolean;
    detail?: string | null;
    model_version?: string | null;
    count?: number;
  };
}> {
  const res = await apiFetch(`${API}/events/${encodeURIComponent(eventKey)}/ingest`, { method: "POST" });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as {
    ok: boolean;
    event_key: string;
    teams: number;
    matches: number;
    match_team_links: number;
    event_team_stats_upserted?: number;
    event_team_stats_source?: string;
    run_post_compute?: boolean;
    ratings_recompute?: {
      triggered: boolean;
      ok: boolean;
      detail?: string | null;
      model_version?: string | null;
      count?: number;
    };
  };
}

export async function searchEvents(query: string): Promise<EventSearchResponse> {
  const q = query.trim();
  if (!q) {
    return { ok: true, query, count: 0, events: [] };
  }
  const isYearQuery = /^\d{4}$/.test(q);
  const limit = isYearQuery ? "700" : q.length >= 5 ? "220" : "100";
  const params = new URLSearchParams({ q, limit });
  const res = await apiFetch(
    `${API}/events/search?${params.toString()}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: SEARCH_CACHE_TTL_MS,
      staleWhileRevalidateMs: SEARCH_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventSearchResponse;
}

function mergeSuggestedFallbackEventLists(
  ...lists: EventSearchItem[][]
): EventSearchItem[] {
  const byKey = new Map<string, EventSearchItem>();
  for (const list of lists) {
    for (const event of list || []) {
      const key = String(event.event_key || "").trim().toLowerCase();
      if (!key) continue;
      if (!byKey.has(key)) {
        byKey.set(key, event);
        continue;
      }
      const previous = byKey.get(key)!;
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

async function loadSuggestedEventsSearchFallback(
  preferredYear: number,
  fallbackYear: number,
  limit: number,
): Promise<SuggestedEventsResponse> {
  const [primarySearch, fallbackSearch] = await Promise.allSettled([
    searchEvents(String(preferredYear)),
    searchEvents(String(fallbackYear)),
  ]);
  // Both failed: say so. An empty list "ok: true" read as "no events" and left Home on
  // an empty day with nothing to retry.
  if (primarySearch.status === "rejected" && fallbackSearch.status === "rejected") {
    throw primarySearch.reason instanceof Error ? primarySearch.reason : new Error("Couldn't load events. Check your connection.");
  }
  const merged = mergeSuggestedFallbackEventLists(
    primarySearch.status === "fulfilled" ? primarySearch.value.events || [] : [],
    fallbackSearch.status === "fulfilled" ? fallbackSearch.value.events || [] : [],
  );
  const events = merged.slice(0, Math.max(1, limit));
  const selectedYear = events.some((event) => Number(event.year) === preferredYear)
    ? preferredYear
    : events.some((event) => Number(event.year) === fallbackYear)
      ? fallbackYear
      : null;

  return {
    ok: true,
    preferred_year: preferredYear,
    fallback_year: fallbackYear,
    selected_year: selectedYear,
    source: "search_fallback",
    count: events.length,
    events,
  };
}

export async function getSuggestedEvents(
  preferredYear: number = SEASON.year,
  fallbackYear: number = SEASON.fallbackYear,
  limit = 25,
  options?: {
    preferLiveNow?: boolean;
    remoteTeamCountFetchLimit?: number;
  },
): Promise<SuggestedEventsResponse> {
  const normalizedLimit = Math.max(1, Math.floor(limit));
  const now = Date.now();
  if (now < suggestedEventsCircuitOpenUntilMs) {
    return loadSuggestedEventsSearchFallback(preferredYear, fallbackYear, normalizedLimit);
  }

  // Every caller (event index, Home, Events, pickers, Scouting) asks for the same
  // list with a different length. One shared request, cut to size here, replaces the
  // 2-3 per page load they used to make. The order doesn't depend on the limit.
  const query = suggestedEventsQuery(
    preferredYear,
    fallbackYear,
    Math.max(normalizedLimit, SUGGESTED_EVENTS_SHARED_LIMIT),
    options?.preferLiveNow ?? true,
    Math.max(SUGGESTED_EVENTS_SHARED_TEAM_COUNT_FETCHES, Math.floor(options?.remoteTeamCountFetchLimit ?? 0)),
  );
  try {
    const res = await apiFetch(
      `${API}/events/suggested?${query}`,
      _cacheRequestOptions(undefined, {
        cacheTtlMs: SUGGESTED_EVENTS_CACHE_TTL_MS,
        staleWhileRevalidateMs: SUGGESTED_EVENTS_CACHE_TTL_MS,
        timeoutMs: SUGGESTED_EVENTS_TIMEOUT_MS,
      }),
    );
    if (!res.ok) {
      suggestedEventsCircuitOpenUntilMs =
        Date.now() + Math.max(10_000, Math.floor(SUGGESTED_EVENTS_FAILURE_COOLDOWN_MS));
      return await loadSuggestedEventsSearchFallback(
        preferredYear,
        fallbackYear,
        normalizedLimit,
      );
    }
    const payload = (await res.json()) as SuggestedEventsResponse;
    suggestedEventsCircuitOpenUntilMs = 0;
    const events = (payload.events || []).slice(0, normalizedLimit);
    return { ...payload, events, count: events.length };
  } catch {
    suggestedEventsCircuitOpenUntilMs =
      Date.now() + Math.max(10_000, Math.floor(SUGGESTED_EVENTS_FAILURE_COOLDOWN_MS));
    return await loadSuggestedEventsSearchFallback(
      preferredYear,
      fallbackYear,
      normalizedLimit,
    );
  }
}

export async function searchTeams(query: string, limit = 25): Promise<TeamSearchResponse> {
  const q = query.trim();
  if (!q) {
    return { ok: true, query, count: 0, teams: [] };
  }
  // Fast UI mode: no team-registration expansion in interactive search.
  const params = new URLSearchParams({
    q,
    limit: String(limit),
    include_event_registrations: "false",
    preferred_year: String(SEASON.year),
    fallback_year: String(SEASON.fallbackYear),
  });
  const res = await apiFetch(
    `${API}/teams/search?${params.toString()}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: SEARCH_CACHE_TTL_MS,
      staleWhileRevalidateMs: SEARCH_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamSearchResponse;
}

export async function getTeamRobotImage(
  teamKey: string,
  eventKey?: string,
): Promise<TeamRobotImageResponse> {
  const params = new URLSearchParams({
    preferred_year: String(SEASON.year),
    fallback_year: String(SEASON.fallbackYear),
    v: TEAM_MEDIA_CACHE_VERSION,
  });
  if (eventKey) params.set("event_key", eventKey);
  const res = await apiFetch(
    `${API}/teams/${encodeURIComponent(teamKey)}/robot-image?${params.toString()}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: STATIC_MEDIA_CACHE_TTL_MS,
      staleWhileRevalidateMs: STATIC_MEDIA_CACHE_TTL_MS,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamRobotImageResponse;
}

export async function getTeamLogo(
  teamKey: string,
  eventKey?: string,
): Promise<TeamLogoResponse> {
  const params = new URLSearchParams({
    preferred_year: String(SEASON.year),
    fallback_year: String(SEASON.fallbackYear),
    v: TEAM_MEDIA_CACHE_VERSION,
  });
  if (eventKey) params.set("event_key", eventKey);
  const res = await apiFetch(
    `${API}/teams/${encodeURIComponent(teamKey)}/logo?${params.toString()}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: STATIC_MEDIA_CACHE_TTL_MS,
      staleWhileRevalidateMs: STATIC_MEDIA_CACHE_TTL_MS,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamLogoResponse;
}

export async function getTeamIntel(
  teamKey: string,
  options?: {
    event_key?: string;
    preferred_year?: number;
    fallback_year?: number;
    include_tba?: boolean;
    allow_season_fallback?: boolean;
    auto_heal_ratings?: boolean;
    refresh?: boolean;
  },
  requestOptions?: ApiRequestOptions,
): Promise<TeamIntelResponse> {
  const canUseAdminReadFlags = clientAdminKeyAvailable();
  const params = new URLSearchParams();
  if (options?.event_key) params.set("event_key", options.event_key);
  if (typeof options?.preferred_year === "number") params.set("preferred_year", String(options.preferred_year));
  if (typeof options?.fallback_year === "number") params.set("fallback_year", String(options.fallback_year));
  if (typeof options?.include_tba === "boolean") params.set("include_tba", String(options.include_tba));
  if (typeof options?.allow_season_fallback === "boolean") {
    params.set("allow_season_fallback", String(options.allow_season_fallback));
  }
  // Flags the server defaults to false go in the URL only when true, so equivalent
  // requests share one cache entry (an offline pack missed pages that sent "=false").
  if (options?.auto_heal_ratings && canUseAdminReadFlags) params.set("auto_heal_ratings", "true");
  if (options?.refresh && canUseAdminReadFlags) params.set("refresh", "true");
  const makeSuffix = (query: URLSearchParams) => (query.toString() ? `?${query.toString()}` : "");
  const resolvedRequestOptions = _cacheRequestOptions(requestOptions, {
    cacheTtlMs: TEAM_INTEL_CACHE_TTL_MS,
    staleWhileRevalidateMs: TEAM_INTEL_CACHE_TTL_MS * 3,
  });
  let res = await apiFetch(`${API}/teams/${encodeURIComponent(teamKey)}/intel${makeSuffix(params)}`, resolvedRequestOptions);
  if (
    (res.status === 401 || res.status === 403) &&
    ((params.get("auto_heal_ratings") || "").toLowerCase() === "true" ||
      (params.get("refresh") || "").toLowerCase() === "true")
  ) {
    const fallbackParams = new URLSearchParams(params);
    fallbackParams.delete("auto_heal_ratings");
    fallbackParams.delete("refresh");
    const fallbackRes = await apiFetch(
      `${API}/teams/${encodeURIComponent(teamKey)}/intel${makeSuffix(fallbackParams)}`,
      resolvedRequestOptions,
    );
    if (fallbackRes.ok) {
      res = fallbackRes;
    }
  }
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamIntelResponse;
}

export async function getEventTeamsIntel(
  eventKey: string,
  options?: {
    include_tba?: boolean;
    auto_heal_ratings?: boolean;
    include_season_fallback?: boolean;
    include_rating_details?: boolean;
    include_rating_signals?: boolean;
    refresh?: boolean;
  },
  requestOptions?: ApiRequestOptions,
): Promise<EventTeamsIntelResponse> {
  const canUseAdminReadFlags = clientAdminKeyAvailable();
  const params = new URLSearchParams();
  if (typeof options?.include_tba === "boolean") params.set("include_tba", String(options.include_tba));
  // Flags the server defaults to false go in the URL only when true, so equivalent
  // requests share one cache entry (an offline pack missed pages that sent "=false").
  if (options?.auto_heal_ratings && canUseAdminReadFlags) params.set("auto_heal_ratings", "true");
  if (typeof options?.include_season_fallback === "boolean") {
    params.set("include_season_fallback", String(options.include_season_fallback));
  }
  if (options?.include_rating_details) params.set("include_rating_details", "true");
  if (options?.include_rating_signals) params.set("include_rating_signals", "true");
  if (options?.refresh && canUseAdminReadFlags) params.set("refresh", "true");
  const makeSuffix = (query: URLSearchParams) => (query.toString() ? `?${query.toString()}` : "");
  const resolvedRequestOptions = _cacheRequestOptions(requestOptions, {
    cacheTtlMs: EVENT_INTEL_CACHE_TTL_MS,
    staleWhileRevalidateMs: EVENT_INTEL_CACHE_TTL_MS * 3,
  });
  let res = await apiFetch(
    `${API}/teams/event/${encodeURIComponent(eventKey)}/intel${makeSuffix(params)}`,
    resolvedRequestOptions,
  );
  if (
    (res.status === 401 || res.status === 403) &&
    ((params.get("auto_heal_ratings") || "").toLowerCase() === "true" ||
      (params.get("refresh") || "").toLowerCase() === "true")
  ) {
    const fallbackParams = new URLSearchParams(params);
    fallbackParams.delete("auto_heal_ratings");
    fallbackParams.delete("refresh");
    const fallbackRes = await apiFetch(
      `${API}/teams/event/${encodeURIComponent(eventKey)}/intel${makeSuffix(fallbackParams)}`,
      resolvedRequestOptions,
    );
    if (fallbackRes.ok) {
      res = fallbackRes;
    }
  }
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventTeamsIntelResponse;
}

export async function getEventTeamsHistory(
  eventKey: string,
  historyLimit = 8,
  excludeCurrentEvent = false,
  requestOptions?: ApiRequestOptions,
): Promise<EventTeamsHistoryResponse> {
  const params = new URLSearchParams({
    history_limit: String(historyLimit),
    exclude_current_event: String(excludeCurrentEvent),
  });
  const res = await apiFetch(
    `${API}/scouting/event/${encodeURIComponent(eventKey)}/teams/history?${params.toString()}`,
    _cacheRequestOptions(requestOptions, {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventTeamsHistoryResponse;
}

export async function getTeamBreakdown(
  teamKey: string,
  eventKey?: string,
): Promise<TeamBreakdownResponse> {
  const params = new URLSearchParams({
    match_limit: "20",
    event_limit: "500",
    track_limit: "1000",
  });
  if (eventKey) params.set("event_key", eventKey);
  const res = await apiFetch(
    `${API}/scouting/team/${encodeURIComponent(teamKey)}/breakdown?${params.toString()}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: TEAM_INTEL_CACHE_TTL_MS,
      staleWhileRevalidateMs: TEAM_INTEL_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamBreakdownResponse;
}

export async function getEventRatings(
  eventKey: string,
  refresh = false,
  options?: { includeTrend?: boolean; bypassCache?: boolean; showSavedMaxAgeMs?: number },
): Promise<EventRatingsResponse> {
  // view=summary: no screen reads a rating's details or evidence, 2/3 of the payload.
  const params = new URLSearchParams({ refresh: String(refresh), view: "summary" });
  if (options?.includeTrend) params.set("include_trend", "true");
  const res = await apiFetch(
    `${API}/scouting/event/${encodeURIComponent(eventKey)}/ratings?${params.toString()}`,
    _cacheRequestOptions(
      options?.bypassCache ? { bypassCache: true } : { showSavedMaxAgeMs: options?.showSavedMaxAgeMs },
      {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventRatingsResponse;
}

// Live polling variant: always includes trend data and bypasses the long
// response cache so each poll reflects the latest recompute. Used by
// useLiveRatings; keep getEventRatings cached for the normal one-shot view.
export async function getEventRatingsLive(
  eventKey: string,
): Promise<EventRatingsResponse> {
  const params = new URLSearchParams({ refresh: "false", include_trend: "true", view: "summary" });
  const res = await apiFetch(
    `${API}/scouting/event/${encodeURIComponent(eventKey)}/ratings?${params.toString()}`,
    // 2 s and no stale window: calls that overlap (the panel's first run, a page's own
    // load, a restart when the live flag flips) share one request, while polls (8 s at
    // the fastest) always get a fresh answer.
    _cacheRequestOptions(undefined, { cacheTtlMs: 2000, staleWhileRevalidateMs: 0 }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventRatingsResponse;
}

/* Who is likely to be picked at alliance selection: a draft simulation on ratings and rank. */
export async function getSelectionModel(
  eventKey: string,
  options: {
    rank_weight: number;
    scale: number;
    captains: number;
    simulations: number;
    rank_source?: "auto" | "tba" | "model";
  },
): Promise<SelectionModelResponse> {
  const params = new URLSearchParams({
    rank_weight: String(options.rank_weight),
    scale: String(options.scale),
    captains: String(options.captains),
    simulations: String(options.simulations),
    rank_source: options.rank_source ?? "auto",
  });
  const res = await apiFetch(`${API}/events/${encodeURIComponent(eventKey)}/selection-model?${params.toString()}`, _cacheRequestOptions(undefined, {
    cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
  }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as SelectionModelResponse;
}

export async function getAllianceOutlook(eventKey: string, teamKeys: string[]): Promise<AllianceOutlookResponse> {
  const params = new URLSearchParams({ teams: teamKeys.join(',') });
  const res = await apiFetch(`${API}/events/${encodeURIComponent(eventKey)}/alliance-outlook?${params.toString()}`, _cacheRequestOptions(undefined, {
    cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
  }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as AllianceOutlookResponse;
}

export async function getEventSchedule(
  eventKey: string,
  refresh = false,
  options?: {
    source?: "auto" | "tba" | "first";
    includeLiveResults?: boolean;
    includeTeams?: boolean;
    includeTeamNicknames?: boolean;
    tournamentLevel?: "None" | "Practice" | "Qualification" | "Playoff";
    teamNumber?: number;
    start?: number;
    end?: number;
    limit?: number;
    offset?: number;
    ifModifiedSince?: string;
    onlyModifiedSince?: string;
  },
  requestOptions?: ApiRequestOptions,
): Promise<EventScheduleResponse> {
  const allowRefresh = !refresh || clientAdminKeyAvailable();
  const params = new URLSearchParams({ refresh: String(allowRefresh ? refresh : false) });
  if (options?.source) params.set("source", options.source);
  if (typeof options?.includeLiveResults === "boolean") {
    params.set("includeLiveResults", String(options.includeLiveResults));
  }
  if (typeof options?.includeTeams === "boolean") {
    params.set("includeTeams", String(options.includeTeams));
  }
  if (typeof options?.includeTeamNicknames === "boolean") {
    params.set("includeTeamNicknames", String(options.includeTeamNicknames));
  }
  if (options?.tournamentLevel) params.set("tournamentLevel", options.tournamentLevel);
  if (typeof options?.teamNumber === "number") params.set("teamNumber", String(options.teamNumber));
  if (typeof options?.start === "number") params.set("start", String(options.start));
  if (typeof options?.end === "number") params.set("end", String(options.end));
  if (typeof options?.limit === "number") params.set("limit", String(options.limit));
  if (typeof options?.offset === "number") params.set("offset", String(options.offset));
  if (options?.ifModifiedSince) params.set("ifModifiedSince", options.ifModifiedSince);
  if (options?.onlyModifiedSince) params.set("onlyModifiedSince", options.onlyModifiedSince);
  const res = await apiFetch(
    withLiveVersion(`${API}/matches/event/${encodeURIComponent(eventKey)}/schedule?${params.toString()}`, [eventKey]),
    _cacheRequestOptions(requestOptions, {
      cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_SCHEDULE_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  const schedule = (await res.json()) as EventScheduleResponse;
  noteLiveSchedule(eventKey, schedule.matches);
  return schedule;
}

type EventSchedulesBatchResponse = {
  ok: boolean;
  count: number;
  schedules: Record<string, EventScheduleResponse | { ok: false; status?: number; detail?: string }>;
};

const EVENT_SCHEDULES_BATCH_MAX = 24;

// Many events' schedules in one request (Home shows every event on a day). Each one
// is also stored under its single-event URL, so opening that event next is instant
// and the copy is there offline. Falls back to one request per event if the batch
// call fails (e.g. an older backend).
export async function getEventSchedules(
  eventKeys: string[],
  requestOptions?: ApiRequestOptions,
): Promise<Record<string, EventScheduleResponse | Error>> {
  const keys = [...new Set(eventKeys.map((key) => key.trim().toLowerCase()).filter(Boolean))];
  const out: Record<string, EventScheduleResponse | Error> = {};
  for (let offset = 0; offset < keys.length; offset += EVENT_SCHEDULES_BATCH_MAX) {
    const chunk = keys.slice(offset, offset + EVENT_SCHEDULES_BATCH_MAX);
    let batch: EventSchedulesBatchResponse | null = null;
    try {
      const res = await apiFetch(
        withLiveVersion(`${API}/matches/schedules?${new URLSearchParams({ event_keys: chunk.join(",") }).toString()}`, chunk),
        _cacheRequestOptions(requestOptions, {
          cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
          staleWhileRevalidateMs: EVENT_SCHEDULE_CACHE_TTL_MS * 3,
        }),
      );
      if (res.ok) batch = (await res.json()) as EventSchedulesBatchResponse;
    } catch {
      batch = null;
    }
    if (!batch || !batch.schedules) {
      const singles = await Promise.allSettled(chunk.map((key) => getEventSchedule(key, false, undefined, requestOptions)));
      singles.forEach((result, idx) => {
        out[chunk[idx]] = result.status === "fulfilled"
          ? result.value
          : result.reason instanceof Error ? result.reason : new Error("Schedule unavailable");
      });
      continue;
    }
    for (const key of chunk) {
      const entry = batch.schedules[key];
      if (entry && entry.ok !== false && Array.isArray((entry as EventScheduleResponse).matches)) {
        const schedule = entry as EventScheduleResponse;
        out[key] = schedule;
        noteLiveSchedule(key, schedule.matches);
        seedGetCache(`${API}/matches/event/${encodeURIComponent(key)}/schedule?refresh=false`, schedule, EVENT_SCHEDULE_CACHE_TTL_MS);
      } else {
        const detail = entry && "detail" in entry && entry.detail ? entry.detail : "Schedule unavailable";
        out[key] = new Error(detail);
      }
    }
  }
  return out;
}

export async function getEventLiveStream(eventKey: string): Promise<EventLiveStreamResponse> {
  const res = await apiFetch(
    `${API}/matches/event/${encodeURIComponent(eventKey)}/live-stream`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventLiveStreamResponse;
}

export async function getEventTeamLiveForm(
  eventKey: string,
  options?: {
    form_window?: number;
    live_window_sec?: number;
  },
  requestOptions?: ApiRequestOptions,
): Promise<EventTeamLiveFormResponse> {
  const params = new URLSearchParams();
  if (typeof options?.form_window === "number") params.set("form_window", String(options.form_window));
  if (typeof options?.live_window_sec === "number") params.set("live_window_sec", String(options.live_window_sec));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const res = await apiFetch(
    withLiveVersion(`${API}/matches/event/${encodeURIComponent(eventKey)}/team-live-form${suffix}`, [eventKey]),
    _cacheRequestOptions(requestOptions, {
      cacheTtlMs: EVENT_LIVE_FORM_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_LIVE_FORM_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventTeamLiveFormResponse;
}

// The schedule with each match's prediction. The URL predates synergy's removal and is
// kept so saved offline copies still match.
export async function getEventPredictionSchedule(
  eventKey: string,
  options?: {
    showSavedMaxAgeMs?: number;
    // Skip this tab's cached copy.
    bypassCache?: boolean;
  },
): Promise<EventPredictionScheduleResponse> {
  const res = await apiFetch(
    withLiveVersion(`${API}/events/${encodeURIComponent(eventKey)}/schedule-with-synergy`, [eventKey]),
    _cacheRequestOptions(
      options?.bypassCache ? { bypassCache: true } : { showSavedMaxAgeMs: options?.showSavedMaxAgeMs },
      {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventPredictionScheduleResponse;
}

export async function getEventAlliances(
  eventKey: string,
  options?: {
    ifModifiedSince?: string;
    onlyModifiedSince?: string;
  },
): Promise<EventAlliancesResponse> {
  const params = new URLSearchParams();
  if (options?.ifModifiedSince) params.set("ifModifiedSince", options.ifModifiedSince);
  if (options?.onlyModifiedSince) params.set("onlyModifiedSince", options.onlyModifiedSince);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const res = await apiFetch(
    `${API}/matches/event/${encodeURIComponent(eventKey)}/alliances${suffix}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_SCHEDULE_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventAlliancesResponse;
}

export type EventForecastTeam = {
  team_key: string;
  current_rank: number | null;
  ranking_score: number | null;
  matches_played: number;
  points_per_match: number;
  expected_rank?: number;
  top8?: number;
  captain?: number;
  on_alliance?: number;
  win_event?: number;
  expected_rp?: number;
  /* A finished event: on the winning / losing finals alliance. */
  won_event?: boolean;
  finalist?: boolean;
};

export type EventForecastResponse = {
  ok: boolean;
  event_key: string;
  runs?: number;
  quals_played?: number;
  quals_remaining?: number;
  playoffs_started?: boolean;
  /* False for an event with no qualification matches (playoffs only). */
  simulated: boolean;
  /* The event is over (or its season is frozen): what happened, not a simulation. */
  finished?: boolean;
  winners?: string[];
  finalists?: string[];
  /* "season_not_rated": a season FRCMOB has no ratings for. */
  unavailable?: string;
  teams: EventForecastTeam[];
};

/* The rest of the event played thousands of times from the team model: each team's
   expected rank and chances of the top 8, captaining, an alliance and the title. */
export async function getEventForecast(eventKey: string, requestOptions?: ApiRequestOptions): Promise<EventForecastResponse> {
  const res = await apiFetch(
    `${API}/events/${encodeURIComponent(eventKey)}/forecast`,
    _cacheRequestOptions(requestOptions, {
      cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_SCHEDULE_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventForecastResponse;
}

export type PickValueCandidate = {
  team_key: string;
  win_vs_strong_alliance: number;
  alliance_points: number;
  points_per_match: number;
  rating_0_100: number;
  climb_points: number;
  dud_rate: number;
  bonus_rp_chances: Record<string, number>;
};

export type PickValuesResponse = {
  ok: boolean;
  event_key: string;
  partners: string[];
  assumed_third: string | null;
  reference: string[];
  reference_points?: number;
  candidates: PickValueCandidate[];
  /* "season_frozen" / "season_not_rated": a past season has no live model to ask. */
  unavailable?: string;
};

export type RatingHistoryEvent = {
  event_key: string;
  event_name: string | null;
  start: number | null;
  rating_0_100: number;
  rating_range_80?: [number, number] | null;
  points_per_match?: number | null;
  matches_here: number;
  rank?: number | null;
  field_size?: number | null;
  won_event: boolean;
};

export type RatingHistorySeason = { season: number; game: string | null; current: boolean; frozen: boolean; events: RatingHistoryEvent[] };

export type RatingHistoryResponse = { ok: boolean; team_key: string; seasons: RatingHistorySeason[]; first_rated_season: number | null };

/* A team's FRCMOB ratings season by season; seasons are different games, listed apart. */
export async function getTeamRatingHistory(teamKey: string): Promise<RatingHistoryResponse> {
  const res = await apiFetch(`${API}/teams/${encodeURIComponent(teamKey)}/rating-history`, _cacheRequestOptions(undefined, {
    cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
    staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
  }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as RatingHistoryResponse;
}

/* Who to pick next for an alliance of 1-2 teams: each candidate's chance to beat a
   strong alliance at the event with them. */
export async function getPickValues(eventKey: string, teamKeys: string[], limit = 30): Promise<PickValuesResponse> {
  const params = new URLSearchParams({ teams: teamKeys.join(','), limit: String(limit) });
  const res = await apiFetch(`${API}/events/${encodeURIComponent(eventKey)}/pick-values?${params.toString()}`, _cacheRequestOptions(undefined, {
    cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
  }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as PickValuesResponse;
}

export interface EventMatchupResponse {
  ok: boolean;
  event_key: string;
  red: string[];
  blue: string[];
  playoff?: boolean;
  red_points?: number;
  blue_points?: number;
  margin_sd?: number;
  red_win_prob?: number;
  blue_win_prob?: number;
  matches_seen?: number;
  unavailable?: 'season_not_rated' | 'season_frozen' | 'no_prediction';
}

// Any six teams at an event, predicted as if they played now (not a scheduled match).
export async function getEventMatchup(eventKey: string, red: string[], blue: string[], playoff: boolean): Promise<EventMatchupResponse> {
  const params = new URLSearchParams({ red: red.join(','), blue: blue.join(','), playoff: String(playoff) });
  const res = await apiFetch(`${API}/events/${encodeURIComponent(eventKey)}/matchup?${params.toString()}`, _cacheRequestOptions(undefined, {
    cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
  }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventMatchupResponse;
}

export interface WhatIfRank {
  expected_rank: number;
  top8: number;
  top4: number;
  rank_likely_best: number;
  rank_likely_worst: number;
  rank_counts: number[];
}

export interface EventWhatIfResponse {
  ok: boolean;
  event_key: string;
  team_key: string;
  matches: Array<{
    match_key: string;
    alliance: 'red' | 'blue';
    partners: string[];
    opponents: string[];
    model_win_prob: number;
    forced: 'win' | 'loss' | null;
  }>;
  field_size?: number;
  runs?: number;
  model?: WhatIfRank;
  what_if?: WhatIfRank;
  unavailable?: 'season_not_rated' | 'season_frozen' | 'event_finished' | 'quals_over' | 'no_matches_left';
}

// Where a team would finish the quals if it won or lost the matches given.
export async function getEventWhatIf(eventKey: string, teamKey: string, wins: string[], losses: string[]): Promise<EventWhatIfResponse> {
  const params = new URLSearchParams({ team: teamKey, wins: wins.join(','), losses: losses.join(',') });
  const res = await apiFetch(`${API}/events/${encodeURIComponent(eventKey)}/what-if?${params.toString()}`, _cacheRequestOptions(undefined, {
    cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
  }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventWhatIfResponse;
}

export interface HeadToHeadRecord { played: number; wins: number; losses: number; ties: number }

export interface HeadToHeadResponse {
  ok: boolean;
  team_key: string;
  other_key: string;
  together: HeadToHeadRecord;
  against: HeadToHeadRecord;
  seasons: number[];
  match_count: number;
  matches: Array<{
    match_key: string;
    event_key: string;
    event_name: string | null;
    season: number;
    comp_level: string;
    relation: 'together' | 'against';
    alliance: 'red' | 'blue';
    score: number | null;
    opponent_score: number | null;
    result: 'win' | 'loss' | 'tie' | null;
    time: number | null;
  }>;
}

// Every match two teams played on the same alliance or against each other, any season.
export async function getTeamHeadToHead(teamKey: string, otherKey: string): Promise<HeadToHeadResponse> {
  const params = new URLSearchParams({ other: otherKey });
  const res = await apiFetch(`${API}/teams/${encodeURIComponent(teamKey)}/head-to-head?${params.toString()}`, _cacheRequestOptions(undefined, {
    cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
  }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as HeadToHeadResponse;
}

export interface SeasonStoryMatch {
  match_key: string;
  alliance: 'red' | 'blue';
  score: number | null;
  opponent_score: number | null;
  result: 'win' | 'loss' | 'tie';
  event_key?: string;
}

export interface SeasonStoryEvent {
  event_key: string;
  event_name: string;
  start: number | null;
  qual_rank: number | null;
  field_size: number | null;
  record: { wins: number; losses: number; ties: number };
  best_match: SeasonStoryMatch | null;
  finish: 'won' | 'finalist' | 'playoffs' | 'quals';
  rating_0_100: number | null;
  points_per_match: number | null;
}

export interface SeasonStoryResponse {
  ok: boolean;
  team_key: string;
  nickname: string | null;
  season: number;
  game: string | null;
  events: SeasonStoryEvent[];
  totals: { wins: number; losses: number; ties: number; events: number; event_wins: number; best_match: SeasonStoryMatch | null };
}

// A team's season event by event: finish, record, best match, rating.
export async function getTeamSeasonStory(teamKey: string, season?: number): Promise<SeasonStoryResponse> {
  const params = new URLSearchParams();
  if (season) params.set('season', String(season));
  const query = params.toString();
  const res = await apiFetch(`${API}/teams/${encodeURIComponent(teamKey)}/season-story${query ? `?${query}` : ''}`, _cacheRequestOptions(undefined, {
    cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
  }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as SeasonStoryResponse;
}

export interface MatchPracticeResponse {
  ok: boolean;
  match_key: string;
  event_key: string;
  label: string;
  team_key: string;
  alliance: 'red' | 'blue';
  played: boolean;
  // False for a season the official breakdown isn't parsed for: nothing to check against.
  checkable: boolean;
  video: { embed_url: string; watch_url: string } | null;
  official: {
    auto_tower_points: number | null;
    endgame_tower_points: number | null;
    alliance_fuel: number | null;
    alliance_score: number | null;
  };
}

// Scout training: one robot in a played match and what the official result knows about it.
export async function getMatchPractice(matchKey: string, teamKey: string): Promise<MatchPracticeResponse> {
  const params = new URLSearchParams({ team: teamKey });
  const res = await apiFetch(`${API}/matches/${encodeURIComponent(matchKey)}/practice?${params.toString()}`, _cacheRequestOptions(undefined, {
    cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
  }));
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as MatchPracticeResponse;
}

export async function getEventRankings(eventKey: string, requestOptions?: ApiRequestOptions): Promise<EventRankingsResponse> {
  const res = await apiFetch(
    withLiveVersion(`${API}/events/${encodeURIComponent(eventKey)}/rankings`, [eventKey]),
    _cacheRequestOptions(requestOptions, {
      cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_SCHEDULE_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventRankingsResponse;
}

export async function getEventPredictions(eventKey: string, requestOptions?: ApiRequestOptions): Promise<EventPredictionsResponse> {
  const res = await apiFetch(
    `${API}/events/${encodeURIComponent(eventKey)}/predictions`,
    _cacheRequestOptions(requestOptions, {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventPredictionsResponse;
}

export async function getEventAwards(eventKey: string, requestOptions?: ApiRequestOptions): Promise<EventAwardsResponse> {
  const res = await apiFetch(
    `${API}/events/${encodeURIComponent(eventKey)}/awards`,
    _cacheRequestOptions(requestOptions, {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 4,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventAwardsResponse;
}

export type ScoutingRoomPresenceMember = {
  scout_profile: string;
  connections: number;
  first_connected_at?: string | null;
  last_connected_at?: string | null;
  last_seen_at?: string | null;
};

export type ScoutingRoomMeta = {
  room_key: string;
  event_key: string | null;
  title: string | null;
  created_by: string | null;
  archived: boolean;
  created_at: string | null;
  updated_at: string | null;
  last_activity_at: string | null;
  leader_scout_profile?: string | null;
  leader_source?: string | null;
  secondary_leader_scout_profiles?: string[];
  presence: ScoutingRoomPresenceMember[];
  ws_path: string;
  room_role?: string;
};

export type ScoutingRoomAccess = {
  room_role: string;
  room_access_token: string;
  expires_at: string;
  expires_at_unix: number;
  ttl_sec: number;
  header: string;
};

export type ScoutingRoomEntryRecord = {
  server_id: number;
  room_key: string;
  event_key: string | null;
  match_key: string | null;
  team_key: string | null;
  scout_profile: string;
  client_entry_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  entry: Record<string, unknown>;
  scores: {
    total_points: number | null;
    driver_score_0_100: number | null;
    manual_rating_0_100: number | null;
    scouting_api_rating_0_100: number | null;
    overall_scout_rating_0_100: number | null;
  };
};

export type ScoutingRoomStateResponse = {
  ok: boolean;
  room: ScoutingRoomMeta;
  entries: ScoutingRoomEntryRecord[];
  assignments?: ScoutingRoomAssignmentRecord[];
  my_assignments?: ScoutingRoomAssignmentRecord[];
  access?: ScoutingRoomAccess;
};

export type ScoutingRoomSaveEntryResponse = {
  ok: boolean;
  room_key: string;
  created: boolean;
  entry: ScoutingRoomEntryRecord;
};

type AutoScoutSeasonSupport = {
  supported: boolean;
  form_fields: string[];
  derived_insights: string[];
  mapper_version: string;
  min_track_coverage: number;
};

export type AutoScoutDraftResponse = {
  ok: boolean;
  created?: boolean;
  draft: AutoScoutDraftRecord | null;
  season_support?: AutoScoutSeasonSupport | null;
  field_overrides?: Record<string, { from: unknown; to: unknown }>;
  approved_payload?: Record<string, unknown>;
};

export type ScoutingRoomAssignmentRecord = {
  id: number;
  room_key: string;
  event_key: string | null;
  match_key: string;
  team_key: string;
  assigned_scout_profile: string;
  assigned_by_scout_profile: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type ScoutingRoomLeaderUpdateResponse = {
  ok: boolean;
  room_key: string;
  target_scout_profile: string;
  secondary_leader_scout_profiles: string[];
  created?: boolean;
  removed?: boolean;
};

export type ScoutingRoomKickMemberResponse = {
  ok: boolean;
  room_key: string;
  target_scout_profile: string;
  kicked: boolean;
  removed_connections: number;
  leader_scout_profile?: string | null;
  leader_source?: string | null;
};

function withoutRoomAccessToken<T extends { room_access_token?: string }>(
  payload: T,
): Omit<T, "room_access_token"> {
  const body = { ...payload } as T & { room_access_token?: string };
  delete body.room_access_token;
  return body as Omit<T, "room_access_token">;
}

export async function createOrJoinScoutingRoom(payload: {
  room_key?: string;
  event_key?: string;
  title?: string;
  scout_profile: string;
  client_id?: string;
  create_if_missing?: boolean;
  room_access_token?: string;
  timeoutMs?: number;
}): Promise<ScoutingRoomStateResponse> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (payload.room_access_token) {
    headers.set(ROOM_ACCESS_HEADER, String(payload.room_access_token || "").trim());
  }
  const body = withoutRoomAccessToken(payload);
  const res = await apiFetch(`${API}/scouting/rooms`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    timeoutMs: Math.max(2000, Math.floor(payload.timeoutMs ?? 25000)),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomStateResponse;
}

export async function getScoutingRoomState(
  roomKey: string,
  historyLimit = 200,
  options?: {
    scout_profile?: string;
    client_id?: string;
    presence_heartbeat?: boolean;
    room_access_token?: string;
    timeoutMs?: number;
  },
): Promise<ScoutingRoomStateResponse> {
  const params = new URLSearchParams({ history_limit: String(historyLimit) });
  if (options?.scout_profile) params.set("scout_profile", String(options.scout_profile || "").trim());
  if (options?.client_id) params.set("client_id", String(options.client_id || "").trim());
  if (options?.presence_heartbeat) params.set("presence_heartbeat", "1");
  const roomAccessToken = String(options?.room_access_token || "").trim();
  const res = await apiFetch(`${API}/scouting/rooms/${encodeURIComponent(roomKey)}?${params.toString()}`, {
    headers: roomAccessToken ? { [ROOM_ACCESS_HEADER]: roomAccessToken } : undefined,
    bypassCache: true,
    cacheTtlMs: 0,
    timeoutMs: Math.max(8000, Math.floor(options?.timeoutMs ?? 20000)),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomStateResponse;
}

// ── Team rooms: one room per workspace and event, found without a key ──

export type TeamRoomMember = {
  member_id: number;
  display_name: string;
  role: "leader" | "member";
};

export type TeamRoomAssignment = {
  match_key: string;
  team_key: string;
  assigned_member_id: number | null;
  assigned_display_name: string;
  member_active: boolean;
  covered: boolean;
  covered_by_me: boolean;
};

export type TeamRoomSnapshot = {
  ok: boolean;
  room_key: string;
  event_key: string;
  me: TeamRoomMember;
  members: TeamRoomMember[];
  assignments: TeamRoomAssignment[];
};

export type TeamRoomEnsureResponse = TeamRoomSnapshot & {
  created: boolean;
  access?: ScoutingRoomAccess;
};

export type TeamRoomAssignmentChange = {
  match_key: string;
  team_key: string;
  assigned_member_id: number | null;
};

export async function ensureTeamRoom(eventKey: string, clientId?: string): Promise<TeamRoomEnsureResponse> {
  const res = await apiFetch(`${API}/scouting/rooms/team`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_key: eventKey.trim().toLowerCase(), client_id: clientId || undefined }),
    bypassCache: true,
    cacheTtlMs: 0,
    timeoutMs: 20000,
  });
  if (!res.ok) throw new Error(await readError(res));
  const payload = (await res.json()) as TeamRoomEnsureResponse;
  // The keyed room code (socket, entries) still needs the room token.
  if (payload.access?.room_access_token && payload.room_key) {
    setStoredRoomAccessToken(payload.room_key, payload.access.room_access_token, payload.access.expires_at_unix);
  }
  return payload;
}

// null means the team hasn't opened this event yet; ensureTeamRoom creates it.
export async function getTeamRoom(eventKey: string): Promise<TeamRoomSnapshot | null> {
  const res = await apiFetch(`${API}/scouting/rooms/team/${encodeURIComponent(eventKey.trim().toLowerCase())}`, {
    bypassCache: true,
    cacheTtlMs: 0,
    liveOnly: true,
    timeoutMs: 15000,
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamRoomSnapshot;
}

export async function saveTeamRoomAssignments(
  eventKey: string,
  changes: TeamRoomAssignmentChange[],
): Promise<TeamRoomSnapshot> {
  const res = await apiFetch(`${API}/scouting/rooms/team/${encodeURIComponent(eventKey.trim().toLowerCase())}/assignments`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changes }),
    bypassCache: true,
    cacheTtlMs: 0,
    timeoutMs: 30000,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamRoomSnapshot;
}

export async function kickScoutingRoomMember(
  roomKey: string,
  payload: {
    scout_profile: string;
    room_access_token?: string;
  },
): Promise<ScoutingRoomKickMemberResponse> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (payload.room_access_token) {
    headers.set(ROOM_ACCESS_HEADER, String(payload.room_access_token || "").trim());
  }
  const body = withoutRoomAccessToken(payload);
  const res = await apiFetch(`${API}/scouting/rooms/${encodeURIComponent(roomKey)}/kick`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomKickMemberResponse;
}

export async function addScoutingRoomSecondaryLeader(
  roomKey: string,
  payload: {
    scout_profile: string;
    room_access_token?: string;
  },
): Promise<ScoutingRoomLeaderUpdateResponse> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (payload.room_access_token) {
    headers.set(ROOM_ACCESS_HEADER, String(payload.room_access_token || "").trim());
  }
  const body = withoutRoomAccessToken(payload);
  const res = await apiFetch(`${API}/scouting/rooms/${encodeURIComponent(roomKey)}/leaders`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomLeaderUpdateResponse;
}

export async function removeScoutingRoomSecondaryLeader(
  roomKey: string,
  payload: {
    scout_profile: string;
    room_access_token?: string;
  },
): Promise<ScoutingRoomLeaderUpdateResponse> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (payload.room_access_token) {
    headers.set(ROOM_ACCESS_HEADER, String(payload.room_access_token || "").trim());
  }
  const body = withoutRoomAccessToken(payload);
  const res = await apiFetch(`${API}/scouting/rooms/${encodeURIComponent(roomKey)}/leaders/remove`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomLeaderUpdateResponse;
}

export async function saveScoutingRoomEntry(
  roomKey: string,
  payload: {
    entry: Record<string, unknown>;
    scout_profile: string;
    client_entry_id?: string;
    room_access_token?: string;
    timeoutMs?: number;
  },
): Promise<ScoutingRoomSaveEntryResponse> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (payload.room_access_token) {
    headers.set(ROOM_ACCESS_HEADER, String(payload.room_access_token || "").trim());
  }
  const body = withoutRoomAccessToken(payload);
  const res = await apiFetch(`${API}/scouting/rooms/${encodeURIComponent(roomKey)}/entries`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    timeoutMs: Math.max(2000, Math.floor(payload.timeoutMs ?? 30000)),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomSaveEntryResponse;
}

export async function generateAutoScoutDraft(payload: {
  event_key: string;
  match_key: string;
  team_key: string;
  force_regenerate?: boolean;
}): Promise<AutoScoutDraftResponse> {
  const res = await apiFetch(`${API}/scouting/auto-drafts/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as AutoScoutDraftResponse;
}

export async function getAutoScoutDraft(
  eventKey: string,
  matchKey: string,
  teamKey: string,
): Promise<AutoScoutDraftResponse> {
  const res = await apiFetch(`${API}/scouting/auto-drafts/${encodeURIComponent(eventKey)}/${encodeURIComponent(matchKey)}/${encodeURIComponent(teamKey)}`);
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as AutoScoutDraftResponse;
}

export async function approveAutoScoutDraft(
  draftId: number,
  payload: {
    approved_by: string;
    draft_version: number;
    edited_payload: Record<string, unknown>;
  },
): Promise<AutoScoutDraftResponse> {
  const res = await apiFetch(`${API}/scouting/auto-drafts/${encodeURIComponent(draftId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as AutoScoutDraftResponse;
}

export async function rejectAutoScoutDraft(
  draftId: number,
  payload: {
    reason: string;
    draft_version: number;
  },
): Promise<AutoScoutDraftResponse> {
  const res = await apiFetch(`${API}/scouting/auto-drafts/${encodeURIComponent(draftId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as AutoScoutDraftResponse;
}

// ── Heatmap and shift-play fetchers ──────────────────────────────

export async function getTeamHeatmap(
  teamKey: string,
  eventKey: string,
  options?: {
    match_key?: string;
    grid_cols?: number;
    grid_rows?: number;
    sigma?: number;
    // Operators only: also count phone recordings nobody has reviewed yet.
    include_unreviewed?: boolean;
    // Workspace members: also count your own team's recordings before review.
    include_workspace?: boolean;
  },
): Promise<TeamHeatmapResponse> {
  const params = new URLSearchParams({ event_key: eventKey });
  if (options?.match_key) params.set("match_key", options.match_key);
  if (options?.grid_cols != null) params.set("grid_cols", String(options.grid_cols));
  if (options?.grid_rows != null) params.set("grid_rows", String(options.grid_rows));
  if (options?.sigma != null) params.set("sigma", String(options.sigma));
  if (options?.include_unreviewed) params.set("include_unreviewed", "true");
  if (options?.include_workspace) params.set("include_workspace", "true");
  const res = await apiFetch(
    `${API}/tracks/heatmap/${encodeURIComponent(teamKey)}?${params.toString()}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamHeatmapResponse;
}

// Shift-based offense/defense + attack/defense heat maps (the on-device-aligned
// engine), aggregated across a team's analyzed matches at an event.
export type TeamShiftPlayResponse = {
  ok: boolean;
  team_key: string;
  event_key: string;
  available: boolean;
  sample_matches: number;
  own_unreviewed_matches?: number;
  offense: { level_1_5: number; confidence_0_1: number } | null;
  defense: { level_1_5: number; confidence_0_1: number; assessable: boolean } | null;
  attack_heatmap: TeamHeatmapResponse | null;
  defense_heatmap: TeamHeatmapResponse | null;
};

export async function getTeamShiftPlay(
  teamKey: string,
  eventKey: string,
  options?: { include_workspace?: boolean },
): Promise<TeamShiftPlayResponse> {
  const params = new URLSearchParams({ event_key: eventKey });
  if (options?.include_workspace) params.set("include_workspace", "true");
  const res = await apiFetch(
    `${API}/tracks/shift-play/${encodeURIComponent(teamKey)}?${params.toString()}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamShiftPlayResponse;
}

// Per-robot shift-play as returned by the on-device session sync (the match-level
// analyze_match_shift_play shape, keyed by team_key).
type OnDeviceRobotShiftPlay = {
  team_key: string;
  alliance: string;
  offense: { level_1_5: number; confidence_0_1: number };
  defense: { level_1_5: number; confidence_0_1: number; assessable: boolean };
  metrics?: Record<string, unknown>;
  heatmaps?: { attack: number[][]; defense: number[][] };
};

export type OnDeviceSessionSyncResponse = {
  ok: boolean;
  match_key: string;
  event_key: string;
  run_id: number;
  on_device_session_id: number;
  session_key: string;
  reused_run: boolean;
  status: "provisional" | "accepted" | "rejected";
  quality_score: number;
  quality: Record<string, unknown> & { eligible_for_review?: boolean };
  shift1_active_alliance: "red" | "blue" | null;
  shift1_source: string | null;
  team_count: number;
  points_persisted: number;
  points_by_team: Record<string, number>;
  skipped_unknown_teams: string[];
  shift_play: Record<string, OnDeviceRobotShiftPlay> | null;
  shift_play_missing_reason: string | null;
};

// Flush a finished on-device match breakdown to the server (POST
// /tracks/on-device-session). The StoredSession camelCase keys match the
// endpoint's request aliases, so a stored session posts verbatim. apiFetch
// auto-attaches the admin/room headers the endpoint's auth gate expects.
class OnDeviceSyncError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'OnDeviceSyncError';
  }
}

export async function syncOnDeviceSession(session: {
  id: string;
  eventKey?: string;
  matchKey?: string;
  createdAt?: number;
  workspaceId?: number | null;
  payload: unknown;
}, workspaceToken?: string): Promise<OnDeviceSessionSyncResponse> {
  const res = await apiFetch(`${API}/tracks/on-device-session`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(workspaceToken ? { [WORKSPACE_ACCESS_HEADER]: workspaceToken } : {}),
    },
    body: JSON.stringify(session),
    bypassCache: true,
    cacheTtlMs: 0,
    timeoutMs: 30000,
  });
  if (!res.ok) throw new OnDeviceSyncError(res.status, await readError(res));
  return (await res.json()) as OnDeviceSessionSyncResponse;
}

function webSocketBase(): URL {
  const base = new URL(WS_API, window.location.origin);
  if (base.protocol === "https:") base.protocol = "wss:";
  else if (base.protocol === "http:") base.protocol = "ws:";
  else if (base.protocol !== "ws:" && base.protocol !== "wss:") {
    base.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  }
  if (window.location.protocol === "https:" && base.protocol === "ws:") {
    // Prevent mixed-content websocket failures when UI is loaded over HTTPS.
    base.protocol = "wss:";
  }
  return base;
}

// Public and credential-free: it only says when an event's results change.
export function liveResultsWebSocketUrl(eventKey: string): string {
  const base = webSocketBase();
  const basePathPrefix = base.pathname && base.pathname !== "/" ? base.pathname.replace(/\/+$/, "") : "";
  base.pathname = `${basePathPrefix}/matches/event/${encodeURIComponent(eventKey.trim().toLowerCase())}/live`;
  base.search = "";
  return base.toString();
}

export function scoutingRoomWebSocketUrl(
  roomKey: string,
  options?: {
    scout_profile?: string;
    event_key?: string;
    title?: string;
    client_id?: string;
    history_limit?: number;
    room_access_token?: string;
  },
): string {
  const normalizedRoom = roomKey.trim().toLowerCase();
  const base = webSocketBase();
  const basePathPrefix = base.pathname && base.pathname !== "/" ? base.pathname.replace(/\/+$/, "") : "";
  base.pathname = `${basePathPrefix}/scouting/rooms/${encodeURIComponent(normalizedRoom)}/ws`;
  base.search = "";
  if (options?.scout_profile) base.searchParams.set("scout_profile", options.scout_profile);
  if (options?.event_key) base.searchParams.set("event_key", options.event_key);
  if (options?.title) base.searchParams.set("title", options.title);
  if (options?.client_id) base.searchParams.set("client_id", options.client_id);
  if (typeof options?.history_limit === "number") {
    base.searchParams.set("history_limit", String(Math.max(1, Math.min(500, Math.floor(options.history_limit)))));
  }
  if (options?.room_access_token) {
    base.searchParams.set("room_access", String(options.room_access_token || "").trim());
  }
  // No admin token here: the socket never reads one, and URLs end up in proxy logs.
  // Browsers can't put headers on a WebSocket, so workspace access rides the URL.
  const workspaceToken = getWorkspaceToken();
  if (workspaceToken) {
    base.searchParams.set(WORKSPACE_ACCESS_QUERY_PARAM, workspaceToken);
  }
  return base.toString();
}

// ── Team workspaces ────────────────────────────────────────────

export interface WorkspaceMemberRecord extends WorkspaceMe {
  joined_at: string | null;
  last_seen_at: string | null;
}

interface WorkspaceDetails extends WorkspaceSummary {
  join_code_rotated_at: string | null;
  created_at: string | null;
}

export interface WorkspaceStateResponse {
  ok: boolean;
  workspace: WorkspaceDetails;
  me: WorkspaceMemberRecord;
  members: WorkspaceMemberRecord[];
  // Only present right after a code is issued: the server keeps just its hash.
  join_code?: string;
  access?: { token: string; expires_at_unix: number };
}

function summarizeWorkspace(workspace: WorkspaceDetails): WorkspaceSummary {
  return { id: workspace.id, name: workspace.name, frc_team_number: workspace.frc_team_number ?? null };
}

function summarizeMe(me: WorkspaceMemberRecord): WorkspaceMe {
  return { id: me.id, display_name: me.display_name, role: me.role };
}

const WORKSPACE_JSON = { "Content-Type": "application/json" };
const WORKSPACE_FETCH = { bypassCache: true, cacheTtlMs: 0 } as const;

// Every workspace route answers with the workspace, the caller and its members;
// create/join also carry a token. Keep the stored session in step either way.
async function applyWorkspaceResponse(res: Response): Promise<WorkspaceStateResponse> {
  if (!res.ok) throw new Error(await readError(res));
  const payload = (await res.json()) as WorkspaceStateResponse;
  if (payload.join_code && payload.workspace) {
    rememberIssuedJoinCode(payload.workspace.id, payload.join_code);
  }
  if (payload.access?.token) {
    await setWorkspaceSession({
      token: payload.access.token,
      expiresAt: payload.access.expires_at_unix,
      workspace: summarizeWorkspace(payload.workspace),
      me: summarizeMe(payload.me),
    });
  } else if (payload.workspace && payload.me) {
    const current = getWorkspaceSession();
    if (current && current.workspace.id === payload.workspace.id) {
      await setWorkspaceSession({ ...current, workspace: summarizeWorkspace(payload.workspace), me: summarizeMe(payload.me) });
    }
  }
  return payload;
}

export interface WorkspaceSignupPolicy {
  required: boolean;
  site_key: string | null;
}

export async function getWorkspaceSignupPolicy(): Promise<WorkspaceSignupPolicy> {
  const res = await apiFetch(`${API}/workspaces/signup-policy`, { method: "GET", cache: "no-store", ...WORKSPACE_FETCH });
  if (!res.ok) throw new Error(await readError(res));
  const payload = await res.json() as WorkspaceSignupPolicy;
  if (typeof payload.required !== 'boolean' || (payload.site_key !== null && typeof payload.site_key !== 'string')) {
    throw new Error('Workspace signup is temporarily unavailable. Try again later.');
  }
  return payload;
}

export async function createWorkspace(payload: {
  name: string;
  frc_team_number?: number | null;
  display_name: string;
  challenge_token?: string | null;
}): Promise<WorkspaceStateResponse> {
  const res = await apiFetch(`${API}/workspaces`, {
    method: "POST",
    headers: WORKSPACE_JSON,
    body: JSON.stringify(payload),
    ...WORKSPACE_FETCH,
  });
  return applyWorkspaceResponse(res);
}

export async function joinWorkspace(payload: { join_code: string; display_name: string }): Promise<WorkspaceStateResponse> {
  const res = await apiFetch(`${API}/workspaces/join`, {
    method: "POST",
    headers: WORKSPACE_JSON,
    body: JSON.stringify(payload),
    ...WORKSPACE_FETCH,
  });
  return applyWorkspaceResponse(res);
}

export async function getMyWorkspace(): Promise<WorkspaceStateResponse> {
  const res = await apiFetch(`${API}/workspaces/me`, { method: "GET", ...WORKSPACE_FETCH });
  return applyWorkspaceResponse(res);
}

export async function updateMyWorkspace(payload: {
  name?: string;
  frc_team_number?: number | null;
  clear_team_number?: boolean;
}): Promise<WorkspaceStateResponse> {
  const res = await apiFetch(`${API}/workspaces/me`, {
    method: "PATCH",
    headers: WORKSPACE_JSON,
    body: JSON.stringify(payload),
    ...WORKSPACE_FETCH,
  });
  return applyWorkspaceResponse(res);
}

export async function renameMeInWorkspace(displayName: string): Promise<WorkspaceStateResponse> {
  const res = await apiFetch(`${API}/workspaces/me/profile`, {
    method: "PATCH",
    headers: WORKSPACE_JSON,
    body: JSON.stringify({ display_name: displayName }),
    ...WORKSPACE_FETCH,
  });
  return applyWorkspaceResponse(res);
}

export async function rotateWorkspaceJoinCode(): Promise<WorkspaceStateResponse> {
  const res = await apiFetch(`${API}/workspaces/me/join-code`, { method: "POST", ...WORKSPACE_FETCH });
  return applyWorkspaceResponse(res);
}

export async function removeWorkspaceMember(memberId: number, rotateJoinCode = true): Promise<WorkspaceStateResponse> {
  const res = await apiFetch(`${API}/workspaces/me/members/${encodeURIComponent(memberId)}/remove`, {
    method: "POST",
    headers: WORKSPACE_JSON,
    body: JSON.stringify({ rotate_join_code: rotateJoinCode }),
    ...WORKSPACE_FETCH,
  });
  return applyWorkspaceResponse(res);
}

export async function setWorkspaceMemberRole(memberId: number, role: "leader" | "member"): Promise<WorkspaceStateResponse> {
  const res = await apiFetch(`${API}/workspaces/me/members/${encodeURIComponent(memberId)}/role`, {
    method: "POST",
    headers: WORKSPACE_JSON,
    body: JSON.stringify({ role }),
    ...WORKSPACE_FETCH,
  });
  return applyWorkspaceResponse(res);
}

export async function leaveWorkspace(confirmLastMember = false): Promise<void> {
  const res = await apiFetch(`${API}/workspaces/me/leave`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm_last_member: confirmLastMember }),
    bypassCache: true,
    cacheTtlMs: 0,
  });
  if (!res.ok) throw new Error(await readError(res));
  clearWorkspaceSession("left");
}

// ── Media helpers ──────────────────────────────────────────────

/** Resolve a backend-relative media path ("/media/...") to a fetchable URL. */
export function pitPhotoDisplayUrl(entry: Pick<PitScoutingEntry, "photos" | "photo_urls">, index: number): string {
  return resolveMediaUrl(entry.photo_urls?.[index] || entry.photos[index]);
}

export function resolveMediaUrl(path: string | null | undefined): string {
  const raw = String(path || "").trim();
  if (!raw) return "";
  return absoluteApiUrl(raw);
}

// ── Picklists ──────────────────────────────────────────────────

export type PicklistSlotTier = "first" | "second" | "dnp";
type PicklistSlotStatus = "available" | "picked" | "declined" | "captain";

export interface PicklistSlot {
  team_key: string;
  tier: PicklistSlotTier;
  status: PicklistSlotStatus;
  picked_by_alliance: number | null;
  dnp_reason: string;
  notes: string;
}

export interface Picklist {
  id: number;
  event_key: string;
  title: string;
  created_by: string | null;
  slots: PicklistSlot[];
  version: number;
  live_mode: boolean;
  archived: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface PicklistUpdateResult {
  ok: boolean;
  conflict?: boolean;
  detail?: string;
  picklist: Picklist;
}

export async function listPicklists(
  eventKey: string,
  includeArchived = false,
): Promise<{ ok: boolean; picklists: Picklist[] }> {
  const params = new URLSearchParams({ event_key: eventKey });
  if (includeArchived) params.set("include_archived", "true");
  const res = await apiFetch(`${API}/picklists?${params.toString()}`, {
    bypassCache: true,
    cacheTtlMs: 0,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean; picklists: Picklist[] };
}

export async function createPicklist(payload: {
  event_key: string;
  title?: string;
  created_by?: string;
  slots?: Partial<PicklistSlot>[];
}): Promise<{ ok: boolean; picklist: Picklist }> {
  const res = await apiFetch(`${API}/picklists`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean; picklist: Picklist };
}

export async function getPicklist(picklistId: number): Promise<{ ok: boolean; picklist: Picklist }> {
  const res = await apiFetch(`${API}/picklists/${encodeURIComponent(picklistId)}`, {
    bypassCache: true,
    cacheTtlMs: 0,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean; picklist: Picklist };
}

export async function updatePicklist(
  picklistId: number,
  payload: {
    version: number;
    slots?: PicklistSlot[];
    title?: string;
    live_mode?: boolean;
    archived?: boolean;
  },
): Promise<PicklistUpdateResult> {
  const res = await apiFetch(`${API}/picklists/${encodeURIComponent(picklistId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as PicklistUpdateResult;
}

export type StrategyStroke = {
  tool: 'pen' | 'arrow' | 'zone';
  color: 'ours' | 'theirs' | 'note';
  // Field fractions: x 0 (left) to 1 (right), y 0 (top) to 1 (bottom) as drawn.
  points: Array<[number, number]>;
};

export interface StrategyBoard {
  match_key: string;
  strokes: StrategyStroke[];
  // 0 for a board nobody has drawn on yet.
  version: number;
  updated_by: string | null;
  updated_at: string | null;
}

export async function getStrategyBoard(matchKey: string): Promise<StrategyBoard> {
  const res = await apiFetch(`${API}/strategy-boards/${encodeURIComponent(matchKey)}`, { bypassCache: true, cacheTtlMs: 0 });
  if (!res.ok) throw new Error(await readError(res));
  return ((await res.json()) as { board: StrategyBoard }).board;
}

// A stale `version` comes back as { conflict: true, board: <theirs> } rather than overwriting it.
export type SaveStrategyBoardResult = { ok: boolean; conflict?: boolean; board: StrategyBoard };

export async function saveStrategyBoard(
  matchKey: string,
  version: number,
  strokes: StrategyStroke[],
): Promise<SaveStrategyBoardResult> {
  const res = await apiFetch(`${API}/strategy-boards/${encodeURIComponent(matchKey)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version, strokes }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as SaveStrategyBoardResult;
}

export async function deletePicklist(picklistId: number): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${API}/picklists/${encodeURIComponent(picklistId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean };
}

// ── Pit scouting ───────────────────────────────────────────────

export interface PitScoutingEntry {
  id: number;
  event_key: string;
  team_key: string;
  scout_profile: string | null;
  payload: Record<string, unknown>;
  photos: string[];
  // Signed display links, aligned with `photos` (the stored paths, used to delete).
  photo_urls?: string[];
  created_at: string | null;
  updated_at: string | null;
}

export async function listPitEntries(
  eventKey: string,
): Promise<{ ok: boolean; entries: PitScoutingEntry[] }> {
  const params = new URLSearchParams({ event_key: eventKey });
  const res = await apiFetch(`${API}/pit-scouting?${params.toString()}`, {
    bypassCache: true,
    cacheTtlMs: 0,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean; entries: PitScoutingEntry[] };
}

export async function upsertPitEntry(payload: {
  event_key: string;
  team_key: string;
  scout_profile?: string;
  payload: Record<string, unknown>;
}): Promise<{ ok: boolean; entry: PitScoutingEntry }> {
  const res = await apiFetch(`${API}/pit-scouting`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean; entry: PitScoutingEntry };
}

export async function uploadPitPhoto(payload: {
  event_key: string;
  team_key: string;
  scout_profile?: string;
  image_base64: string;
}): Promise<{ ok: boolean; photo: string; entry: PitScoutingEntry }> {
  const res = await apiFetch(`${API}/pit-scouting/photo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 45000,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean; photo: string; entry: PitScoutingEntry };
}

export async function deletePitPhoto(payload: {
  event_key: string;
  team_key: string;
  photo_path: string;
}): Promise<{ ok: boolean; entry: PitScoutingEntry }> {
  const res = await apiFetch(`${API}/pit-scouting/photo/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean; entry: PitScoutingEntry };
}

// ── Scouting insights (coverage / leaderboard / raw export) ────

interface CoverageSlot {
  team_key: string;
  alliance: string;
  station: string | null;
  entry_count: number;
  scouts: string[];
}

interface CoverageMatchRow {
  match_key: string;
  label: string;
  comp_level: string;
  time: number | null;
  slots: CoverageSlot[];
}

interface CoverageLeaderboardRow {
  scout_profile: string;
  entry_count: number;
  matches_covered: number;
  teams_covered: number;
  best_qual_streak: number;
  last_entry_at: string | null;
}

interface CoverageOutlier {
  kind: "scout_disagreement" | "anomalous_entry";
  match_key: string;
  team_key: string;
  detail: string;
  scouts: string[];
}

export interface ScoutingCoverageResponse {
  ok: boolean;
  event_key: string;
  summary: {
    total_slots: number;
    covered_slots: number;
    coverage_pct: number;
    total_entries: number;
    scout_count: number;
    outlier_count: number;
  };
  grid: CoverageMatchRow[];
  leaderboard: CoverageLeaderboardRow[];
  outliers: CoverageOutlier[];
}

export async function getScoutingCoverage(eventKey: string): Promise<ScoutingCoverageResponse> {
  const params = new URLSearchParams({ event_key: eventKey });
  const res = await apiFetch(`${API}/scouting/insights/coverage?${params.toString()}`, {
    cacheTtlMs: 15000,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingCoverageResponse;
}

export interface TeamScoutNote {
  id: number;
  event_key: string | null;
  match_key: string | null;
  scout_profile: string;
  notes: string;
  tags: string[];
  saved_at_ms: number | null;
  created_at: string | null;
}

export interface TeamNotesResponse {
  ok: boolean;
  team_key: string;
  event_key: string | null;
  reports: number;
  tag_counts: Array<{ tag: string; count: number }>;
  notes: TeamScoutNote[];
}

// What your workspace's scouts wrote and tagged about one team (one event, or the season).
export async function getTeamNotes(teamKey: string, eventKey?: string): Promise<TeamNotesResponse> {
  const params = new URLSearchParams({ team_key: teamKey });
  if (eventKey) params.set('event_key', eventKey);
  const res = await apiFetch(`${API}/scouting/insights/team-notes?${params.toString()}`, {
    cacheTtlMs: 15000,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TeamNotesResponse;
}

export interface ExportedRoomEntry {
  server_id: number;
  room_key: string;
  event_key: string | null;
  match_key: string | null;
  team_key: string | null;
  scout_profile: string;
  client_entry_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  entry: Record<string, unknown>;
  scores: {
    total_points: number | null;
    driver_score_0_100: number | null;
    manual_rating_0_100: number | null;
    scouting_api_rating_0_100: number | null;
    overall_scout_rating_0_100: number | null;
  };
}

export async function exportEventScoutingEntries(
  eventKey: string,
): Promise<{ ok: boolean; count: number; entries: ExportedRoomEntry[] }> {
  const params = new URLSearchParams({ event_key: eventKey });
  const res = await apiFetch(`${API}/scouting/insights/entries-export?${params.toString()}`, {
    bypassCache: true,
    cacheTtlMs: 0,
    timeoutMs: 45000,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean; count: number; entries: ExportedRoomEntry[] };
}

// ── Web push notifications ─────────────────────────────────────

export async function getPushPublicKey(): Promise<{
  ok: boolean;
  configured: boolean;
  public_key: string | null;
  detail?: string;
}> {
  const res = await apiFetch(`${API}/push/public-key`, { cacheTtlMs: 60000 });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as {
    ok: boolean;
    configured: boolean;
    public_key: string | null;
    detail?: string;
  };
}

export async function subscribePush(payload: {
  endpoint: string;
  keys: { p256dh: string; auth: string };
  event_key?: string | null;
  team_keys?: string[];
  prefs?: {
    match_lead_minutes?: number;
    shift_alerts?: boolean;
    scout_profile?: string;
    room_key?: string;
  };
}): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${API}/push/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean };
}

export async function unsubscribePush(endpoint: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${API}/push/unsubscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean };
}

export async function sendPushTest(endpoint: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${API}/push/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as { ok: boolean };
}

// Native admin/room access is tab-scoped memory, not WebView sessionStorage.
export function bootstrapNativeEphemeralAccess(): void {
  if (!isNativeApp()) return;
  for (const key of Object.keys(window.sessionStorage)) {
    if (key === ADMIN_SESSION_TOKEN_STORAGE || key === ADMIN_SESSION_EXPIRES_AT_STORAGE
      || key.startsWith(ROOM_ACCESS_TOKEN_STORAGE_PREFIX) || key.startsWith(ROOM_ACCESS_EXPIRES_AT_STORAGE_PREFIX)) {
      const value = window.sessionStorage.getItem(key);
      if (value) nativeSessionValues.set(key, value);
      window.sessionStorage.removeItem(key);
    }
  }
}
