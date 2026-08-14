/**
 * Season game-field configuration.
 *
 * Single place that knows which scouting fields exist for the current game,
 * so a new season is a config change here (and in the backend season
 * template) instead of a rewrite of the scouting pages.
 *
 * Used by: the pit scouting form (fully schema-driven) and the match
 * scouting form's counter/scale sections.
 */

export const SEASON_YEAR = 2026;
export const SEASON_NAME = 'REBUILT';

/* ------------------------------------------------------------------ */
/*  Match scouting form fields                                         */
/* ------------------------------------------------------------------ */

import type { CounterField } from '../pages/scoutingPage.types';

export type MatchFormSection = 'auto' | 'teleop' | 'mobility' | 'strategy';

/** 1-5 qualitative scale fields in ScoutFormState. */
export type ScaleField =
  | 'auto_path_quality_1_5'
  | 'offense_level_1_5'
  | 'defense_level_1_5'
  | 'field_awareness_1_5'
  | 'decision_quality_1_5'
  | 'communication_1_5'
  | 'anti_defense_level_1_5'
  | 'escape_level_1_5'
  | 'reroute_level_1_5'
  | 'hit_recovery_level_1_5';

export interface MatchCounterDef {
  key: CounterField;
  label: string;
  section: MatchFormSection;
  /** Tap increment (fuel scores in bursts of 3 this season). */
  step?: number;
}

export interface MatchScaleDef {
  key: ScaleField;
  label: string;
  section: MatchFormSection;
}

export const MATCH_COUNTER_FIELDS: MatchCounterDef[] = [
  // Auto
  { key: 'auto_scored', label: 'Auto fuel scored', section: 'auto', step: 3 },
  { key: 'auto_missed', label: 'Auto misses', section: 'auto', step: 3 },
  { key: 'auto_pickups', label: 'Auto pickups', section: 'auto' },
  { key: 'auto_cycles', label: 'Auto cycles', section: 'auto' },
  // Teleop
  { key: 'teleop_scored', label: 'Teleop fuel scored', section: 'teleop', step: 3 },
  { key: 'teleop_missed', label: 'Teleop misses', section: 'teleop', step: 3 },
  { key: 'teleop_under_defense_scored', label: 'Scored while defended', section: 'teleop', step: 3 },
  { key: 'teleop_under_defense_attempts', label: 'Attempts while defended', section: 'teleop' },
  { key: 'teleop_cycles', label: 'Teleop cycles', section: 'teleop' },
  { key: 'teleop_drops', label: 'Drops/lost pieces', section: 'teleop' },
  { key: 'intake_failures', label: 'Intake failures', section: 'teleop' },
  { key: 'foul_count', label: 'Fouls / penalties', section: 'teleop' },
  // 2026 REBUILT field features
  { key: 'bump_crosses', label: 'Bump crossings', section: 'mobility' },
  { key: 'trench_crosses', label: 'Trench crossings', section: 'mobility' },
];

export const MATCH_SCALE_FIELDS: MatchScaleDef[] = [
  { key: 'auto_path_quality_1_5', label: 'Auto path quality', section: 'auto' },
  { key: 'anti_defense_level_1_5', label: 'Anti-defense level', section: 'mobility' },
  { key: 'escape_level_1_5', label: 'Escape from pin', section: 'mobility' },
  { key: 'reroute_level_1_5', label: 'Path reroute', section: 'mobility' },
  { key: 'hit_recovery_level_1_5', label: 'Recovery after contact', section: 'mobility' },
  { key: 'offense_level_1_5', label: 'Offense level', section: 'strategy' },
  { key: 'defense_level_1_5', label: 'Defense level', section: 'strategy' },
  { key: 'field_awareness_1_5', label: 'Field awareness', section: 'strategy' },
  { key: 'decision_quality_1_5', label: 'Decision quality', section: 'strategy' },
  { key: 'communication_1_5', label: 'Communication', section: 'strategy' },
];

export function countersFor(section: MatchFormSection): MatchCounterDef[] {
  return MATCH_COUNTER_FIELDS.filter((field) => field.section === section);
}

export function scalesFor(section: MatchFormSection): MatchScaleDef[] {
  return MATCH_SCALE_FIELDS.filter((field) => field.section === section);
}

/** 2026 endgame states (keep in sync with backend season template). */
export const ENDGAME_MODES = [
  { value: 'none', label: 'None' },
  { value: 'kept_scoring', label: 'Kept scoring' },
  { value: 'parked', label: 'Parked' },
  { value: 'climb_level_1', label: 'Climb L1' },
  { value: 'climb_level_2', label: 'Climb L2' },
  { value: 'climb_level_3', label: 'Climb L3' },
] as const;

/** 2026 ranking points. */
export const RP_FLAGS = [
  { key: 'energized', label: 'Energized' },
  { key: 'supercharged', label: 'Supercharged' },
  { key: 'traversal', label: 'Traversal' },
  { key: 'coop', label: 'Co-op' },
] as const;

/* ------------------------------------------------------------------ */
/*  Pit scouting form schema                                           */
/* ------------------------------------------------------------------ */

export type PitFieldType = 'select' | 'number' | 'text' | 'textarea' | 'multiselect' | 'toggle';

export interface PitFieldDef {
  key: string;
  label: string;
  type: PitFieldType;
  options?: string[];
  placeholder?: string;
  unit?: string;
}

export interface PitFormSection {
  title: string;
  fields: PitFieldDef[];
}

export const PIT_FORM_SECTIONS: PitFormSection[] = [
  {
    title: 'Robot',
    fields: [
      {
        key: 'drivetrain',
        label: 'Drivetrain',
        type: 'select',
        options: ['Swerve', 'Tank / KOP', 'Mecanum', 'Other'],
      },
      { key: 'weight_lbs', label: 'Weight', type: 'number', unit: 'lbs' },
      {
        key: 'dimensions',
        label: 'Dimensions (L×W×H)',
        type: 'text',
        placeholder: 'e.g. 28×28×40 in',
      },
      {
        key: 'programming_language',
        label: 'Language',
        type: 'select',
        options: ['Java', 'C++', 'Python', 'LabVIEW', 'Other'],
      },
      { key: 'vision_system', label: 'Has vision / auto-align', type: 'toggle' },
    ],
  },
  {
    // Self-reported capabilities — what the team SAYS they can do when you ask
    // them at their pit. Labelled "Claimed" so it reads as their word, to be
    // compared later against what live scouting actually observes.
    title: 'Claimed Capabilities',
    fields: [
      {
        key: 'auto_capabilities',
        label: 'Claimed autonomous capabilities',
        type: 'multiselect',
        options: ['Mobility', 'Scores preload', 'Multi-piece auto', 'Picks from depot', 'Custom paths per station'],
      },
      {
        key: 'claimed_cycle_time_sec',
        label: 'Claimed cycle time',
        type: 'number',
        unit: 'sec',
      },
      {
        key: 'claimed_climb',
        label: 'Claimed climb',
        type: 'select',
        options: ['None', 'Park only', 'Level 1', 'Level 2', 'Level 3'],
      },
      {
        key: 'can_cross_bump',
        label: 'Claims they can cross bump',
        type: 'toggle',
      },
      {
        key: 'can_cross_trench',
        label: 'Claims they can cross trench',
        type: 'toggle',
      },
    ],
  },
  {
    title: 'Strategy',
    fields: [
      {
        key: 'preferred_role',
        label: 'Preferred role',
        type: 'select',
        options: ['Offense', 'Defense', 'Flex / either'],
      },
      {
        key: 'drive_team_experience',
        label: 'Drive team experience',
        type: 'select',
        options: ['Rookie', '1 season', '2+ seasons'],
      },
      {
        key: 'notes',
        label: 'Notes',
        type: 'textarea',
        placeholder: 'Build quality, spare parts, pit crew vibe, anything that matters Saturday…',
      },
    ],
  },
];
