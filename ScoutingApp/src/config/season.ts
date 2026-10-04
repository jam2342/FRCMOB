// The frontend's one source of season facts. It is a static snapshot rather than a
// fetch because scouting runs offline at events; season.test.ts fails whenever it
// drifts from backend/app/game_config/season_template.json, so a new game is a
// change in both places or neither.
export type Season = {
  readonly year: number;
  readonly fallbackYear: number;
  readonly name: string;
  readonly matchSec: number;
  readonly autoSec: number;
  readonly endgameSec: number;
  readonly fieldLengthM: number;
  readonly fieldWidthM: number;
};

// Typed as numbers, not literals: a countdown seeded from matchSec must accept any value.
export const SEASON: Season = {
  year: 2026,
  fallbackYear: 2025,
  name: 'REBUILT',
  matchSec: 160,
  autoSec: 20,
  endgameSec: 30,
  fieldLengthM: 16.541,
  fieldWidthM: 8.0693,
};

// What a top REBUILT robot does, used to scale Team Center's bars so an elite
// robot fills one. Fuel is teleop fuel per minute of the robot's own hub being
// active — the one fuel-rate unit the app uses, the same one the official ingest
// stores as fuel_scoring_rate. Across 97 events of TBA component OPRs (2026-09-28)
// that is p50 21, p99 147, max 238; 254 sits near 170. Auto: 75 points
// (p99 54, max 84; docs/REBUILT_PLAUSIBILITY.md).
export const SEASON_BENCHMARKS = {
  eliteFuelPerActiveMin: 170,
  eliteAutoPoints: 75,
} as const;

// Where a scouted robot's points sit on the overall scout rating: the midpoint is a
// typical 2026 robot (Statbotics season p50 per robot) and the spread puts a p90
// robot near the top. The old midpoints (4.5 auto, 16 teleop, 27 total) came from an
// earlier game, so nearly every REBUILT robot read as maximum.
export const SCOUT_POINTS_SCALE = {
  auto: { midpoint: 9, spread: 5.5 },
  teleop: { midpoint: 28, spread: 24 },
  total: { midpoint: 37, spread: 30 },
} as const;
