/**
 * Type definitions for the ScoutingPage component.
 */

export type FloatingTimerPosition = {
  x: number;
  y: number;
};

export type FloatingTimerBounds = {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
};

export type Level1To5 = 1 | 2 | 3 | 4 | 5 | null;
export type EndgameMode = 'none' | 'kept_scoring' | 'parked' | 'climb_level_1' | 'climb_level_2' | 'climb_level_3';
export type ScoutingMode = 'match' | 'rapid';
export type MobileScoutSection = 'capture' | 'score' | 'history';
export type MobileCapturePanel = 'auto' | 'teleop' | 'endgame' | 'mobility' | 'strategy' | 'notes' | 'auto-paths';
export type MobileScorePanel = 'driver' | 'points' | 'live' | 'saved';
export type MobileHistoryPanel = 'team_matches' | 'team_rollups' | 'entries' | 'summaries';

export type ScoutFormState = {
  auto_mobility: boolean;
  auto_scored: number;
  auto_missed: number;
  auto_pickups: number;
  auto_cycles: number;
  auto_path_quality_1_5: Level1To5;
  teleop_scored: number;
  teleop_missed: number;
  teleop_under_defense_scored: number;
  teleop_under_defense_attempts: number;
  teleop_cycles: number;
  teleop_drops: number;
  intake_failures: number;
  foul_count: number;
  offense_level_1_5: Level1To5;
  defense_level_1_5: Level1To5;
  field_awareness_1_5: Level1To5;
  decision_quality_1_5: Level1To5;
  communication_1_5: Level1To5;
  anti_defense_level_1_5: Level1To5;
  escape_level_1_5: Level1To5;
  reroute_level_1_5: Level1To5;
  hit_recovery_level_1_5: Level1To5;
  bump_crosses: number;
  trench_crosses: number;
  climbed_under_pressure: boolean;
  protected_zone_risk: boolean;
  endgame_mode: EndgameMode;
};

export type RpState = {
  energized: boolean;
  supercharged: boolean;
  traversal: boolean;
  coop: boolean;
};

export type DriverCompetency = {
  score_0_100: number;
  level_1_5: number;
  tier: string;
  breakdown: {
    teleop_volume_0_1: number;
    teleop_accuracy_0_1: number;
    offense_0_1: number;
    defense_0_1: number;
    endgame_0_1: number;
    discipline_0_1: number;
  };
};

export type ManualScoutingRating = {
  score_0_100: number;
  tier: string;
  breakdown: {
    auto_0_1: number;
    teleop_0_1: number;
    endgame_0_1: number;
    anti_defense_0_1: number;
    execution_0_1: number;
    discipline_0_1: number;
  };
  pros: string[];
  cons: string[];
};

export type OverallScoutRating = {
  score_0_100: number;
  tier: string;
  components: {
    manual_0_100: number;
    driver_0_100: number;
    points_0_100: number;
    endgame_0_100: number;
    discipline_0_100: number;
  };
};

export type ApiTeamSnapshot = {
  source_event_key: string | null;
  fetched_at_ms: number;
  rating_0_100: number | null;
  confidence_0_1: number | null;
  reliability_0_1: number | null;
  auto_contribution: number | null;
  climb_success_prob: number | null;
  cycle_time_sec: number | null;
  statbotics_norm_epa: number | null;
  tba_rank: number | null;
  warnings: string[];
};

export type ScoutingApiRating = {
  score_0_100: number;
  confidence_0_1: number;
  tier: string;
  components: {
    manual_weighted_0_100: number;
    api_strength_0_100: number;
    api_reliability_0_100: number;
    api_auto_0_100: number;
    api_climb_0_100: number;
    api_cycle_0_100: number;
    freshness_penalty_0_100: number;
  };
  pros: string[];
  cons: string[];
};

type AutoScoutFieldProvenance = 'auto' | 'needs_review' | 'manual' | 'blank';

export type AutoScoutFieldOverride = {
  from: unknown;
  to: unknown;
};

export type AutoScoutMeta = {
  draft_id: number;
  mapper_version: string;
  analysis_version: string;
  approved_at_ms: number | null;
};

type AutoScoutEvidenceRef = {
  type: string;
  ref_id: string;
  t_sec: number;
  meta?: Record<string, unknown> | null;
};

type AutoScoutDisabledPeriodInsight = {
  seconds: number | null;
  windows?: Array<{
    start_sec: number;
    end_sec: number;
    seconds: number;
  }>;
};

type AutoScoutCyclePaceInsight = {
  est_cycles: number | null;
  cycle_time_sec: number | null;
  balls_shot_total?: number | null;
};

export type AutoScoutDraftPayload = {
  form_patch: Partial<ScoutFormState>;
  notes_seed: string;
  derived_insights: {
    disabled_period?: AutoScoutDisabledPeriodInsight;
    defensive_engagement_presence?: boolean | null;
    cycle_pace_summary?: AutoScoutCyclePaceInsight;
    zone_dwell_breakdown?: Record<string, number>;
    [key: string]: unknown;
  };
};

export type AutoScoutDraftRecord = {
  id: number;
  event_key: string;
  match_key: string;
  team_key: string;
  status: 'pending' | 'generating' | 'ready' | 'low_confidence' | 'failed' | 'approved' | 'rejected' | 'superseded';
  draft_payload: AutoScoutDraftPayload;
  approved_payload?: AutoScoutDraftPayload | null;
  field_confidence: Record<string, number>;
  field_provenance: Record<string, AutoScoutFieldProvenance>;
  field_evidence_refs: Record<string, AutoScoutEvidenceRef[]>;
  field_overrides?: Record<string, AutoScoutFieldOverride> | null;
  coverage_summary: Record<string, unknown>;
  missing_reasons: string[];
  analysis_version: string;
  analysis_run_id: number | null;
  mapper_version: string;
  draft_version: number;
  generated_at: string | null;
  updated_at?: string | null;
  approved_at: string | null;
  approved_by?: string | null;
  rejected_at?: string | null;
  rejected_reason?: string | null;
  superseded_at?: string | null;
  read_only?: boolean;
  read_only_reason?: string | null;
};

export type SavedScoutingEntry = {
  id: string;
  saved_at_ms: number;
  scout_profile: string;
  room_key: string | null;
  mode: ScoutingMode;
  event_key: string;
  match_key: string;
  match_display: string;
  team_key: string;
  team_label: string;
  alliance: 'red' | 'blue';
  station: string | null;
  points: {
    auto: number;
    teleop: number;
    endgame: number;
    total: number;
  };
  rp: RpState;
  form: ScoutFormState;
  driver_competency: DriverCompetency;
  manual_rating: ManualScoutingRating;
  overall_scout_rating: OverallScoutRating;
  scouting_api_rating: ScoutingApiRating | null;
  api_snapshot: ApiTeamSnapshot | null;
  notes: string;
  entry_source?: 'manual' | 'reviewed_auto';
  auto_scout_meta?: AutoScoutMeta | null;
  field_overrides?: Record<string, AutoScoutFieldOverride> | null;
};

export type MatchTeamOption = {
  team_key: string;
  team_label: string;
  alliance: 'red' | 'blue';
  station: string | null;
};

export type EventTeamApiBaseline = {
  rating_0_100: number | null;
  reliability_0_1: number | null;
  statbotics_norm_epa: number | null;
};

export type TeamRollup = {
  team_key: string;
  team_label: string;
  entries_count: number;
  latest_event_key: string;
  latest_match_key: string;
  latest_match_display: string;
  latest_saved_at_ms: number;
  avg_points_total: number;
  avg_driver_0_100: number;
  avg_manual_0_100: number;
  avg_overall_scout_0_100: number;
  avg_scouting_api_0_100: number | null;
  head_up: string;
  notes_count: number;
};

export type TeamMatchPerformanceRow = {
  match_key: string;
  match_display: string;
  comp_level: string;
  scheduled_time: number | null;
  status: 'upcoming' | 'live' | 'completed' | 'unknown';
  alliance: 'red' | 'blue';
  alliance_score: number | null;
  opponent_score: number | null;
  winner_alliance: 'red' | 'blue' | 'tie' | null;
  scout_reports: number;
  overall_scout_avg_0_100: number | null;
  manual_avg_0_100: number | null;
  scouting_api_avg_0_100: number | null;
  avg_points_total: number | null;
  latest_note: string | null;
};

export type TeamSummaryRow = {
  team_key: string;
  team_label: string;
  reports: number;
  avg_overall_scout_0_100: number;
  avg_manual_0_100: number;
  avg_scouting_api_0_100: number | null;
  avg_driver_0_100: number;
  avg_points_total: number;
  endgame_mode: EndgameMode;
  summary: string;
  notes: string[];
};

export type RoomConnectionState = 'disconnected' | 'connecting' | 'connected' | 'error';

export type CounterField =
  | 'auto_scored'
  | 'auto_missed'
  | 'auto_pickups'
  | 'auto_cycles'
  | 'teleop_scored'
  | 'teleop_missed'
  | 'teleop_under_defense_scored'
  | 'teleop_under_defense_attempts'
  | 'teleop_cycles'
  | 'teleop_drops'
  | 'intake_failures'
  | 'foul_count'
  | 'bump_crosses'
  | 'trench_crosses';
