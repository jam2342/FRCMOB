// The frontend's one source of season facts. It is a static snapshot rather than a
// fetch because scouting runs offline at events; season.test.ts fails whenever it
// drifts from backend/app/game_config/season_template.json, so a new game is a
// change in both places or neither.
export type Season = {
  readonly year: number;
  readonly fallbackYear: number;
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
  matchSec: 160,
  autoSec: 20,
  endgameSec: 30,
  fieldLengthM: 16.541,
  fieldWidthM: 8.0693,
};

// The game each season was played with (backend services/seasons.py SEASON_GAMES), and
// the first season FRCMOB rates: earlier seasons have results but no ratings.
export const SEASON_GAMES: Readonly<Record<number, string>> = {
  2022: 'RAPID REACT',
  2023: 'CHARGED UP',
  2024: 'CRESCENDO',
  2025: 'REEFSCAPE',
  2026: 'REBUILT',
};
export const FIRST_RATED_SEASON = 2026;

export function seasonOfEventKey(eventKey: string | null | undefined): number | null {
  const head = String(eventKey || '').trim().slice(0, 4);
  return /^\d{4}$/.test(head) ? Number(head) : null;
}

// "2026 · REBUILT", or just the year for a season we don't have a game name for.
export function seasonLabel(year: number): string {
  const game = SEASON_GAMES[year];
  return game ? `${year} · ${game}` : String(year);
}

// The match's phases in order, from the backend's shift_schedule. In REBUILT the hubs alternate
// through shifts 1-4; which alliance goes first comes from auto, so it isn't known in advance.
export type MatchPhase = { readonly key: string; readonly label: string; readonly startSec: number; readonly endSec: number };

export const MATCH_PHASES: readonly MatchPhase[] = [
  { key: 'auto', label: 'Auto', startSec: 0, endSec: 20 },
  { key: 'transition', label: 'Transition', startSec: 20, endSec: 30 },
  { key: 'shift_1', label: 'Shift 1', startSec: 30, endSec: 55 },
  { key: 'shift_2', label: 'Shift 2', startSec: 55, endSec: 80 },
  { key: 'shift_3', label: 'Shift 3', startSec: 80, endSec: 105 },
  { key: 'shift_4', label: 'Shift 4', startSec: 105, endSec: 130 },
  { key: 'endgame', label: 'Endgame', startSec: 130, endSec: 160 },
];

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
