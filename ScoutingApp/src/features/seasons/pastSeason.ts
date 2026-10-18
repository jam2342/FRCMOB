import { FIRST_RATED_SEASON, SEASON, SEASON_GAMES, seasonOfEventKey } from '../../config/season';

// What a page about an earlier season's event says first: its ratings come from a
// different game and never compare with this season's. Null for this season.
export function pastSeasonNote(eventKey: string | null | undefined, currentYear: number = SEASON.year): string | null {
  const year = seasonOfEventKey(eventKey);
  if (year === null || year >= currentYear) return null;
  const game = SEASON_GAMES[year];
  const label = game ? `${year} season · ${game}` : `${year} season`;
  if (year < FIRST_RATED_SEASON) {
    return `${label}. FRCMOB ratings start in ${FIRST_RATED_SEASON}, so this event has results but no ratings or predictions.`;
  }
  return `${label} · final ratings. They don't compare with ${currentYear}.`;
}
