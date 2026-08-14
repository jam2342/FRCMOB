const CONFIGURED_API_URL = String(
  import.meta.env.VITE_API_URL || import.meta.env.NEXT_PUBLIC_API_URL || "",
).trim();
const CONFIGURED_WS_URL = String(
  import.meta.env.VITE_WS_URL || import.meta.env.NEXT_PUBLIC_WS_URL || "",
).trim();

function resolveApiBaseUrl(): string {
  const fallback = import.meta.env.PROD ? "/api" : "http://localhost:8000";
  let base = CONFIGURED_API_URL || fallback;

  // Prevent mixed-content failures when frontend is served over HTTPS.
  // In that case, force same-origin API proxy path.
  if (typeof window !== "undefined") {
    if (window.location.protocol === "https:" && /^http:\/\//i.test(base)) {
      base = "/api";
    }
  }

  return base.replace(/\/+$/, "");
}

const API = resolveApiBaseUrl();

function resolveWebSocketBaseUrl(): string {
  const base = CONFIGURED_WS_URL || API;
  return String(base || "").trim().replace(/\/+$/, "");
}

const WS_API = resolveWebSocketBaseUrl();
const REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 12000);
import { enqueue as enqueueOffline } from "./utils/offlineQueue";
import type { AutoScoutDraftRecord } from "./pages/scoutingPage.types";
const DEFAULT_GET_CACHE_TTL_MS = Number(import.meta.env.VITE_API_CACHE_TTL_MS || 8000);
const DEFAULT_GET_STALE_WHILE_REVALIDATE_MS = Number(import.meta.env.VITE_API_STALE_WHILE_REVALIDATE_MS || 45000);
const PERSIST_GET_CACHE_TTL_MS = Number(import.meta.env.VITE_API_PERSIST_CACHE_TTL_MS || 21600000);
const PERSIST_GET_CACHE_MAX_ENTRIES = Number(import.meta.env.VITE_API_PERSIST_CACHE_MAX_ENTRIES || 120);
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

function readSessionString(key: string): string {
  if (typeof window === "undefined") return "";
  try {
    return String(window.sessionStorage.getItem(key) || "").trim();
  } catch {
    return "";
  }
}

function writeSessionString(key: string, value: string): void {
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
};

type ApiRequestOptions = Pick<ApiFetchInit, "cacheTtlMs" | "staleWhileRevalidateMs" | "bypassCache" | "timeoutMs">;

type CachedResponse = {
  storedAtMs: number;
  expiresAtMs: number;
  response: Response;
};

type PersistentCachedResponse = {
  storedAtMs: number;
  expiresAtMs: number;
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
const inflightGetRequests = new Map<string, Promise<Response>>();
const PERSIST_CACHE_PREFIX = "scouting_api_cache_v1:";
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

type DataFreshness = {
  is_outdated: boolean;
  outdated_days_threshold: number;
  latest_match_time: number | null;
  latest_match_age_days: number | null;
  warnings: string[];
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
  data_freshness?: DataFreshness;
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
  data_freshness?: DataFreshness;
  ml_projection?: {
    available: boolean;
    model_key: string;
    model_version: string | null;
    source: string | null;
    source_event_key: string | null;
    predicted_active_bps: number | null;
    predicted_scores_per_min: number | null;
    reason: string | null;
    detail: string | null;
    input?: {
      rating_0_100: number | null;
      throughput_0_100: number | null;
      confidence_0_1: number | null;
      match_count: number;
      coverage_factor_0_1: number | null;
    } | null;
  };
  matches_analyzed: number;
  averages: MetricAverages | null;
  metric_units?: Record<string, string>;
  climb_sources?: {
    overall_climb_success_prob: number | null;
    video_only: ClimbSourceSummary;
    official_score_breakdown: ClimbSourceSummary;
    level_capability?: ClimbLevelCapability;
  };
  metric_coverage?: Record<string, MetricCoverageInfo>;
  active_perimeter_type?: "welded" | "andymark" | null;
  perimeter_types?: Array<"welded" | "andymark">;
  perimeter_sources?: string[];
  analysis_versions: string[];
  event_type_counts: Array<{ event_type: string; count: number }>;
  zone_time_sec: Record<string, number>;
  recent_matches: Array<
    TeamHistoryFinding & {
      analysis_run_id: number;
      source_version: string;
    }
  >;
  recent_events: Array<{
    id: number;
    analysis_run_id: number;
    match_key: string;
    event_key: string;
    track_id: number | null;
    time_sec: number;
    frame_index: number | null;
    event_type: string;
    confidence: number | null;
    field_x: number | null;
    field_y: number | null;
    meta: Record<string, unknown>;
  }>;
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
  run_ids: number[];
};

type RatingSignalEvidence = {
  match_key: string;
  metric: string;
  value: number;
  clip_url: string | null;
};

type RatingSignal = {
  label: string;
  metric_value: number;
  percentile: number;
  evidence: RatingSignalEvidence[];
};

export type EventTeamRatingItem = {
  event_key: string;
  team_key: string;
  team_number: number | null;
  nickname: string | null;
  rating_0_100: number;
  confidence_0_1: number;
  robot_level_0_100: number;
  driver_skill_0_100: number;
  subscores: {
    results_anchor: number;
    throughput: number;
    shift_productivity: number;
    capacity_utilization: number;
    endgame: number;
    auto_contribution?: number | null;
    manual_points_impact?: number | null;
    rp_contribution?: number | null;
    defense_presence?: number | null;
    consistency: number;
    penalty_discipline?: number | null;
  };
  pros: RatingSignal[];
  cons: RatingSignal[];
  evidence: RatingSignalEvidence[];
  details: Record<string, unknown>;
  model_version: string;
  updated_at: string | null;
  rating_trend?: RatingTrend | null;
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

type ScheduleSynergyPairBreakdown = {
  team_key_a: string;
  team_key_b: string;
  base_synergy_points?: number;
  role_adjustment_points?: number;
  complement_bonus_points?: number;
  risk_penalty_points?: number;
  synergy_points: number;
  confidence: number;
  role_profile_confidence_0_1?: number;
  role_profile_coverage_0_1?: number;
  role_axes?: Record<string, number>;
  source: "projection" | "blended" | "measured";
  prior_n: number;
  event_n: number;
};

type ScheduleSynergyAlliance = {
  available: boolean;
  alliance_synergy_score_0_100: number | null;
  alliance_synergy_points: number;
  expected_throughput: number | null;
  projected_throughput: number | null;
  confidence_0_1: number;
  source_label: "projection" | "blended" | "measured";
  pair_breakdown: ScheduleSynergyPairBreakdown[];
  computed_at?: string | null;
};

type MatchPredictionPayload = {
  available: boolean;
  source_label: string;
  model_key: string;
  model_version: string | null;
  red_win_prob: number | null;
  blue_win_prob: number | null;
  favored_alliance: 'red' | 'blue' | null;
  edge_confidence_0_1: number | null;
};

export type ScheduleWithSynergyMatch = {
  match_key: string;
  comp_level: string;
  set_number: number;
  match_number: number;
  scheduled_time: number | null;
  red: {
    teams: EventScheduleTeam[];
    synergy: ScheduleSynergyAlliance;
  };
  blue: {
    teams: EventScheduleTeam[];
    synergy: ScheduleSynergyAlliance;
  };
  prediction?: MatchPredictionPayload;
};

export type EventScheduleWithSynergyResponse = {
  ok: boolean;
  event_key: string;
  event_name: string | null;
  model_version: string;
  projection_count: number;
  ml_prediction_model_version?: string | null;
  ml_prediction_count?: number;
  count: number;
  precompute?: Record<string, unknown> | null;
  matches: ScheduleWithSynergyMatch[];
};

type MatchPhaseWindow = {
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  start_unix: number | null;
  end_unix: number | null;
  confidence_0_1?: number;
};

export type MatchPhasesResponse = {
  ok: boolean;
  match_key: string;
  event_key: string;
  time: number | null;
  source: string;
  config_season: number;
  config_version: string;
  total_sec: number;
  windows: {
    auto: MatchPhaseWindow;
    teleop: MatchPhaseWindow;
    teleop_scoring: MatchPhaseWindow;
    endgame: MatchPhaseWindow;
  };
};

// ── Robot tracking / heatmap types ───────────────────���──────────────

export type TrackRow = {
  id: number;
  track_id: number;
  team_key: string | null;
  frame_index: number;
  time_sec: number;
  bbox: [number, number, number, number]; // [x1, y1, x2, y2]
  centroid: [number, number];
  field_x: number | null;
  field_y: number | null;
  zone_key: string | null;
  speed_mps: number | null;
  confidence: number | null;
};

export type MatchTracksResponse = {
  ok: boolean;
  match_key: string;
  event_key: string;
  video_url: string | null;
  local_video_url: string | null;
  field_length_m: number;
  field_width_m: number;
  calibration: {
    image_width: number;
    image_height: number;
    homography: number[][];
    image_points: { x: number; y: number }[];
    field_points: { x: number; y: number }[];
    frame_time_sec: number | null;
  } | null;
  team_keys: string[];
  total_rows: number;
  tracks: TrackRow[];
};

export type TeamHeatmapResponse = {
  ok: boolean;
  team_key: string;
  event_key: string;
  match_key: string | null;
  total_points: number;
  match_count: number;
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
  statbotics: Record<string, unknown>;
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

type TheoreticalAllianceSignal = {
  label?: string;
  metric_value?: number;
  percentile?: number;
} & Record<string, unknown>;

type TheoreticalAllianceTeamSummary = {
  team_key: string;
  rating_0_100: number | null;
  model_confidence_0_1: number | null;
  compatibility_points: number;
  compatibility_avg_pair_points: number;
  compatibility_score_0_100: number;
  pros_score_0_100: number;
  cons_risk_0_100: number;
  weighted_score_0_100: number;
  selection_rank?: number | null;
  selection_strength_score?: number | null;
  selection_strength_source?: string | null;
  selection_desirability?: number | null;
  selection_pick_probability_0_1?: number | null;
  selection_is_captain?: boolean;
  pros_top: TheoreticalAllianceSignal[];
  cons_top: TheoreticalAllianceSignal[];
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

export type TheoreticalAllianceResponse = {
  ok: boolean;
  event_key: string;
  team_keys: string[];
  weights: {
    compatibility_weight: number;
    pros_weight: number;
    cons_weight: number;
  };
  compatibility: {
    alliance_synergy_points: number;
    compatibility_score_0_100: number;
    confidence_0_1: number;
    source_label: "projection" | "blended" | "measured";
    pair_breakdown: Array<{
      team_key_a: string;
      team_key_b: string;
      base_synergy_points?: number;
      role_adjustment_points?: number;
      complement_bonus_points?: number;
      risk_penalty_points?: number;
      synergy_points: number;
      confidence: number;
      role_profile_confidence_0_1?: number;
      role_profile_coverage_0_1?: number;
      role_axes?: Record<string, number>;
      source: "projection" | "blended" | "measured";
      prior_n: number;
      event_n: number;
    }>;
  };
  pros_cons: {
    alliance_pros_score_0_100: number;
    alliance_cons_risk_0_100: number;
  };
  weighted_total_score_0_100: number;
  selection_model?: TheoreticalSelectionModel | null;
  teams: TheoreticalAllianceTeamSummary[];
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
  queue: {
    ok: boolean;
    counts?: {
      queued: number;
      started: number;
      deferred: number;
      scheduled: number;
      failed: number;
      pending_total: number;
    };
    caps?: {
      max_pending_jobs: number;
      max_schedule_per_call: number;
    };
    pressure_0_1?: number;
    detail?: string;
  };
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
    videos_gb?: number;
    analysis_frames_gb?: number;
    disk_free_gb?: number;
  };
  climb_integrity: {
    enabled: boolean;
    last_result_available: boolean;
    last_result?: {
      severity?: string;
      totals?: {
        compared_pairs?: number;
        mismatched_pairs?: number;
        mismatch_rate?: number | null;
      };
    } | null;
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

async function readError(response: Response): Promise<string> {
  const fallback = `Request failed with status ${response.status}`;
  if (response.bodyUsed) return fallback;
  let bodyText = "";
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
  if (/body stream already read/i.test(trimmed)) return fallback;

  try {
    const data = JSON.parse(trimmed) as { detail?: unknown; message?: unknown };
    if (typeof data.detail === "string" && data.detail.trim()) return data.detail.trim();
    if (typeof data.message === "string" && data.message.trim()) return data.message.trim();
  } catch {
    // Non-JSON payloads should surface as raw text.
  }
  return trimmed;
}

function absoluteApiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${API}${path}`;
}

function normalizeMethod(method: string | undefined): string {
  return (method || "GET").toUpperCase();
}

function shouldBypassCache(url: string, init?: ApiFetchInit): boolean {
  if (init?.bypassCache) return true;
  const method = normalizeMethod(init?.method);
  if (method !== "GET") return true;
  if (url.includes("refresh=true")) return true;
  if (url.includes("onlyModifiedSince=")) return true;
  return false;
}

function cacheKeyFor(url: string, init?: ApiFetchInit): string {
  return `${normalizeMethod(init?.method)}:${url}`;
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
  };
}

type TimeoutState = {
  didTimeout: boolean;
};

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

function prunePersistentCache(maxEntries: number) {
  if (!localStorageAvailable() || maxEntries <= 0) return;
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
        const expiresAtMs = Number(parsed.expiresAtMs || 0);
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
      window.localStorage.removeItem(storageKey);
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

// Builds an offline-stale Response from the age-agnostic cache and signals the
// UI (via a window event) that the user is now looking at last-session data.
function offlineStaleFallback(requestKey: string): Response | null {
  const hit = readCacheAnyAge(requestKey);
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

async function writePersistentGetCache(requestKey: string, response: Response, ttlMs: number): Promise<void> {
  if (!localStorageAvailable()) return;
  if (ttlMs <= 0) return;
  try {
    const bodyText = await response.text();
    if (bodyText.length > PERSIST_GET_CACHE_MAX_BODY_BYTES) return;
    const payload: PersistentCachedResponse = {
      storedAtMs: Date.now(),
      expiresAtMs: Date.now() + ttlMs,
      status: response.status,
      statusText: response.statusText,
      headers: Array.from(response.headers.entries()),
      bodyText,
    };
    window.localStorage.setItem(persistentCacheKey(requestKey), JSON.stringify(payload));
    prunePersistentCache(Math.max(20, Math.floor(PERSIST_GET_CACHE_MAX_ENTRIES)));
  } catch {
    // ignore cache write failures (quota, unavailable, serialization)
  }
}

async function apiFetch(input: string, init?: ApiFetchInit): Promise<Response> {
  const normalizedInput = String(input || "");
  const hasAbsoluteScheme = normalizedInput.startsWith("http://") || normalizedInput.startsWith("https://");
  const isRootRelative = normalizedInput.startsWith("/");
  const url = hasAbsoluteScheme || isRootRelative ? normalizedInput : `${API}/${normalizedInput.replace(/^\/+/, "")}`;
  const method = normalizeMethod(init?.method);
  const bypassCache = shouldBypassCache(url, init);
  const cacheTtlMs = bypassCache ? 0 : _cacheValueInt(init?.cacheTtlMs, DEFAULT_GET_CACHE_TTL_MS);
  const staleWhileRevalidateMs = bypassCache
    ? 0
    : _cacheValueInt(init?.staleWhileRevalidateMs, DEFAULT_GET_STALE_WHILE_REVALIDATE_MS);
  const shouldUseCache = method === "GET" && cacheTtlMs > 0;
  const requestKey = cacheKeyFor(url, init);
  const now = Date.now();
  const timeoutMs = Math.max(2000, Math.floor(init?.timeoutMs ?? REQUEST_TIMEOUT_MS));
  let staleCandidate: CacheLookupResult | null = null;

  if (shouldUseCache) {
    const memCached = readMemoryGetCache(requestKey, now, staleWhileRevalidateMs);
    if (memCached && memCached.state === "fresh") {
      return memCached.response.clone();
    }
    if (memCached && memCached.state === "stale") {
      staleCandidate = memCached;
    }
    const persisted = readPersistentGetCache(requestKey, now, staleWhileRevalidateMs);
    if (persisted) {
      const persistedStoredAtMs = Math.max(0, now - persisted.ageSec * 1000);
      responseCache.set(requestKey, {
        storedAtMs: persistedStoredAtMs,
        response: persisted.response.clone(),
        expiresAtMs: persisted.state === "fresh" ? now + cacheTtlMs : now - 1,
      });
      if (persisted.state === "fresh") {
        if (ENABLE_REQUEST_LOGS) {
          console.debug(`[api] ${method} ${url} -> persisted-cache hit`);
        }
        return persisted.response.clone();
      }
      if (!staleCandidate) {
        staleCandidate = persisted;
      }
    }
  }

  if (shouldUseCache) {
    const inFlight = inflightGetRequests.get(requestKey);
    if (inFlight) {
      if (staleCandidate) {
        if (ENABLE_REQUEST_LOGS) {
          console.debug(`[api] ${method} ${url} -> stale-cache hit (inflight refresh)`);
        }
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
    const offlineStale = offlineStaleFallback(requestKey);
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
  const start = performance.now();
  const run = async (forBackground = false) => {
    const timeoutState: TimeoutState = { didTimeout: false };
    const requestSignal = withTimeout(forBackground ? undefined : init?.signal, timeoutMs, timeoutState);
    let response: Response;
    try {
      response = await fetch(url, {
        ...init,
        method,
        headers,
        signal: requestSignal,
      });
    } catch (error) {
      throw normalizeApiFetchError(error, timeoutMs, timeoutState, requestSignal);
    }
    if (ENABLE_REQUEST_LOGS) {
      const tookMs = Math.round(performance.now() - start);
      const cacheStatus = shouldUseCache ? "cacheable" : "direct";
      console.debug(`[api] ${method} ${url} -> ${response.status} (${tookMs}ms, ${cacheStatus})`);
    }
    if (shouldUseCache && response.ok) {
      responseCache.set(requestKey, {
        storedAtMs: Date.now(),
        response: response.clone(),
        expiresAtMs: Date.now() + cacheTtlMs,
      });
      const persistentTtlMs = Math.max(cacheTtlMs, Math.max(0, Math.floor(PERSIST_GET_CACHE_TTL_MS)));
      void writePersistentGetCache(requestKey, response.clone(), persistentTtlMs);
    }
    return response;
  };

  if (!shouldUseCache) {
    try {
      return await run();
    } catch (err) {
      // If offline and this is a mutation, queue it for later replay.
      if (method !== "GET" && !navigator.onLine) {
        const headerEntries: Record<string, string> = {};
        headers.forEach((v, k) => { headerEntries[k] = v; });
        enqueueOffline({
          url,
          method,
          body: typeof init?.body === "string" ? init.body : null,
          headers: headerEntries,
          label: `${method} ${new URL(url, window.location.origin).pathname}`,
        });
        throw new Error("You are offline. The request has been queued and will be sent when you reconnect.");
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
    const backgroundPromise = run(true)
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
  try {
    const response = await promise;
    return response.clone();
  } catch (err) {
    // Network fetch failed (offline / DNS / connection refused): fall back to
    // the last data we ever saw, regardless of age, as a last resort.
    const offlineStale = offlineStaleFallback(requestKey);
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
  const res = await apiFetch(`${API}/health`);
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

export async function runClimbOfficialBackfill(options?: {
  event_key?: string;
  max_events?: number;
}): Promise<{
  ok: boolean;
  result: {
    ok: boolean;
    event_count: number;
    totals: {
      official_rows: number;
      updated_findings: number;
      inserted_findings: number;
      inserted_runs: number;
      errored_events: number;
    };
  };
}> {
  const params = new URLSearchParams();
  if (typeof options?.event_key === "string" && options.event_key.trim()) {
    params.set("event_key", options.event_key.trim().toLowerCase());
  }
  if (typeof options?.max_events === "number") {
    params.set("max_events", String(Math.max(1, Math.min(40, Math.round(options.max_events)))));
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const res = await apiFetch(`${API}/maintenance/climb/official-backfill/run${suffix}`, {
    method: "POST",
    bypassCache: true,
    cacheTtlMs: 0,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as {
    ok: boolean;
    result: {
      ok: boolean;
      event_count: number;
      totals: {
        official_rows: number;
        updated_findings: number;
        inserted_findings: number;
        inserted_runs: number;
        errored_events: number;
      };
    };
  };
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
  synergy_precompute?: {
    triggered: boolean;
    ok: boolean;
    detail?: string | null;
    model_version?: string | null;
    projection_count?: number;
  };
}> {
  const res = await apiFetch(`${API}/events/${eventKey}/ingest`, { method: "POST" });
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
    synergy_precompute?: {
      triggered: boolean;
      ok: boolean;
      detail?: string | null;
      model_version?: string | null;
      projection_count?: number;
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
  preferredYear = 2026,
  fallbackYear = 2025,
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

  const params = new URLSearchParams({
    preferred_year: String(preferredYear),
    fallback_year: String(fallbackYear),
    limit: String(normalizedLimit),
    prioritize_running: String(options?.preferLiveNow ?? true),
  });
  if (typeof options?.remoteTeamCountFetchLimit === 'number') {
    params.set('team_count_fetch_limit', String(Math.max(0, Math.floor(options.remoteTeamCountFetchLimit))));
  }
  try {
    const res = await apiFetch(
      `${API}/events/suggested?${params.toString()}`,
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
    return payload;
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
  // Fast UI mode: avoid expensive global/team-registration expansion in interactive search.
  const params = new URLSearchParams({
    q,
    limit: String(limit),
    include_global: "false",
    include_event_registrations: "false",
    preferred_year: "2026",
    fallback_year: "2025",
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
    preferred_year: "2026",
    fallback_year: "2025",
    v: TEAM_MEDIA_CACHE_VERSION,
  });
  if (eventKey) params.set("event_key", eventKey);
  const res = await apiFetch(
    `${API}/teams/${teamKey}/robot-image?${params.toString()}`,
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
    preferred_year: "2026",
    fallback_year: "2025",
    v: TEAM_MEDIA_CACHE_VERSION,
  });
  if (eventKey) params.set("event_key", eventKey);
  const res = await apiFetch(
    `${API}/teams/${teamKey}/logo?${params.toString()}`,
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
    include_statbotics?: boolean;
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
  if (typeof options?.include_statbotics === "boolean") {
    params.set("include_statbotics", String(options.include_statbotics));
  }
  if (typeof options?.allow_season_fallback === "boolean") {
    params.set("allow_season_fallback", String(options.allow_season_fallback));
  }
  if (typeof options?.auto_heal_ratings === "boolean") {
    if (!options.auto_heal_ratings || canUseAdminReadFlags) {
      params.set("auto_heal_ratings", String(options.auto_heal_ratings));
    }
  }
  if (typeof options?.refresh === "boolean") {
    if (!options.refresh || canUseAdminReadFlags) {
      params.set("refresh", String(options.refresh));
    }
  }
  const makeSuffix = (query: URLSearchParams) => (query.toString() ? `?${query.toString()}` : "");
  const resolvedRequestOptions = _cacheRequestOptions(requestOptions, {
    cacheTtlMs: TEAM_INTEL_CACHE_TTL_MS,
    staleWhileRevalidateMs: TEAM_INTEL_CACHE_TTL_MS * 3,
  });
  let res = await apiFetch(`${API}/teams/${teamKey}/intel${makeSuffix(params)}`, resolvedRequestOptions);
  if (
    (res.status === 401 || res.status === 403) &&
    ((params.get("auto_heal_ratings") || "").toLowerCase() === "true" ||
      (params.get("refresh") || "").toLowerCase() === "true")
  ) {
    const fallbackParams = new URLSearchParams(params);
    fallbackParams.delete("auto_heal_ratings");
    fallbackParams.delete("refresh");
    const fallbackRes = await apiFetch(
      `${API}/teams/${teamKey}/intel${makeSuffix(fallbackParams)}`,
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
    include_statbotics?: boolean;
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
  if (typeof options?.include_statbotics === "boolean") {
    params.set("include_statbotics", String(options.include_statbotics));
  }
  if (typeof options?.auto_heal_ratings === "boolean") {
    if (!options.auto_heal_ratings || canUseAdminReadFlags) {
      params.set("auto_heal_ratings", String(options.auto_heal_ratings));
    }
  }
  if (typeof options?.include_season_fallback === "boolean") {
    params.set("include_season_fallback", String(options.include_season_fallback));
  }
  if (typeof options?.include_rating_details === "boolean") {
    params.set("include_rating_details", String(options.include_rating_details));
  }
  if (typeof options?.include_rating_signals === "boolean") {
    params.set("include_rating_signals", String(options.include_rating_signals));
  }
  if (typeof options?.refresh === "boolean") {
    if (!options.refresh || canUseAdminReadFlags) {
      params.set("refresh", String(options.refresh));
    }
  }
  const makeSuffix = (query: URLSearchParams) => (query.toString() ? `?${query.toString()}` : "");
  const resolvedRequestOptions = _cacheRequestOptions(requestOptions, {
    cacheTtlMs: EVENT_INTEL_CACHE_TTL_MS,
    staleWhileRevalidateMs: EVENT_INTEL_CACHE_TTL_MS * 3,
  });
  let res = await apiFetch(
    `${API}/teams/event/${eventKey}/intel${makeSuffix(params)}`,
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
      `${API}/teams/event/${eventKey}/intel${makeSuffix(fallbackParams)}`,
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
): Promise<EventTeamsHistoryResponse> {
  const params = new URLSearchParams({
    history_limit: String(historyLimit),
    exclude_current_event: String(excludeCurrentEvent),
  });
  const res = await apiFetch(
    `${API}/scouting/event/${eventKey}/teams/history?${params.toString()}`,
    _cacheRequestOptions(undefined, {
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
    `${API}/scouting/team/${teamKey}/breakdown?${params.toString()}`,
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
  options?: { includeTrend?: boolean },
): Promise<EventRatingsResponse> {
  const params = new URLSearchParams({ refresh: String(refresh) });
  if (options?.includeTrend) params.set("include_trend", "true");
  const res = await apiFetch(
    `${API}/scouting/event/${eventKey}/ratings?${params.toString()}`,
    _cacheRequestOptions(undefined, {
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
  const params = new URLSearchParams({ refresh: "false", include_trend: "true" });
  const res = await apiFetch(
    `${API}/scouting/event/${eventKey}/ratings?${params.toString()}`,
    _cacheRequestOptions(undefined, { bypassCache: true }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventRatingsResponse;
}

export async function getTheoreticalAlliance(
  eventKey: string,
  payload: {
    team_keys: string[];
    compatibility_weight: number;
    pros_weight: number;
    cons_weight: number;
    include_selection_model?: boolean;
    selection_rank_weight?: number;
    selection_scale?: number;
    selection_captains?: number;
    selection_simulations?: number;
    selection_rank_source?: "auto" | "tba" | "model";
    model_version?: string;
    quality_threshold?: number;
    auto_precompute?: boolean;
  },
): Promise<TheoreticalAllianceResponse> {
  const res = await apiFetch(`${API}/synergy/event/${eventKey}/theoretical-alliance`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as TheoreticalAllianceResponse;
}

export async function getMatchPhases(matchKey: string): Promise<MatchPhasesResponse> {
  const res = await apiFetch(
    `${API}/matches/${matchKey}/phases`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: EVENT_LIVE_FORM_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_LIVE_FORM_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as MatchPhasesResponse;
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
    `${API}/matches/event/${eventKey}/schedule?${params.toString()}`,
    _cacheRequestOptions(requestOptions, {
      cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_SCHEDULE_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventScheduleResponse;
}

export async function getEventLiveStream(eventKey: string): Promise<EventLiveStreamResponse> {
  const res = await apiFetch(
    `${API}/matches/event/${eventKey}/live-stream`,
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
    `${API}/matches/event/${eventKey}/team-live-form${suffix}`,
    _cacheRequestOptions(requestOptions, {
      cacheTtlMs: EVENT_LIVE_FORM_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_LIVE_FORM_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventTeamLiveFormResponse;
}

export async function getEventScheduleWithSynergy(
  eventKey: string,
  options?: {
    model_version?: string;
    include_pair_breakdown?: boolean;
    refresh?: boolean;
  },
): Promise<EventScheduleWithSynergyResponse> {
  const params = new URLSearchParams();
  if (options?.model_version) params.set("model_version", options.model_version);
  if (typeof options?.include_pair_breakdown === "boolean") {
    params.set("include_pair_breakdown", String(options.include_pair_breakdown));
  }
  if (typeof options?.refresh === "boolean") {
    if (!options.refresh || clientAdminKeyAvailable()) {
      params.set("refresh", String(options.refresh));
    } else {
      params.set("refresh", "false");
    }
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const res = await apiFetch(
    `${API}/events/${eventKey}/schedule-with-synergy${suffix}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventScheduleWithSynergyResponse;
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
    `${API}/matches/event/${eventKey}/alliances${suffix}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_SCHEDULE_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventAlliancesResponse;
}

export async function getEventRankings(eventKey: string): Promise<EventRankingsResponse> {
  const res = await apiFetch(
    `${API}/events/${eventKey}/rankings`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: EVENT_SCHEDULE_CACHE_TTL_MS,
      staleWhileRevalidateMs: EVENT_SCHEDULE_CACHE_TTL_MS * 3,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventRankingsResponse;
}

export async function getEventPredictions(eventKey: string): Promise<EventPredictionsResponse> {
  const res = await apiFetch(
    `${API}/events/${eventKey}/predictions`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as EventPredictionsResponse;
}

export async function getEventAwards(eventKey: string): Promise<EventAwardsResponse> {
  const res = await apiFetch(
    `${API}/events/${eventKey}/awards`,
    _cacheRequestOptions(undefined, {
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

export type AutoScoutSeasonSupport = {
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

export type ScoutingRoomAssignmentsResponse = {
  ok: boolean;
  room_key: string;
  event_key: string | null;
  count: number;
  leader_scout_profile?: string | null;
  leader_source?: string | null;
  room_role?: string | null;
  secondary_leader_scout_profiles?: string[];
  presence?: ScoutingRoomPresenceMember[];
  assignments: ScoutingRoomAssignmentRecord[];
  my_assignments: ScoutingRoomAssignmentRecord[];
};

export type ScoutingRoomLeaderUpdateResponse = {
  ok: boolean;
  room_key: string;
  target_scout_profile: string;
  secondary_leader_scout_profiles: string[];
  created?: boolean;
  removed?: boolean;
};

export type ScoutingRoomAssignmentUpsertResponse = {
  ok: boolean;
  room_key: string;
  deleted: boolean;
  assignment: {
    id?: number;
    room_key?: string;
    event_key?: string | null;
    match_key: string | null;
    team_key: string | null;
    assigned_scout_profile?: string;
    assigned_by_scout_profile?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
  };
};

export type ScoutingRoomAssignmentsReplaceResponse = {
  ok: boolean;
  room_key: string;
  event_key: string | null;
  count: number;
  assignments: ScoutingRoomAssignmentRecord[];
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
  const res = await apiFetch(`${API}/scouting/rooms/${roomKey}?${params.toString()}`, {
    headers: roomAccessToken ? { [ROOM_ACCESS_HEADER]: roomAccessToken } : undefined,
    bypassCache: true,
    cacheTtlMs: 0,
    timeoutMs: Math.max(8000, Math.floor(options?.timeoutMs ?? 20000)),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomStateResponse;
}

export async function getScoutingRoomAssignments(
  roomKey: string,
  options?: {
    event_key?: string;
    for_scout_profile?: string;
    client_id?: string;
    presence_heartbeat?: boolean;
    room_access_token?: string;
    timeoutMs?: number;
  },
): Promise<ScoutingRoomAssignmentsResponse> {
  const params = new URLSearchParams();
  if (options?.event_key) params.set("event_key", String(options.event_key || "").trim().toLowerCase());
  if (options?.for_scout_profile) params.set("for_scout_profile", String(options.for_scout_profile || "").trim());
  if (options?.client_id) params.set("client_id", String(options.client_id || "").trim());
  if (options?.presence_heartbeat) params.set("presence_heartbeat", "1");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const roomAccessToken = String(options?.room_access_token || "").trim();
  const res = await apiFetch(`${API}/scouting/rooms/${roomKey}/assignments${suffix}`, {
    headers: roomAccessToken ? { [ROOM_ACCESS_HEADER]: roomAccessToken } : undefined,
    bypassCache: true,
    cacheTtlMs: 0,
    timeoutMs: Math.max(8000, Math.floor(options?.timeoutMs ?? 30000)),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomAssignmentsResponse;
}

export async function upsertScoutingRoomAssignment(
  roomKey: string,
  payload: {
    match_key: string;
    team_key: string;
    assigned_scout_profile?: string | null;
    event_key?: string;
    room_access_token?: string;
    timeoutMs?: number;
  },
): Promise<ScoutingRoomAssignmentUpsertResponse> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (payload.room_access_token) {
    headers.set(ROOM_ACCESS_HEADER, String(payload.room_access_token || "").trim());
  }
  const body = withoutRoomAccessToken(payload);
  const res = await apiFetch(`${API}/scouting/rooms/${roomKey}/assignments`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    timeoutMs: Math.max(10000, Math.floor(payload.timeoutMs ?? 45000)),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomAssignmentUpsertResponse;
}

export async function replaceScoutingRoomAssignments(
  roomKey: string,
  payload: {
    event_key?: string;
    assignments: Array<{
      match_key: string;
      team_key: string;
      assigned_scout_profile: string;
    }>;
    room_access_token?: string;
    timeoutMs?: number;
  },
): Promise<ScoutingRoomAssignmentsReplaceResponse> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (payload.room_access_token) {
    headers.set(ROOM_ACCESS_HEADER, String(payload.room_access_token || "").trim());
  }
  const body = withoutRoomAccessToken(payload);
  const res = await apiFetch(`${API}/scouting/rooms/${roomKey}/assignments/replace`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    timeoutMs: Math.max(10000, Math.floor(payload.timeoutMs ?? 60000)),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as ScoutingRoomAssignmentsReplaceResponse;
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
  const res = await apiFetch(`${API}/scouting/rooms/${roomKey}/kick`, {
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
  const res = await apiFetch(`${API}/scouting/rooms/${roomKey}/leaders`, {
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
  const res = await apiFetch(`${API}/scouting/rooms/${roomKey}/leaders/remove`, {
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
  const res = await apiFetch(`${API}/scouting/rooms/${roomKey}/entries`, {
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
  const res = await apiFetch(`${API}/scouting/auto-drafts/${eventKey}/${matchKey}/${teamKey}`);
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
  const res = await apiFetch(`${API}/scouting/auto-drafts/${draftId}/approve`, {
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
  const res = await apiFetch(`${API}/scouting/auto-drafts/${draftId}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as AutoScoutDraftResponse;
}

// ── Robot tracking / heatmap fetchers ───────────────────────────────

export async function getMatchTracks(
  matchKey: string,
  options?: {
    team_key?: string;
    min_time?: number;
    max_time?: number;
    min_confidence?: number;
    limit?: number;
  },
): Promise<MatchTracksResponse> {
  const params = new URLSearchParams();
  if (options?.team_key) params.set("team_key", options.team_key);
  if (options?.min_time != null) params.set("min_time", String(options.min_time));
  if (options?.max_time != null) params.set("max_time", String(options.max_time));
  if (options?.min_confidence != null) params.set("min_confidence", String(options.min_confidence));
  if (options?.limit != null) params.set("limit", String(options.limit));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const res = await apiFetch(
    `${API}/tracks/match/${matchKey}${suffix}`,
    _cacheRequestOptions(undefined, {
      cacheTtlMs: LONG_CONTEXT_CACHE_TTL_MS,
      staleWhileRevalidateMs: LONG_CONTEXT_CACHE_TTL_MS * 2,
    }),
  );
  if (!res.ok) throw new Error(await readError(res));
  const payload = (await res.json()) as MatchTracksResponse;
  return {
    ...payload,
    local_video_url: payload.local_video_url ? absoluteApiUrl(payload.local_video_url) : null,
  };
}

export async function getTeamHeatmap(
  teamKey: string,
  eventKey: string,
  options?: {
    match_key?: string;
    grid_cols?: number;
    grid_rows?: number;
    sigma?: number;
    min_confidence?: number;
  },
): Promise<TeamHeatmapResponse> {
  const params = new URLSearchParams({ event_key: eventKey });
  if (options?.match_key) params.set("match_key", options.match_key);
  if (options?.grid_cols != null) params.set("grid_cols", String(options.grid_cols));
  if (options?.grid_rows != null) params.set("grid_rows", String(options.grid_rows));
  if (options?.sigma != null) params.set("sigma", String(options.sigma));
  if (options?.min_confidence != null) params.set("min_confidence", String(options.min_confidence));
  const res = await apiFetch(
    `${API}/tracks/heatmap/${teamKey}?${params.toString()}`,
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
  offense: { level_1_5: number; confidence_0_1: number } | null;
  defense: { level_1_5: number; confidence_0_1: number; assessable: boolean } | null;
  attack_heatmap: TeamHeatmapResponse | null;
  defense_heatmap: TeamHeatmapResponse | null;
};

export async function getTeamShiftPlay(teamKey: string, eventKey: string): Promise<TeamShiftPlayResponse> {
  const params = new URLSearchParams({ event_key: eventKey });
  const res = await apiFetch(
    `${API}/tracks/shift-play/${teamKey}?${params.toString()}`,
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
export type OnDeviceRobotShiftPlay = {
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
  session_key: string;
  reused_run: boolean;
  team_count: number;
  points_persisted: number;
  points_by_team: Record<string, number>;
  skipped_unknown_teams: string[];
  shift_play: Record<string, OnDeviceRobotShiftPlay> | null;
};

// Flush a finished on-device match breakdown to the server (POST
// /tracks/on-device-session). The StoredSession camelCase keys match the
// endpoint's request aliases, so a stored session posts verbatim. apiFetch
// auto-attaches the admin/room headers the endpoint's auth gate expects.
export async function syncOnDeviceSession(session: {
  id: string;
  eventKey?: string;
  matchKey?: string;
  createdAt?: number;
  payload: unknown;
}): Promise<OnDeviceSessionSyncResponse> {
  const res = await apiFetch(`${API}/tracks/on-device-session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(session),
    bypassCache: true,
    cacheTtlMs: 0,
    timeoutMs: 30000,
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as OnDeviceSessionSyncResponse;
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
    admin_session_token?: string;
  },
): string {
  const normalizedRoom = roomKey.trim().toLowerCase();
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
  const basePathPrefix = base.pathname && base.pathname !== "/" ? base.pathname.replace(/\/+$/, "") : "";
  base.pathname = `${basePathPrefix}/scouting/rooms/${normalizedRoom}/ws`;
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
  const adminSessionToken = String(options?.admin_session_token || "").trim() || effectiveAdminSessionToken();
  if (adminSessionToken) {
    base.searchParams.set("admin_session", adminSessionToken);
  }
  return base.toString();
}

// ── Media helpers ──────────────────────────────────────────────

/** Resolve a backend-relative media path ("/media/...") to a fetchable URL. */
export function resolveMediaUrl(path: string | null | undefined): string {
  const raw = String(path || "").trim();
  if (!raw) return "";
  return absoluteApiUrl(raw);
}

// ── Picklists ──────────────────────────────────────────────────

export type PicklistSlotTier = "first" | "second" | "dnp";
export type PicklistSlotStatus = "available" | "picked" | "declined" | "captain";

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
  const res = await apiFetch(`${API}/picklists/${picklistId}`, {
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
  const res = await apiFetch(`${API}/picklists/${picklistId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as PicklistUpdateResult;
}

export async function deletePicklist(picklistId: number): Promise<{ ok: boolean }> {
  const res = await apiFetch(`${API}/picklists/${picklistId}`, { method: "DELETE" });
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

export interface CoverageSlot {
  team_key: string;
  alliance: string;
  station: string | null;
  entry_count: number;
  scouts: string[];
}

export interface CoverageMatchRow {
  match_key: string;
  label: string;
  comp_level: string;
  time: number | null;
  slots: CoverageSlot[];
}

export interface CoverageLeaderboardRow {
  scout_profile: string;
  entry_count: number;
  matches_covered: number;
  teams_covered: number;
  best_qual_streak: number;
  last_entry_at: string | null;
}

export interface CoverageOutlier {
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
