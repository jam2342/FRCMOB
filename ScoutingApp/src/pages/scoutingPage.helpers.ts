/**
 * Constants and helper functions for the ScoutingPage component.
 * Pure logic - no React dependency.
 */

import type {
  AutoScoutDraftPayload,
  AutoScoutFieldOverride,
  AutoScoutMeta,
  ApiTeamSnapshot,
  DriverCompetency,
  EndgameMode,
  EventTeamApiBaseline,
  Level1To5,
  ManualScoutingRating,
  MatchTeamOption,
  MobileCapturePanel,
  MobileHistoryPanel,
  MobileScorePanel,
  OverallScoutRating,
  RpState,
  SavedScoutingEntry,
  ScoutFormState,
  ScoutingApiRating,
  FloatingTimerPosition,
  FloatingTimerBounds,
} from './scoutingPage.types';

import type {
  EventScheduleItem,
  EventScheduleTeam,
  ScoutingRoomEntryRecord,
} from '../api';

import {
  asRecord,
  clampNumber,
  parseNumber,
  liveTimerLabel,
  teamNumberFromTeamKey,
  titleizeKey,
} from './centerUtils';

const SCOUTING_ENTRIES_STORAGE = 'scouting_manual_entries_v2';
const SCOUTING_ENTRIES_STORAGE_LEGACY = 'scouting_manual_entries_v1';
const SCOUTING_TIMER_FLOAT_STORAGE = 'scouting_timer_float_pos_v1';
const SCOUTING_MOBILE_COMPACT_STORAGE = 'scouting_mobile_compact_mode_v1';

const MATCH_DURATION_SEC = 150;
const AUTO_PHASE_SEC = 20;
const ENDGAME_SEC = 30;

const FLOAT_TIMER_VIEWPORT_PADDING = 6;
const FLOAT_TIMER_MIN_VISIBLE_X = 36;
const FLOAT_TIMER_MIN_VISIBLE_Y = 28;
const FLOAT_TIMER_HEADER_OVERLAP_Y = 132;
// Roughly the timer's own height plus a margin, so it lands above the fold edge.
const FLOAT_TIMER_DEFAULT_BOTTOM_OFFSET = 150;

export const ENDGAME_LABELS: Record<EndgameMode, string> = {
  none: 'No endgame action',
  kept_scoring: 'Kept scoring',
  parked: 'Parked',
  climb_level_1: 'Climbed 1 level',
  climb_level_2: 'Climbed 2 levels',
  climb_level_3: 'Climbed 3 levels',
};

export const ENDGAME_POINTS: Record<EndgameMode, number> = {
  none: 0,
  kept_scoring: 0,
  parked: 5,
  climb_level_1: 10,
  climb_level_2: 20,
  climb_level_3: 30,
};

const ENDGAME_DRIVER_FACTOR: Record<EndgameMode, number> = {
  none: 0.24,
  kept_scoring: 0.52,
  parked: 0.58,
  climb_level_1: 0.72,
  climb_level_2: 0.86,
  climb_level_3: 1,
};

const MAX_ENDGAME_POINTS = Math.max(...Object.values(ENDGAME_POINTS));

export const MOBILE_CAPTURE_PANEL_TABS: Array<{ id: MobileCapturePanel; label: string }> = [
  { id: 'auto', label: 'Auto' },
  { id: 'teleop', label: 'Teleop' },
  { id: 'endgame', label: 'Endgame' },
  { id: 'mobility', label: 'Mobility' },
  { id: 'strategy', label: 'Strategy' },
  { id: 'notes', label: 'Notes' },
  { id: 'auto-paths', label: 'Paths' },
];

export const MOBILE_SCORE_PANEL_TABS: Array<{ id: MobileScorePanel; label: string }> = [
  { id: 'driver', label: 'Driver' },
  { id: 'points', label: 'Points' },
  { id: 'live', label: 'Live' },
  { id: 'saved', label: 'Saved' },
];

export const MOBILE_HISTORY_PANEL_TABS: Array<{ id: MobileHistoryPanel; label: string }> = [
  { id: 'team_matches', label: 'Matches' },
  { id: 'team_rollups', label: 'Teams' },
  { id: 'entries', label: 'Logs' },
  { id: 'summaries', label: 'Summary' },
];

export const EMPTY_FORM: ScoutFormState = {
  auto_mobility: false,
  auto_scored: 0,
  auto_missed: 0,
  auto_pickups: 0,
  auto_cycles: 0,
  auto_path_quality_1_5: null,
  teleop_scored: 0,
  teleop_missed: 0,
  teleop_under_defense_scored: 0,
  teleop_under_defense_attempts: 0,
  teleop_cycles: 0,
  teleop_drops: 0,
  intake_failures: 0,
  foul_count: 0,
  offense_level_1_5: null,
  defense_level_1_5: null,
  field_awareness_1_5: null,
  decision_quality_1_5: null,
  communication_1_5: null,
  anti_defense_level_1_5: null,
  escape_level_1_5: null,
  reroute_level_1_5: null,
  hit_recovery_level_1_5: null,
  bump_crosses: 0,
  trench_crosses: 0,
  climbed_under_pressure: false,
  protected_zone_risk: false,
  endgame_mode: 'none',
};

export const EMPTY_RP: RpState = {
  energized: false,
  supercharged: false,
  traversal: false,
  coop: false,
};

export function normalizeEventKeyInput(raw: string): string {
  return raw.trim().toLowerCase();
}

export function normalizeScoutProfile(raw: string): string {
  const cleaned = raw.trim().replace(/\s+/g, ' ');
  return cleaned.slice(0, 40);
}

export function normalizeScoutingNotes(raw: string): string {
  return raw.replace(/\r\n/g, '\n').slice(0, 600);
}

function toLevel(value: unknown, fallback: Level1To5 = 3): Level1To5 {
  const parsed = parseNumber(value);
  if (parsed === null) return fallback;
  if (parsed <= 1) return 1;
  if (parsed >= 5) return 5;
  return Math.round(parsed) as Level1To5;
}

function toInt(value: unknown, fallback = 0): number {
  const parsed = parseNumber(value);
  if (parsed === null) return fallback;
  return Math.max(0, Math.floor(parsed));
}

function toBool(value: unknown, fallback = false): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value > 0;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (normalized === 'true' || normalized === '1' || normalized === 'yes') return true;
    if (normalized === 'false' || normalized === '0' || normalized === 'no') return false;
  }
  return fallback;
}

function normalizeForm(raw: Record<string, unknown> | null): ScoutFormState {
  if (!raw) return { ...EMPTY_FORM };
  const endgameRaw = typeof raw.endgame_mode === 'string' ? raw.endgame_mode : '';
  const endgameMode: EndgameMode =
    endgameRaw === 'kept_scoring' ||
    endgameRaw === 'parked' ||
    endgameRaw === 'climb_level_1' ||
    endgameRaw === 'climb_level_2' ||
    endgameRaw === 'climb_level_3'
      ? endgameRaw
      : 'none';
  return {
    auto_mobility: toBool(raw.auto_mobility),
    auto_scored: toInt(raw.auto_scored),
    auto_missed: toInt(raw.auto_missed),
    auto_pickups: toInt(raw.auto_pickups),
    auto_cycles: toInt(raw.auto_cycles),
    auto_path_quality_1_5: toLevel(raw.auto_path_quality_1_5, null),
    teleop_scored: toInt(raw.teleop_scored),
    teleop_missed: toInt(raw.teleop_missed),
    teleop_under_defense_scored: toInt(raw.teleop_under_defense_scored),
    teleop_under_defense_attempts: toInt(raw.teleop_under_defense_attempts),
    teleop_cycles: toInt(raw.teleop_cycles),
    teleop_drops: toInt(raw.teleop_drops),
    intake_failures: toInt(raw.intake_failures),
    foul_count: toInt(raw.foul_count),
    offense_level_1_5: toLevel(raw.offense_level_1_5, null),
    defense_level_1_5: toLevel(raw.defense_level_1_5, null),
    field_awareness_1_5: toLevel(raw.field_awareness_1_5, null),
    decision_quality_1_5: toLevel(raw.decision_quality_1_5, null),
    communication_1_5: toLevel(raw.communication_1_5, null),
    anti_defense_level_1_5: toLevel(raw.anti_defense_level_1_5, null),
    escape_level_1_5: toLevel(raw.escape_level_1_5, null),
    reroute_level_1_5: toLevel(raw.reroute_level_1_5, null),
    hit_recovery_level_1_5: toLevel(raw.hit_recovery_level_1_5, null),
    bump_crosses: toInt(raw.bump_crosses),
    trench_crosses: toInt(raw.trench_crosses),
    climbed_under_pressure: toBool(raw.climbed_under_pressure),
    protected_zone_risk: toBool(raw.protected_zone_risk),
    endgame_mode: endgameMode,
  };
}

function normalizeRp(raw: Record<string, unknown> | null): RpState {
  if (!raw) return { ...EMPTY_RP };
  return {
    energized: toBool(raw.energized),
    supercharged: toBool(raw.supercharged),
    traversal: toBool(raw.traversal),
    coop: toBool(raw.coop),
  };
}

export function pointsFromForm(form: ScoutFormState): SavedScoutingEntry['points'] {
  const autoPoints = (form.auto_scored + (form.auto_mobility ? 1 : 0)) * 1;
  const teleopPoints = form.teleop_scored * 1;
  const endgamePoints = ENDGAME_POINTS[form.endgame_mode];
  return {
    auto: autoPoints,
    teleop: teleopPoints,
    endgame: endgamePoints,
    total: autoPoints + teleopPoints + endgamePoints,
  };
}

function normalizeScoreTier(score: number): string {
  if (score >= 88) return 'Elite';
  if (score >= 76) return 'Strong';
  if (score >= 62) return 'Solid';
  if (score >= 48) return 'Developing';
  return 'At Risk';
}

export function scoreTierLevel(score: number): number {
  if (score >= 88) return 5;
  if (score >= 76) return 4;
  if (score >= 62) return 3;
  if (score >= 48) return 2;
  return 1;
}

function pushUnique(list: string[], value: string) {
  if (!list.includes(value)) list.push(value);
}

function safeRatio(numerator: number, denominator: number, fallback = 0): number {
  if (denominator <= 0) return fallback;
  return numerator / denominator;
}

function logistic01(value: number, midpoint: number, steepness: number): number {
  const scale = Math.max(0.0001, steepness);
  return 1 / (1 + Math.exp(-(value - midpoint) / scale));
}

function sampleEvidenceConfidence(observations: number, midpoint = 6, steepness = 2.1): number {
  return clampNumber(logistic01(Math.max(0, observations), midpoint, steepness), 0, 1);
}

function bayesianRate(
  successes: number,
  attempts: number,
  options?: {
    priorMean?: number;
    priorStrength?: number;
  },
): number {
  const priorMean = options?.priorMean ?? 0.55;
  const priorStrength = options?.priorStrength ?? 5;
  const safeAttempts = Math.max(0, attempts);
  const safeSuccesses = clampNumber(successes, 0, safeAttempts);
  const alpha = clampNumber(priorMean, 0.01, 0.99) * Math.max(1, priorStrength);
  const beta = (1 - clampNumber(priorMean, 0.01, 0.99)) * Math.max(1, priorStrength);
  return (safeSuccesses + alpha) / Math.max(1, safeAttempts + alpha + beta);
}

export function driverCompetency(form: ScoutFormState): DriverCompetency {
  const teleopAttempts = form.teleop_scored + form.teleop_missed;
  const teleopEvidence = sampleEvidenceConfidence(teleopAttempts, 8, 2.4);
  const teleopAccuracy = clampNumber(
    bayesianRate(form.teleop_scored, teleopAttempts, { priorMean: 0.57, priorStrength: 7 }),
    0,
    1,
  );

  const defendedAttempts = form.teleop_under_defense_attempts;
  const defendedEvidence = sampleEvidenceConfidence(defendedAttempts, 5, 1.9);
  const defendedAccuracy = clampNumber(
    bayesianRate(form.teleop_under_defense_scored, defendedAttempts, {
      priorMean: teleopAccuracy,
      priorStrength: 5,
    }),
    0,
    1,
  );

  const teleopVolume = clampNumber(
    0.5 * logistic01(form.teleop_scored, 10.5, 2.8) +
      0.34 * logistic01(form.teleop_cycles, 8.5, 2.2) +
      0.16 * logistic01(form.auto_pickups + form.teleop_cycles, 12, 3.3),
    0,
    1,
  );

  const cycleYield = safeRatio(form.teleop_scored, Math.max(1, form.teleop_cycles), 0.88);
  const dropPressure = safeRatio(form.teleop_drops + form.intake_failures * 0.85, teleopAttempts + 4, 0.2);
  const handlingEfficiency = clampNumber(
    1 - dropPressure * 1.35,
    0,
    1,
  );
  const cycleEfficiency = clampNumber(
    0.58 * logistic01(cycleYield, 1.28, 0.28) + 0.42 * handlingEfficiency,
    0,
    1,
  );

  const offenseSignal = clampNumber(((form.offense_level_1_5 ?? 3) - 1) / 4, 0, 1);
  const defenseSignal = clampNumber(((form.defense_level_1_5 ?? 3) - 1) / 4, 0, 1);
  const awarenessSignal = clampNumber(((form.field_awareness_1_5 ?? 3) - 1) / 4, 0, 1);
  const decisionSignal = clampNumber(((form.decision_quality_1_5 ?? 3) - 1) / 4, 0, 1);
  const communicationSignal = clampNumber(((form.communication_1_5 ?? 3) - 1) / 4, 0, 1);
  const tacticalSignal = clampNumber(
    0.28 * awarenessSignal +
      0.28 * decisionSignal +
      0.18 * communicationSignal +
      0.16 * offenseSignal +
      0.1 * defenseSignal,
    0,
    1,
  );

  const antiDefenseSignal = clampNumber(
    0.34 * clampNumber(((form.anti_defense_level_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.26 * clampNumber(((form.escape_level_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.2 * clampNumber(((form.reroute_level_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.2 * clampNumber(((form.hit_recovery_level_1_5 ?? 3) - 1) / 4, 0, 1),
    0,
    1,
  );
  const defendedRetention = clampNumber(0.5 + (defendedAccuracy - teleopAccuracy) * 1.4, 0, 1);
  const laneMobilitySignal = clampNumber(
    0.52 * logistic01(form.bump_crosses + form.trench_crosses, 7, 2.5) +
      0.48 * antiDefenseSignal,
    0,
    1,
  );
  const pressureSignal = clampNumber(
    0.46 * antiDefenseSignal + 0.34 * defendedRetention + 0.2 * laneMobilitySignal,
    0,
    1,
  );

  const endgameSignal = clampNumber(
    ENDGAME_DRIVER_FACTOR[form.endgame_mode] * (form.climbed_under_pressure ? 1.07 : 1) -
      Number(form.protected_zone_risk) * 0.08,
    0,
    1,
  );
  const foulPressure = safeRatio(form.foul_count, Math.max(1, teleopAttempts / 6), 0);
  const disciplineSignal = clampNumber(
    1 -
      foulPressure * 0.24 -
      dropPressure * 0.52 -
      Number(form.protected_zone_risk) * 0.16,
    0,
    1,
  );

  const consistencySpread = Math.max(
    teleopAccuracy,
    defendedAccuracy,
    cycleEfficiency,
    tacticalSignal,
    disciplineSignal,
  ) - Math.min(
    teleopAccuracy,
    defendedAccuracy,
    cycleEfficiency,
    tacticalSignal,
    disciplineSignal,
  );
  const consistencySignal = clampNumber(1 - consistencySpread * 0.46, 0, 1);
  const evidenceSignal = clampNumber(
    0.44 * teleopEvidence +
      0.24 * defendedEvidence +
      0.2 * sampleEvidenceConfidence(form.teleop_cycles, 6, 1.7) +
      0.12 * sampleEvidenceConfidence(form.auto_scored + form.teleop_scored, 10, 2.8),
    0,
    1,
  );

  const synergyBonus = clampNumber(
    (teleopVolume >= 0.64 && teleopAccuracy >= 0.6 ? 0.038 : 0) +
      (pressureSignal >= 0.63 && disciplineSignal >= 0.62 ? 0.032 : 0) +
      (tacticalSignal >= 0.68 && cycleEfficiency >= 0.63 ? 0.028 : 0),
    0,
    0.12,
  );
  const volatilityPenalty = clampNumber(
    (teleopVolume >= 0.72 && teleopAccuracy <= 0.46 ? 0.06 : 0) +
      (defendedAttempts >= 4 && pressureSignal <= 0.42 ? 0.05 : 0) +
      (disciplineSignal <= 0.45 ? 0.04 : 0) +
      (evidenceSignal <= 0.3 ? 0.035 : 0),
    0,
    0.21,
  );

  // This intentionally excludes autonomous to isolate driver-control quality.
  const baselineScore01 = clampNumber(
    0.16 * teleopVolume +
      0.17 * teleopAccuracy +
      0.14 * cycleEfficiency +
      0.14 * pressureSignal +
      0.13 * tacticalSignal +
      0.1 * endgameSignal +
      0.08 * disciplineSignal +
      0.05 * consistencySignal +
      0.03 * laneMobilitySignal,
    0,
    1,
  );
  const evidenceAdjustment = clampNumber((evidenceSignal - 0.45) * 0.16, -0.06, 0.07);
  const score01 = clampNumber(
    baselineScore01 + synergyBonus - volatilityPenalty + evidenceAdjustment,
    0,
    1,
  );

  const score = Math.round(clampNumber(score01, 0, 1) * 100);
  const level = score >= 88 ? 5 : score >= 74 ? 4 : score >= 60 ? 3 : score >= 46 ? 2 : 1;
  const tier = level === 5 ? 'Elite' : level === 4 ? 'Strong' : level === 3 ? 'Solid' : level === 2 ? 'Developing' : 'Struggling';

  return {
    score_0_100: score,
    level_1_5: level,
    tier,
    breakdown: {
      teleop_volume_0_1: Number(teleopVolume.toFixed(4)),
      teleop_accuracy_0_1: Number(teleopAccuracy.toFixed(4)),
      offense_0_1: Number(tacticalSignal.toFixed(4)),
      defense_0_1: Number(pressureSignal.toFixed(4)),
      endgame_0_1: Number(endgameSignal.toFixed(4)),
      discipline_0_1: Number(disciplineSignal.toFixed(4)),
    },
  };
}

export function manualScoutingRating(
  form: ScoutFormState,
  driverScore: DriverCompetency,
): ManualScoutingRating {
  const autoAttempts = form.auto_scored + form.auto_missed;
  const autoAccuracy = autoAttempts > 0 ? form.auto_scored / autoAttempts : 0.5;
  const autoSignal = clampNumber(
    0.34 * clampNumber(form.auto_scored / 7, 0, 1) +
      0.2 * autoAccuracy +
      0.2 * Number(form.auto_mobility) +
      0.14 * clampNumber(form.auto_pickups / 4, 0, 1) +
      0.12 * clampNumber(((form.auto_path_quality_1_5 ?? 3) - 1) / 4, 0, 1),
    0,
    1,
  );

  const teleopAttempts = form.teleop_scored + form.teleop_missed;
  const teleopAccuracy = teleopAttempts > 0 ? form.teleop_scored / teleopAttempts : 0.5;
  const defendedAccuracy =
    form.teleop_under_defense_attempts > 0
      ? form.teleop_under_defense_scored / Math.max(1, form.teleop_under_defense_attempts)
      : 0.5;
  const teleopSignal = clampNumber(
    0.38 * clampNumber(form.teleop_scored / 26, 0, 1) +
      0.2 * teleopAccuracy +
      0.16 * clampNumber(form.teleop_cycles / 12, 0, 1) +
      0.18 * defendedAccuracy +
      0.08 * clampNumber(1 - form.teleop_drops * 0.08, 0, 1),
    0,
    1,
  );

  const endgameSignal = clampNumber(
    0.78 * (ENDGAME_POINTS[form.endgame_mode] / MAX_ENDGAME_POINTS) + 0.22 * Number(form.climbed_under_pressure),
    0,
    1,
  );

  const antiDefenseSignal = clampNumber(
    0.26 * clampNumber(((form.anti_defense_level_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.2 * clampNumber(((form.escape_level_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.16 * clampNumber(((form.reroute_level_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.14 * clampNumber(((form.hit_recovery_level_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.1 * clampNumber(form.teleop_under_defense_scored / 10, 0, 1) +
      0.08 * clampNumber(form.bump_crosses / 12, 0, 1) +
      0.06 * clampNumber(form.trench_crosses / 12, 0, 1),
    0,
    1,
  );

  const executionSignal = clampNumber(
    0.28 * clampNumber(((form.offense_level_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.18 * clampNumber(((form.defense_level_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.18 * clampNumber(((form.field_awareness_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.18 * clampNumber(((form.decision_quality_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.08 * clampNumber(((form.communication_1_5 ?? 3) - 1) / 4, 0, 1) +
      0.1 * clampNumber(driverScore.score_0_100 / 100, 0, 1),
    0,
    1,
  );

  const disciplineSignal = clampNumber(
    1 - form.foul_count * 0.11 - form.teleop_drops * 0.035 - form.intake_failures * 0.03 - Number(form.protected_zone_risk) * 0.18,
    0,
    1,
  );

  const score01 =
    0.2 * autoSignal +
    0.29 * teleopSignal +
    0.16 * endgameSignal +
    0.17 * antiDefenseSignal +
    0.12 * executionSignal +
    0.06 * disciplineSignal;

  const score = Math.round(clampNumber(score01, 0, 1) * 100);

  const pros: string[] = [];
  const cons: string[] = [];

  if (autoSignal >= 0.68) pushUnique(pros, 'Strong autonomous consistency.');
  if (teleopSignal >= 0.72) pushUnique(pros, 'High teleop production and efficiency.');
  if (antiDefenseSignal >= 0.68) pushUnique(pros, 'Maintains output while under defense.');
  if (endgameSignal >= 0.75) pushUnique(pros, 'Reliable endgame value under pressure.');
  if (disciplineSignal >= 0.82) pushUnique(pros, 'Low foul and handling risk profile.');

  if (autoSignal <= 0.4) pushUnique(cons, 'Autonomous output currently limited.');
  if (teleopSignal <= 0.46) pushUnique(cons, 'Teleop conversion and pace need improvement.');
  if (antiDefenseSignal <= 0.42) pushUnique(cons, 'Defense pressure causes meaningful drop-off.');
  if (endgameSignal <= 0.38) pushUnique(cons, 'Endgame ceiling appears limited.');
  if (disciplineSignal <= 0.46) pushUnique(cons, 'Penalties and handling errors add risk.');

  if (pros.length === 0) pushUnique(pros, 'Balanced baseline with no single dominant signal yet.');
  if (cons.length === 0) pushUnique(cons, 'No major red flags in this scouting sample.');

  return {
    score_0_100: score,
    tier: normalizeScoreTier(score),
    breakdown: {
      auto_0_1: Number(autoSignal.toFixed(4)),
      teleop_0_1: Number(teleopSignal.toFixed(4)),
      endgame_0_1: Number(endgameSignal.toFixed(4)),
      anti_defense_0_1: Number(antiDefenseSignal.toFixed(4)),
      execution_0_1: Number(executionSignal.toFixed(4)),
      discipline_0_1: Number(disciplineSignal.toFixed(4)),
    },
    pros,
    cons,
  };
}

export function overallScoutRating(
  points: SavedScoutingEntry['points'],
  manualRating: ManualScoutingRating,
  driverScore: DriverCompetency,
): OverallScoutRating {
  const manual01 = clampNumber(manualRating.score_0_100 / 100, 0, 1);
  const driver01 = clampNumber(driverScore.score_0_100 / 100, 0, 1);

  const autoPointsSignal = logistic01(points.auto, 4.5, 1.6);
  const teleopPointsSignal = logistic01(points.teleop, 16, 4);
  const endgamePointsSignal = clampNumber(points.endgame / Math.max(1, MAX_ENDGAME_POINTS), 0, 1);
  const totalPointsSignal = logistic01(points.total, 27, 5.5);
  const pointsSignal = clampNumber(
    0.42 * totalPointsSignal +
      0.22 * teleopPointsSignal +
      0.14 * autoPointsSignal +
      0.22 * endgamePointsSignal,
    0,
    1,
  );

  const pressureSignal = clampNumber(
    0.56 * manualRating.breakdown.anti_defense_0_1 +
      0.44 * driverScore.breakdown.defense_0_1,
    0,
    1,
  );
  const endgameSignal = clampNumber(
    0.58 * manualRating.breakdown.endgame_0_1 +
      0.42 * driverScore.breakdown.endgame_0_1,
    0,
    1,
  );
  const disciplineSignal = clampNumber(
    0.58 * manualRating.breakdown.discipline_0_1 +
      0.42 * driverScore.breakdown.discipline_0_1,
    0,
    1,
  );
  const executionSignal = clampNumber(
    0.62 * manualRating.breakdown.execution_0_1 +
      0.38 * driverScore.breakdown.offense_0_1,
    0,
    1,
  );
  const reliabilitySignal = clampNumber(
    0.38 * disciplineSignal +
      0.27 * executionSignal +
      0.2 * manualRating.breakdown.teleop_0_1 +
      0.15 * pressureSignal,
    0,
    1,
  );

  const manualDriverGap = Math.abs(manual01 - driver01);
  const alignmentPenalty = clampNumber(manualDriverGap * 0.22, 0, 0.1);
  const ceilingBonus = clampNumber(
    (pointsSignal - 0.62) * 0.1 + (endgameSignal >= 0.72 ? 0.025 : 0),
    0,
    0.09,
  );
  const riskPenalty = clampNumber(
    (1 - reliabilitySignal) * 0.09 + (pressureSignal < 0.4 ? 0.025 : 0),
    0,
    0.12,
  );

  const score01 = clampNumber(
    0.42 * manual01 +
      0.22 * driver01 +
      0.14 * pointsSignal +
      0.09 * endgameSignal +
      0.07 * reliabilitySignal +
      0.06 * pressureSignal +
      ceilingBonus -
      alignmentPenalty -
      riskPenalty,
    0,
    1,
  );
  const rounded = Number((score01 * 100).toFixed(1));
  return {
    score_0_100: rounded,
    tier: normalizeScoreTier(rounded),
    components: {
      manual_0_100: Number((manual01 * 100).toFixed(2)),
      driver_0_100: Number((driver01 * 100).toFixed(2)),
      points_0_100: Number((pointsSignal * 100).toFixed(2)),
      endgame_0_100: Number((endgameSignal * 100).toFixed(2)),
      discipline_0_100: Number((reliabilitySignal * 100).toFixed(2)),
    },
  };
}

function normalizeEpaTo100(value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  return clampNumber((value - 1200) / 8.5, 0, 100);
}

function firstDefinedNumber(...values: Array<number | null | undefined>): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
}

export function scoutingApiRating(
  manualRating: ManualScoutingRating,
  apiSnapshot: ApiTeamSnapshot | null,
  baseline: EventTeamApiBaseline | null,
): ScoutingApiRating | null {
  const strengthSignal = firstDefinedNumber(
    apiSnapshot?.rating_0_100,
    baseline?.rating_0_100,
    normalizeEpaTo100(apiSnapshot?.statbotics_norm_epa ?? null),
    normalizeEpaTo100(baseline?.statbotics_norm_epa ?? null),
  );
  const reliabilitySignal = firstDefinedNumber(
    apiSnapshot?.reliability_0_1 !== null && apiSnapshot?.reliability_0_1 !== undefined
      ? apiSnapshot.reliability_0_1 * 100
      : null,
    baseline?.reliability_0_1 !== null && baseline?.reliability_0_1 !== undefined
      ? baseline.reliability_0_1 * 100
      : null,
  );
  const autoSignal =
    apiSnapshot?.auto_contribution !== null && apiSnapshot?.auto_contribution !== undefined
      ? clampNumber(apiSnapshot.auto_contribution * 14, 0, 100)
      : null;
  const climbSignal =
    apiSnapshot?.climb_success_prob !== null && apiSnapshot?.climb_success_prob !== undefined
      ? clampNumber(apiSnapshot.climb_success_prob * 100, 0, 100)
      : null;
  const cycleSignal =
    apiSnapshot?.cycle_time_sec !== null && apiSnapshot?.cycle_time_sec !== undefined
      ? clampNumber((56 - apiSnapshot.cycle_time_sec) * 2.7, 0, 100)
      : null;

  const availableSignals = [strengthSignal, reliabilitySignal, autoSignal, climbSignal, cycleSignal].filter(
    (value): value is number => value !== null,
  );
  if (availableSignals.length === 0) {
    return {
      score_0_100: manualRating.score_0_100,
      confidence_0_1: 0.35,
      tier: normalizeScoreTier(manualRating.score_0_100),
      components: {
        manual_weighted_0_100: Number((manualRating.score_0_100 * 0.72).toFixed(2)),
        api_strength_0_100: 50,
        api_reliability_0_100: 50,
        api_auto_0_100: 50,
        api_climb_0_100: 50,
        api_cycle_0_100: 50,
        freshness_penalty_0_100: 0,
      },
      pros: ['Manual scouting is the dominant signal for this report.'],
      cons: ['API context is limited; confidence is reduced until more synced data arrives.'],
    };
  }

  const fallbackWarningCount = (apiSnapshot?.warnings || []).filter(
    (warning) => /fallback|sparse|outdated|unavailable/i.test(warning),
  ).length;
  const signalCompleteness = clampNumber(availableSignals.length / 5, 0, 1);
  const strength = strengthSignal ?? 50;
  const reliability = reliabilitySignal ?? 50;
  const auto = autoSignal ?? 50;
  const climb = climbSignal ?? 50;
  const cycle = cycleSignal ?? 50;

  const confidenceInput = clampNumber(
    firstDefinedNumber(
      apiSnapshot?.confidence_0_1,
      baseline?.reliability_0_1,
      0.45,
    ) ?? 0.45,
    0,
    1,
  );
  const reliability01 = clampNumber(reliability / 100, 0, 1);
  const externalConfidence = clampNumber(
    0.24 +
      signalCompleteness * 0.3 +
      confidenceInput * 0.28 +
      reliability01 * 0.14 -
      fallbackWarningCount * 0.06,
    0.2,
    0.97,
  );

  const signalWeights = [
    { value: strengthSignal, weight: 0.44 },
    { value: reliabilitySignal, weight: 0.2 },
    { value: autoSignal, weight: 0.13 },
    { value: climbSignal, weight: 0.13 },
    { value: cycleSignal, weight: 0.1 },
  ];
  let apiWeightTotal = 0;
  let apiWeightedSum = 0;
  for (const item of signalWeights) {
    if (item.value === null || item.value === undefined) continue;
    apiWeightTotal += item.weight;
    apiWeightedSum += item.weight * item.value;
  }
  const apiComposite = apiWeightTotal > 0 ? apiWeightedSum / apiWeightTotal : 50;
  const manualWeight = clampNumber(
    0.79 - externalConfidence * 0.36 + (fallbackWarningCount > 0 ? 0.05 : 0),
    0.44,
    0.82,
  );
  const manualWeightedScore = manualRating.score_0_100 * manualWeight;
  const apiWeightedScore = apiComposite * (1 - manualWeight);
  const alignmentGap = Math.abs(manualRating.score_0_100 - strength) / 100;
  const freshnessPenalty = clampNumber(
    fallbackWarningCount * 2.2 + (signalCompleteness < 0.45 ? 2.4 : 0),
    0,
    14,
  );
  const disagreementPenalty = clampNumber((alignmentGap - 0.24) * 18, 0, 10);
  const volatilityPenalty = clampNumber(
    (reliability <= 45 ? 2.6 : 0) +
      (manualRating.breakdown.discipline_0_1 <= 0.4 ? 2 : 0) +
      (cycle <= 38 && strength >= 65 ? 1.8 : 0),
    0,
    7,
  );
  const upsideBonus = clampNumber(
    (strength >= 76 && manualRating.score_0_100 >= 72 ? 2.2 : 0) +
      (climb >= 74 && manualRating.breakdown.endgame_0_1 >= 0.7 ? 1.3 : 0),
    0,
    4.5,
  );

  const score = clampNumber(
    manualWeightedScore +
      apiWeightedScore +
      upsideBonus -
      freshnessPenalty -
      disagreementPenalty -
      volatilityPenalty,
    0,
    100,
  );

  const confidence = clampNumber(
    0.26 + externalConfidence * 0.62 + signalCompleteness * 0.12 - fallbackWarningCount * 0.05,
    0.22,
    0.99,
  );

  const pros: string[] = [];
  const cons: string[] = [];

  if (strength >= 74) pushUnique(pros, 'API baseline projects above-average match contribution.');
  if (reliability >= 72) pushUnique(pros, 'API reliability trend is steady.');
  if (apiComposite >= 76 && manualRating.score_0_100 >= 72) pushUnique(pros, 'Manual and API signals align on a strong ceiling.');
  if (manualRating.breakdown.anti_defense_0_1 >= 0.65) pushUnique(pros, 'Manual scouting confirms anti-defense resilience.');
  if (auto >= 68) pushUnique(pros, 'Strong autonomous component from synced API context.');
  if (climb >= 72) pushUnique(pros, 'Endgame projection is favorable from synced climb signals.');

  if (strength <= 45) pushUnique(cons, 'API baseline indicates limited ceiling versus event field.');
  if (reliability <= 46) pushUnique(cons, 'API reliability trend is volatile.');
  if (alignmentGap >= 0.34) pushUnique(cons, 'Manual and API signals disagree; prioritize additional live scouts.');
  if (cycle <= 38 && strength >= 65) pushUnique(cons, 'Cycle-time signal is lagging relative to projected strength.');
  if (manualRating.breakdown.discipline_0_1 <= 0.45) pushUnique(cons, 'Manual scouting reports discipline risk.');
  if (freshnessPenalty >= 4) pushUnique(cons, 'Some API context is fallback/outdated and should be validated.');

  if (pros.length === 0) pushUnique(pros, 'Manual and API signals are balanced with moderate confidence.');
  if (cons.length === 0) pushUnique(cons, 'No significant API-adjusted risk flags in this sample.');

  const roundedScore = Number(score.toFixed(1));
  return {
    score_0_100: roundedScore,
    confidence_0_1: Number(confidence.toFixed(3)),
    tier: normalizeScoreTier(roundedScore),
    components: {
      manual_weighted_0_100: Number(manualWeightedScore.toFixed(2)),
      api_strength_0_100: Number(strength.toFixed(2)),
      api_reliability_0_100: Number(reliability.toFixed(2)),
      api_auto_0_100: Number(auto.toFixed(2)),
      api_climb_0_100: Number(climb.toFixed(2)),
      api_cycle_0_100: Number(cycle.toFixed(2)),
      freshness_penalty_0_100: Number(freshnessPenalty.toFixed(2)),
    },
    pros,
    cons,
  };
}

export function entryHeadUp(entry: SavedScoutingEntry): string {
  const api = entry.scouting_api_rating?.score_0_100 ?? null;
  const manual = entry.manual_rating.score_0_100;
  if (api !== null && api >= 80) return 'High-value target based on manual + API alignment.';
  if (manual >= 78) return 'Strong manual scouting report; prioritize follow-up clips.';
  if (manual <= 48) return 'Risk profile elevated; validate with additional scouts.';
  return 'Balanced profile; use with alliance-fit context.';
}

function normalizeApiSnapshot(raw: Record<string, unknown> | null): ApiTeamSnapshot | null {
  if (!raw) return null;
  return {
    source_event_key: typeof raw.source_event_key === 'string' ? raw.source_event_key : null,
    fetched_at_ms: toInt(raw.fetched_at_ms, Date.now()),
    rating_0_100: parseNumber(raw.rating_0_100),
    confidence_0_1: parseNumber(raw.confidence_0_1),
    reliability_0_1: parseNumber(raw.reliability_0_1),
    auto_contribution: parseNumber(raw.auto_contribution),
    climb_success_prob: parseNumber(raw.climb_success_prob),
    cycle_time_sec: parseNumber(raw.cycle_time_sec),
    statbotics_norm_epa: parseNumber(raw.statbotics_norm_epa),
    tba_rank: parseNumber(raw.tba_rank),
    warnings: Array.isArray(raw.warnings) ? raw.warnings.map((value) => String(value)) : [],
  };
}

function normalizeManualRating(
  raw: Record<string, unknown> | null,
  form: ScoutFormState,
  driver: DriverCompetency,
): ManualScoutingRating {
  if (!raw) return manualScoutingRating(form, driver);
  const breakdown = asRecord(raw.breakdown);
  const score = clampNumber(parseNumber(raw.score_0_100) ?? manualScoutingRating(form, driver).score_0_100, 0, 100);
  return {
    score_0_100: Number(score.toFixed(1)),
    tier: typeof raw.tier === 'string' ? raw.tier : normalizeScoreTier(score),
    breakdown: {
      auto_0_1: clampNumber(parseNumber(breakdown?.auto_0_1) ?? 0.5, 0, 1),
      teleop_0_1: clampNumber(parseNumber(breakdown?.teleop_0_1) ?? 0.5, 0, 1),
      endgame_0_1: clampNumber(parseNumber(breakdown?.endgame_0_1) ?? 0.5, 0, 1),
      anti_defense_0_1: clampNumber(parseNumber(breakdown?.anti_defense_0_1) ?? 0.5, 0, 1),
      execution_0_1: clampNumber(parseNumber(breakdown?.execution_0_1) ?? 0.5, 0, 1),
      discipline_0_1: clampNumber(parseNumber(breakdown?.discipline_0_1) ?? 0.5, 0, 1),
    },
    pros: Array.isArray(raw.pros) ? raw.pros.map((value) => String(value)) : [],
    cons: Array.isArray(raw.cons) ? raw.cons.map((value) => String(value)) : [],
  };
}

function normalizeScoutingApiRating(raw: Record<string, unknown> | null): ScoutingApiRating | null {
  if (!raw) return null;
  const components = asRecord(raw.components);
  const score = parseNumber(raw.score_0_100);
  const confidence = parseNumber(raw.confidence_0_1);
  if (score === null || confidence === null) return null;
  return {
    score_0_100: Number(clampNumber(score, 0, 100).toFixed(1)),
    confidence_0_1: Number(clampNumber(confidence, 0, 1).toFixed(3)),
    tier: typeof raw.tier === 'string' ? raw.tier : normalizeScoreTier(score),
    components: {
      manual_weighted_0_100: Number((parseNumber(components?.manual_weighted_0_100) ?? 0).toFixed(2)),
      api_strength_0_100: Number((parseNumber(components?.api_strength_0_100) ?? 0).toFixed(2)),
      api_reliability_0_100: Number((parseNumber(components?.api_reliability_0_100) ?? 0).toFixed(2)),
      api_auto_0_100: Number((parseNumber(components?.api_auto_0_100) ?? 0).toFixed(2)),
      api_climb_0_100: Number((parseNumber(components?.api_climb_0_100) ?? 0).toFixed(2)),
      api_cycle_0_100: Number((parseNumber(components?.api_cycle_0_100) ?? 0).toFixed(2)),
      freshness_penalty_0_100: Number((parseNumber(components?.freshness_penalty_0_100) ?? 0).toFixed(2)),
    },
    pros: Array.isArray(raw.pros) ? raw.pros.map((value) => String(value)) : [],
    cons: Array.isArray(raw.cons) ? raw.cons.map((value) => String(value)) : [],
  };
}

function normalizeOverallScoutRating(
  raw: Record<string, unknown> | null,
  points: SavedScoutingEntry['points'],
  manual: ManualScoutingRating,
  driver: DriverCompetency,
): OverallScoutRating {
  const fallback = overallScoutRating(points, manual, driver);
  if (!raw) return fallback;
  const components = asRecord(raw.components);
  const parsedScore = parseNumber(raw.score_0_100);
  if (parsedScore === null) return fallback;
  const score = Number(clampNumber(parsedScore, 0, 100).toFixed(1));
  return {
    score_0_100: score,
    tier: typeof raw.tier === 'string' ? raw.tier : normalizeScoreTier(score),
    components: {
      manual_0_100: Number((parseNumber(components?.manual_0_100) ?? fallback.components.manual_0_100).toFixed(2)),
      driver_0_100: Number((parseNumber(components?.driver_0_100) ?? fallback.components.driver_0_100).toFixed(2)),
      points_0_100: Number((parseNumber(components?.points_0_100) ?? fallback.components.points_0_100).toFixed(2)),
      endgame_0_100: Number((parseNumber(components?.endgame_0_100) ?? fallback.components.endgame_0_100).toFixed(2)),
      discipline_0_100: Number((parseNumber(components?.discipline_0_100) ?? fallback.components.discipline_0_100).toFixed(2)),
    },
  };
}

function normalizeAutoScoutMeta(raw: Record<string, unknown> | null): AutoScoutMeta | null {
  if (!raw) return null;
  const draftId = toInt(raw.draft_id, 0);
  const mapperVersion = typeof raw.mapper_version === 'string' ? raw.mapper_version.trim() : '';
  const analysisVersion = typeof raw.analysis_version === 'string' ? raw.analysis_version.trim() : '';
  if (!draftId || !mapperVersion || !analysisVersion) return null;
  const approvedAtValue = parseNumber(raw.approved_at_ms);
  return {
    draft_id: draftId,
    mapper_version: mapperVersion,
    analysis_version: analysisVersion,
    approved_at_ms: approvedAtValue === null ? null : Math.floor(approvedAtValue),
  };
}

function normalizeFieldOverrides(raw: unknown): Record<string, AutoScoutFieldOverride> | null {
  const row = asRecord(raw);
  if (!row) return null;
  const entries = Object.entries(row)
    .map(([fieldName, value]) => {
      const parsed = asRecord(value);
      if (!parsed || !Object.prototype.hasOwnProperty.call(parsed, 'from') || !Object.prototype.hasOwnProperty.call(parsed, 'to')) {
        return null;
      }
      return [fieldName, { from: parsed.from, to: parsed.to } satisfies AutoScoutFieldOverride] as const;
    })
    .filter((entry): entry is readonly [string, AutoScoutFieldOverride] => Boolean(entry));
  return entries.length > 0 ? Object.fromEntries(entries) : null;
}

export function applyAutoScoutDraftPayload(
  baseForm: ScoutFormState,
  payload: AutoScoutDraftPayload | null | undefined,
): ScoutFormState {
  if (!payload || !payload.form_patch) return baseForm;
  const next = { ...baseForm };
  const patch = payload.form_patch;
  for (const key of Object.keys(patch) as Array<keyof ScoutFormState>) {
    if (!Object.prototype.hasOwnProperty.call(next, key)) continue;
    const value = patch[key];
    if (value == null) continue;
    (next as Record<string, unknown>)[key] = value;
  }
  return normalizeForm(next);
}

export function buildSavedScoutingEntry(payload: {
  id?: string;
  saved_at_ms: number;
  scout_profile: string;
  room_key: string | null;
  mode: SavedScoutingEntry['mode'];
  event_key: string;
  match_key: string;
  match_display: string;
  team_key: string;
  team_label: string;
  alliance: SavedScoutingEntry['alliance'];
  station: string | null;
  points: SavedScoutingEntry['points'];
  rp: RpState;
  form: ScoutFormState;
  driver_competency: DriverCompetency;
  manual_rating: ManualScoutingRating;
  overall_scout_rating: OverallScoutRating;
  scouting_api_rating: ScoutingApiRating | null;
  api_snapshot: ApiTeamSnapshot | null;
  notes: string;
  entry_source?: SavedScoutingEntry['entry_source'];
  auto_scout_meta?: AutoScoutMeta | null;
  field_overrides?: Record<string, AutoScoutFieldOverride> | null;
}): SavedScoutingEntry {
  const savedAt = Math.max(0, Math.floor(payload.saved_at_ms));
  return {
    id: payload.id || `${savedAt}-${Math.random().toString(36).slice(2, 8)}`,
    saved_at_ms: savedAt,
    scout_profile: normalizeScoutProfile(payload.scout_profile),
    room_key: payload.room_key ? normalizeRoomKey(payload.room_key) || null : null,
    mode: payload.mode === 'rapid' ? 'rapid' : 'match',
    event_key: normalizeEventKeyInput(payload.event_key),
    match_key: normalizeEventKeyInput(payload.match_key),
    match_display: String(payload.match_display || payload.match_key || 'Match'),
    team_key: String(payload.team_key || '').trim().toLowerCase(),
    team_label: String(payload.team_label || payload.team_key || 'Team'),
    alliance: payload.alliance === 'blue' ? 'blue' : 'red',
    station: payload.station ?? null,
    points: payload.points,
    rp: payload.rp,
    form: normalizeForm(payload.form),
    driver_competency: payload.driver_competency,
    manual_rating: payload.manual_rating,
    overall_scout_rating: payload.overall_scout_rating,
    scouting_api_rating: payload.scouting_api_rating,
    api_snapshot: payload.api_snapshot,
    notes: normalizeScoutingNotes(payload.notes),
    entry_source: payload.entry_source === 'reviewed_auto' ? 'reviewed_auto' : 'manual',
    auto_scout_meta: payload.entry_source === 'reviewed_auto' ? (payload.auto_scout_meta ?? null) : null,
    field_overrides: payload.entry_source === 'reviewed_auto' ? (payload.field_overrides ?? null) : null,
  };
}

export function normalizeEntry(value: unknown): SavedScoutingEntry | null {
  const row = asRecord(value);
  if (!row) return null;
  const form = normalizeForm(asRecord(row.form));
  const pointsRaw = asRecord(row.points);
  const pointsDerived = pointsFromForm(form);
  const points = {
    auto: parseNumber(pointsRaw?.auto) ?? pointsDerived.auto,
    teleop: parseNumber(pointsRaw?.teleop) ?? pointsDerived.teleop,
    endgame: parseNumber(pointsRaw?.endgame) ?? pointsDerived.endgame,
    total: parseNumber(pointsRaw?.total) ?? pointsDerived.total,
  };
  const driver = asRecord(row.driver_competency);
  const driverScore = driver
    ? {
        score_0_100: clampNumber(parseNumber(driver.score_0_100) ?? 0, 0, 100),
        level_1_5: clampNumber(Math.round(parseNumber(driver.level_1_5) ?? 3), 1, 5),
        tier: typeof driver.tier === 'string' ? driver.tier : 'Solid',
        breakdown: {
          teleop_volume_0_1: clampNumber(parseNumber(asRecord(driver.breakdown)?.teleop_volume_0_1) ?? 0.5, 0, 1),
          teleop_accuracy_0_1: clampNumber(parseNumber(asRecord(driver.breakdown)?.teleop_accuracy_0_1) ?? 0.5, 0, 1),
          offense_0_1: clampNumber(parseNumber(asRecord(driver.breakdown)?.offense_0_1) ?? 0.5, 0, 1),
          defense_0_1: clampNumber(parseNumber(asRecord(driver.breakdown)?.defense_0_1) ?? 0.5, 0, 1),
          endgame_0_1: clampNumber(parseNumber(asRecord(driver.breakdown)?.endgame_0_1) ?? 0.5, 0, 1),
          discipline_0_1: clampNumber(parseNumber(asRecord(driver.breakdown)?.discipline_0_1) ?? 0.5, 0, 1),
        },
      }
    : driverCompetency(form);

  const manual = normalizeManualRating(asRecord(row.manual_rating), form, driverScore);
  const overall = normalizeOverallScoutRating(
    asRecord(row.overall_scout_rating),
    points,
    manual,
    driverScore,
  );
  const apiRating = normalizeScoutingApiRating(asRecord(row.scouting_api_rating));

  return {
    id: typeof row.id === 'string' ? row.id : `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    saved_at_ms: toInt(row.saved_at_ms, Date.now()),
    scout_profile: normalizeScoutProfile(typeof row.scout_profile === 'string' ? row.scout_profile : ''),
    room_key: typeof row.room_key === 'string' ? normalizeRoomKey(row.room_key) || null : null,
    mode: row.mode === 'rapid' ? 'rapid' : 'match',
    event_key: normalizeEventKeyInput(String(row.event_key || '')),
    match_key: normalizeEventKeyInput(String(row.match_key || '')),
    match_display: String(row.match_display || row.match_key || 'Match'),
    team_key: String(row.team_key || '').trim().toLowerCase(),
    team_label: String(row.team_label || row.team_key || 'Team'),
    alliance: row.alliance === 'blue' ? 'blue' : 'red',
    station: typeof row.station === 'string' ? row.station : null,
    points,
    rp: normalizeRp(asRecord(row.rp)),
    form,
    driver_competency: driverScore,
    manual_rating: manual,
    overall_scout_rating: overall,
    scouting_api_rating: apiRating,
    api_snapshot: normalizeApiSnapshot(asRecord(row.api_snapshot)),
    notes: typeof row.notes === 'string' ? normalizeScoutingNotes(row.notes) : '',
    entry_source: row.entry_source === 'reviewed_auto' ? 'reviewed_auto' : 'manual',
    auto_scout_meta: normalizeAutoScoutMeta(asRecord(row.auto_scout_meta)),
    field_overrides: normalizeFieldOverrides(row.field_overrides),
  };
}

export function readStoredEntries(): SavedScoutingEntry[] {
  const raw = window.localStorage.getItem(SCOUTING_ENTRIES_STORAGE) || window.localStorage.getItem(SCOUTING_ENTRIES_STORAGE_LEGACY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.map(normalizeEntry).filter((value): value is SavedScoutingEntry => Boolean(value)).slice(0, 600);
  } catch {
    return [];
  }
}

export function entryFileName(eventKey: string, scoutProfile: string): string {
  const safeEvent = (eventKey || 'scouting').replace(/[^a-z0-9_-]+/gi, '-');
  const safeScout = (normalizeScoutProfile(scoutProfile) || 'scout').replace(/[^a-z0-9_-]+/gi, '-').toLowerCase();
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  return `scouting-${safeEvent}-${safeScout}-${stamp}.json`;
}

function teamLabel(team: EventScheduleTeam): string {
  if (team.team_number) {
    return `#${team.team_number}${team.nickname ? ` · ${team.nickname}` : ''}`;
  }
  const fallbackNumber = teamNumberFromTeamKey(team.team_key);
  if (fallbackNumber !== null) {
    return `#${fallbackNumber}${team.nickname ? ` · ${team.nickname}` : ''}`;
  }
  return `${team.team_key.toUpperCase()}${team.nickname ? ` · ${team.nickname}` : ''}`;
}

export function preferredMatchKey(rows: EventScheduleItem[]): string {
  const nowMs = Date.now();
  const withTimer = rows
    .map((row) => ({
      row,
      timer: liveTimerLabel(row.scheduled_time, nowMs),
    }))
    .sort((a, b) => {
      const aTime = a.row.scheduled_time || 0;
      const bTime = b.row.scheduled_time || 0;
      return aTime - bTime;
    });
  const live = withTimer.find((item) => item.timer.state === 'live');
  if (live) return live.row.match_key;
  const upcoming = withTimer.find((item) => item.timer.state === 'upcoming');
  if (upcoming) return upcoming.row.match_key;
  return rows[0]?.match_key || '';
}

function normalizeCompLevel(compLevel: string): string {
  return compLevel.trim().toLowerCase();
}

export function compLevelLabel(compLevel: string): string {
  const normalized = normalizeCompLevel(compLevel);
  if (normalized === 'qm') return 'Qualification';
  if (normalized === 'ef') return 'Eighth Finals';
  if (normalized === 'qf') return 'Quarterfinal';
  if (normalized === 'sf') return 'Semifinal';
  if (normalized === 'f') return 'Final';
  if (normalized === 'playoff' || normalized === 'elim' || normalized === 'elimination') return 'Playoff';
  return titleizeKey(normalized || 'Match');
}

function matchHasScores(match: EventScheduleItem): boolean {
  return (
    typeof match.red_score === 'number' &&
    typeof match.blue_score === 'number' &&
    Number.isFinite(match.red_score) &&
    Number.isFinite(match.blue_score)
  );
}

export function inferMatchCompleted(match: EventScheduleItem, nowMs: number): boolean {
  const timer = liveTimerLabel(match.scheduled_time, nowMs);
  return (
    Boolean(match.is_completed) ||
    match.winner_alliance === 'red' ||
    match.winner_alliance === 'blue' ||
    match.winner_alliance === 'tie' ||
    (matchHasScores(match) && timer.state === 'ended')
  );
}

export function inferWinnerAlliance(match: EventScheduleItem, nowMs: number): 'red' | 'blue' | 'tie' | null {
  if (match.winner_alliance === 'red' || match.winner_alliance === 'blue' || match.winner_alliance === 'tie') {
    return match.winner_alliance;
  }
  if (!inferMatchCompleted(match, nowMs) || !matchHasScores(match)) return null;
  if ((match.red_score || 0) > (match.blue_score || 0)) return 'red';
  if ((match.blue_score || 0) > (match.red_score || 0)) return 'blue';
  return 'tie';
}

export function teamAllianceForMatch(match: EventScheduleItem, teamKey: string): 'red' | 'blue' | null {
  const normalized = teamKey.trim().toLowerCase();
  if (!normalized) return null;
  if ((match.red || []).some((team) => team.team_key.toLowerCase() === normalized)) return 'red';
  if ((match.blue || []).some((team) => team.team_key.toLowerCase() === normalized)) return 'blue';
  return null;
}

export function phaseLabelForTimer(timerSec: number): 'Auto' | 'Teleop' | 'Endgame' | 'Complete' {
  if (timerSec <= 0) return 'Complete';
  if (timerSec > MATCH_DURATION_SEC - AUTO_PHASE_SEC) return 'Auto';
  if (timerSec <= ENDGAME_SEC) return 'Endgame';
  return 'Teleop';
}

export function parseTeamOptions(matchRow: EventScheduleItem | null): MatchTeamOption[] {
  if (!matchRow) return [];
  const red = (matchRow.red || []).map((team) => ({
    team_key: team.team_key.toLowerCase(),
    team_label: teamLabel(team),
    alliance: 'red' as const,
    station: team.station || null,
  }));
  const blue = (matchRow.blue || []).map((team) => ({
    team_key: team.team_key.toLowerCase(),
    team_label: teamLabel(team),
    alliance: 'blue' as const,
    station: team.station || null,
  }));
  return [...red, ...blue];
}

export function mergeEntries(existing: SavedScoutingEntry[], incoming: SavedScoutingEntry[]): SavedScoutingEntry[] {
  const map = new Map<string, SavedScoutingEntry>();
  for (const entry of existing) {
    map.set(entry.id, entry);
  }
  for (const entry of incoming) {
    const key = map.has(entry.id)
      ? `${entry.id}-${entry.saved_at_ms}-${entry.team_key}`
      : entry.id;
    map.set(key, {
      ...entry,
      id: key,
    });
  }
  return Array.from(map.values())
    .sort((a, b) => b.saved_at_ms - a.saved_at_ms)
    .slice(0, 600);
}

export function mergeEntry(existing: SavedScoutingEntry[], incoming: SavedScoutingEntry): SavedScoutingEntry[] {
  const normalizedIncoming = normalizeEntry(incoming);
  if (!normalizedIncoming) return existing;
  const nextMap = new Map(existing.map((entry) => [entry.id, entry]));
  nextMap.set(normalizedIncoming.id, normalizedIncoming);
  return Array.from(nextMap.values())
    .sort((a, b) => b.saved_at_ms - a.saved_at_ms)
    .slice(0, 600);
}

export function entriesFromRoomRecords(records: ScoutingRoomEntryRecord[], fallbackRoomKey?: string): SavedScoutingEntry[] {
  const normalizedFallbackRoomKey = normalizeRoomKey(fallbackRoomKey || '');
  const normalized = records
    .map((record) => {
      const parsed = normalizeEntry(record.entry);
      if (!parsed) return null;
      const recordRoomKey = normalizeRoomKey(record.room_key || '');
      return {
        ...parsed,
        room_key: recordRoomKey || normalizedFallbackRoomKey || parsed.room_key || null,
      } satisfies SavedScoutingEntry;
    })
    .filter((entry): entry is SavedScoutingEntry => Boolean(entry));
  return mergeEntries([], normalized);
}

export function stripEntriesForRoom(entries: SavedScoutingEntry[], roomKey: string): SavedScoutingEntry[] {
  const normalizedRoomKey = normalizeRoomKey(roomKey);
  if (!normalizedRoomKey) return entries;
  return entries.filter((entry) => normalizeRoomKey(entry.room_key || '') !== normalizedRoomKey);
}

export function normalizeRoomKey(raw: string): string {
  return raw.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '').slice(0, 48);
}

/* Width of the nav rail the timer must not sit on top of. Read from the live
   token rather than hardcoded, because --ps-sidebar-current-width tracks the
   collapsed state and the wide-screen @media override — the timer has to follow
   both. Zero below 1120px, where the sidebar is not on screen. */
export function currentSidebarWidth(viewportWidth: number): number {
  if (viewportWidth <= 1120) return 0;
  if (typeof document === 'undefined') return 0;
  const shell = document.querySelector('.product-shell');
  if (!shell) return 0;
  const raw = getComputedStyle(shell).getPropertyValue('--ps-sidebar-current-width');
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

/* Top of the scrolling content area, measured rather than assumed. The chrome
   above it is not a fixed height — the context strip grows with the number of
   recent picks and collapses to one line — so any constant here is wrong on
   some screen. Falls back to a value that clears the topbar. */
export function contentAreaTop(): number {
  if (typeof document === 'undefined') return 74;
  const content = document.querySelector('.ps-content');
  if (!content) return 74;
  const top = content.getBoundingClientRect().top;
  return Number.isFinite(top) && top > 0 ? Math.round(top) : 74;
}

export function defaultFloatingTimerPosition(viewportWidth: number): FloatingTimerPosition {
  if (viewportWidth <= 900) return { x: 8, y: 64 };
  if (viewportWidth <= 1120) return { x: 10, y: 68 };
  // Two things this default has already got wrong: x:10 put it on top of the
  // sidebar, covering the COLLAPSE control and the Home link, and y:74 then put
  // it on top of the context strip's chips. Anchoring to the top means every
  // change to the chrome above moves it back onto something.
  //
  // Bottom-left of the content area instead: clear of the rail, clear of the
  // chrome however tall it grows, and clear of the counter columns a scout is
  // actually tapping. Still draggable from there.
  const viewportHeight = typeof window === 'undefined' ? 900 : window.innerHeight;
  return {
    x: currentSidebarWidth(viewportWidth) + FLOAT_TIMER_VIEWPORT_PADDING,
    y: Math.max(
      contentAreaTop() + FLOAT_TIMER_VIEWPORT_PADDING,
      viewportHeight - FLOAT_TIMER_DEFAULT_BOTTOM_OFFSET,
    ),
  };
}

function normalizeFloatingTimerPosition(raw: unknown): FloatingTimerPosition | null {
  if (!raw || typeof raw !== 'object') return null;
  const row = raw as Record<string, unknown>;
  const xValue = parseNumber(row.x);
  const yValue = parseNumber(row.y);
  if (typeof xValue !== 'number' || !Number.isFinite(xValue)) return null;
  if (typeof yValue !== 'number' || !Number.isFinite(yValue)) return null;
  return { x: xValue, y: yValue };
}

export function readStoredFloatingTimerPosition(): FloatingTimerPosition | null {
  if (typeof window === 'undefined') return null;
  const raw = window.localStorage.getItem(SCOUTING_TIMER_FLOAT_STORAGE);
  if (!raw) return null;
  try {
    return normalizeFloatingTimerPosition(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function readStoredMobileCompactMode(): boolean {
  if (typeof window === 'undefined') return true;
  const raw = String(window.localStorage.getItem(SCOUTING_MOBILE_COMPACT_STORAGE) || '').trim().toLowerCase();
  if (raw === 'false' || raw === '0' || raw === 'off') return false;
  if (raw === 'true' || raw === '1' || raw === 'on') return true;
  return true;
}

export function floatingTimerBounds(): FloatingTimerBounds {
  if (typeof window === 'undefined') {
    return { minX: FLOAT_TIMER_VIEWPORT_PADDING, maxX: FLOAT_TIMER_VIEWPORT_PADDING, minY: FLOAT_TIMER_VIEWPORT_PADDING, maxY: FLOAT_TIMER_VIEWPORT_PADDING };
  }

  const viewport = window.visualViewport;
  const viewportWidth = viewport?.width ?? window.innerWidth;
  const viewportHeight = viewport?.height ?? window.innerHeight;
  const bottomReserve = 0;
  const minX = currentSidebarWidth(viewportWidth) + FLOAT_TIMER_VIEWPORT_PADDING;
  // Mobile deliberately allows a negative offset so the timer can tuck under
  // the header. On desktop there is chrome worth protecting, so the floor is
  // the top of the content area.
  const minY = viewportWidth <= 1120
    ? FLOAT_TIMER_VIEWPORT_PADDING - FLOAT_TIMER_HEADER_OVERLAP_Y
    : contentAreaTop() + FLOAT_TIMER_VIEWPORT_PADDING;
  const maxX = Math.max(minX, viewportWidth - FLOAT_TIMER_MIN_VISIBLE_X - FLOAT_TIMER_VIEWPORT_PADDING);
  const maxY = Math.max(minY, viewportHeight - bottomReserve - FLOAT_TIMER_MIN_VISIBLE_Y - FLOAT_TIMER_VIEWPORT_PADDING);
  return { minX, maxX, minY, maxY };
}

export function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    return navigator.clipboard
      .writeText(text)
      .then(() => true)
      .catch(() => false);
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.style.position = 'fixed';
  area.style.left = '-99999px';
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand('copy');
  document.body.removeChild(area);
  return Promise.resolve(ok);
}

export function apiSnapshotFromIntel(intelPayload: unknown, selectedEventKey: string): ApiTeamSnapshot | null {
  const intel = asRecord(intelPayload);
  if (!intel) return null;
  const analysis = asRecord(intel.analysis);
  const averages = asRecord(analysis?.averages);
  const climbSources = asRecord(analysis?.climb_sources);
  const climbVideo = asRecord(climbSources?.video_only);
  const climbOfficial = asRecord(climbSources?.official_score_breakdown);
  const rating = asRecord(intel.rating);
  const statbotics = asRecord(intel.statbotics);
  const statboticsTeam = asRecord(statbotics?.team);
  const statboticsNormEpa = asRecord(statboticsTeam?.norm_epa);
  const tba = asRecord(intel.tba);
  const eventStatus = asRecord(tba?.event_status);
  const qual = asRecord(eventStatus?.qual);
  const ranking = asRecord(qual?.ranking);
  const warnings = Array.isArray(intel.warnings)
    ? intel.warnings.map((value) => String(value || '').trim()).filter(Boolean)
    : [];

  return {
    source_event_key: typeof intel.event_key === 'string' ? intel.event_key : selectedEventKey || null,
    fetched_at_ms: Date.now(),
    rating_0_100: parseNumber(rating?.rating_0_100),
    confidence_0_1: parseNumber(rating?.confidence_0_1),
    reliability_0_1: parseNumber(averages?.reliability_score),
    auto_contribution: parseNumber(averages?.auto_contribution),
    climb_success_prob: firstDefinedNumber(
      parseNumber(averages?.climb_success_prob),
      parseNumber(climbSources?.overall_climb_success_prob),
      parseNumber(climbVideo?.climb_success_prob),
      parseNumber(climbOfficial?.climb_success_prob),
    ),
    cycle_time_sec: parseNumber(averages?.cycle_time_sec),
    statbotics_norm_epa: parseNumber(statboticsNormEpa?.current),
    tba_rank: parseNumber(ranking?.rank),
    warnings,
  };
}
