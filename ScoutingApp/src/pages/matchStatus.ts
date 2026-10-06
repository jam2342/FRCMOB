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

// Results land every few minutes while an event is running, so pages that show
// them refresh only then: an unplayed match due within the window (a running-late
// schedule keeps "due" matches a few hours in the past).
const IN_PLAY_PAST_SEC = 3 * 3600;
const IN_PLAY_AHEAD_SEC = 12 * 3600;
export function eventHasMatchesInPlay(matches: readonly EventScheduleItem[] | null | undefined, nowMs: number): boolean {
  if (!matches?.length) return false;
  const nowSec = nowMs / 1000;
  return matches.some((match) => {
    if (inferMatchCompleted(match, nowMs)) return false;
    const start = matchStartTime(match);
    return start !== null && start >= nowSec - IN_PLAY_PAST_SEC && start <= nowSec + IN_PLAY_AHEAD_SEC;
  });
}
