// Whether a match has a result, shared by Events, Match Center and Scouting (each
// had its own copy, and Scouting's counted TBA's -1 "not played" scores as a result,
// so a late-running match could read as finished there and upcoming elsewhere).
import type { EventScheduleItem } from '../api';
import { liveTimerLabel, matchStartTime } from './centerUtils';

export function matchHasScores(match: EventScheduleItem | null): boolean {
  if (!match) return false;
  return (
    typeof match.red_score === 'number' &&
    typeof match.blue_score === 'number' &&
    Number.isFinite(match.red_score) &&
    Number.isFinite(match.blue_score) &&
    match.red_score >= 0 &&
    match.blue_score >= 0
  );
}

export function inferMatchCompleted(match: EventScheduleItem | null, nowMs: number): boolean {
  if (!match) return false;
  const winner = match.winner_alliance || null;
  return (
    Boolean(match.is_completed) ||
    winner === 'red' ||
    winner === 'blue' ||
    winner === 'tie' ||
    (matchHasScores(match) && liveTimerLabel(matchStartTime(match), nowMs).state === 'ended')
  );
}
