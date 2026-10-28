import type { ScoutingCoverageResponse } from '../../api';
import { ordinal } from '../../utils/ordinal';

export type ScoutThanks = { headline: string; detail: string };

type Leaderboard = ScoutingCoverageResponse['leaderboard'];

// A scout's own share of the team's scouting at an event, said back to them: how many reports,
// their best run of matches in a row, and where that puts them on the team. Null before their
// first report. The headline thanks them once their assignments are all done.
export function scoutThanks(leaderboard: Leaderboard, me: string, allDone: boolean): ScoutThanks | null {
  const name = me.trim().toLowerCase();
  const sorted = [...leaderboard].sort((a, b) => b.entry_count - a.entry_count);
  const index = sorted.findIndex((row) => row.scout_profile.trim().toLowerCase() === name);
  if (index < 0 || sorted[index].entry_count <= 0) return null;
  const row = sorted[index];
  const reports = `${row.entry_count} report${row.entry_count === 1 ? '' : 's'}`;
  const streak = row.best_qual_streak >= 3 ? `, ${row.best_qual_streak} matches in a row` : '';
  // Ties share a place: two scouts on 12 are both "most".
  const place = sorted.filter((other) => other.entry_count > row.entry_count).length + 1;
  const standing = sorted.length < 2
    ? ''
    : place === 1
      ? ' The most on your team.'
      : place <= 3
        ? ` ${ordinal(place)} most on your team.`
        : '';
  return {
    headline: allDone ? 'Thank you for scouting!' : 'Your scouting here',
    detail: `${reports}${streak}.${standing}`,
  };
}
