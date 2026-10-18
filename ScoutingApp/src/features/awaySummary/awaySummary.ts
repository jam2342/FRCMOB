import type { EventScheduleItem } from '../../api';
import { matchStartTime } from '../../pages/centerUtils';
import { countdownLabel, findNextMatch, shortMatchLabel } from '../nextMatch/nextMatch';

// "While you were away": what finished on Home's day since you last looked. Home records when you
// leave (hide the app or the page); on the way back, results that came in meanwhile are summed up.

const LAST_SEEN_KEY = 'frcmob_home_last_seen';
const AWAY_MIN_MS = 10 * 60_000;
// A match's start is what we know; its result posts a few minutes later.
const RESULT_LAG_MS = 3 * 60_000;

export function readLastSeen(): number | null {
  try {
    const value = Number(window.localStorage.getItem(LAST_SEEN_KEY));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

export function markSeen(nowMs = Date.now()): void {
  try {
    window.localStorage.setItem(LAST_SEEN_KEY, String(nowMs));
  } catch {
    // Storage unavailable: no summary next time, nothing else lost.
  }
}

export type AwaySummary = {
  awayMs: number;
  newMatchKeys: string[];
  headline: string;
  details: string[];
};

function hasScores(match: EventScheduleItem): boolean {
  return typeof match.red_score === 'number' && typeof match.blue_score === 'number';
}

export function awayDuration(ms: number): string {
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 12 * 60) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }
  return '';
}

export function summarizeAway(
  matches: readonly EventScheduleItem[],
  lastSeenMs: number | null,
  nowMs: number,
  myTeamKey: string,
): AwaySummary | null {
  if (lastSeenMs === null || nowMs - lastSeenMs < AWAY_MIN_MS) return null;
  const fresh = matches
    .filter((match) => {
      if (!hasScores(match)) return false;
      const start = matchStartTime(match);
      return start !== null && start * 1000 >= lastSeenMs - RESULT_LAG_MS && start * 1000 <= nowMs;
    })
    .sort((a, b) => (matchStartTime(a) ?? 0) - (matchStartTime(b) ?? 0));
  if (fresh.length === 0) return null;

  const details = [`${fresh.length} result${fresh.length === 1 ? '' : 's'}`];
  const team = myTeamKey.toLowerCase();
  if (team) {
    const mine = [...fresh].reverse().find((match) => [...match.red, ...match.blue].some((slot) => slot.team_key === team));
    if (mine) {
      const onRed = mine.red.some((slot) => slot.team_key === team);
      const ours = onRed ? mine.red_score! : mine.blue_score!;
      const theirs = onRed ? mine.blue_score! : mine.red_score!;
      const verb = ours > theirs ? 'won' : ours < theirs ? 'lost' : 'tied';
      details.push(`you ${verb} ${shortMatchLabel(mine)} ${ours}–${theirs}`);
    }
    const next = findNextMatch(matches, team, nowMs);
    if (next) details.push(`next: ${shortMatchLabel(next.match)} ${countdownLabel(next.startMs, nowMs)}`);
  }
  const away = awayDuration(nowMs - lastSeenMs);
  return {
    awayMs: nowMs - lastSeenMs,
    newMatchKeys: fresh.map((match) => match.match_key),
    headline: away ? `While you were away (${away})` : 'Since your last visit',
    details,
  };
}
