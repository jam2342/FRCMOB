import type { PitScoutingEntry } from '../../api';

export type PitTeam = {
  team_key: string;
  team_number: number;
  nickname: string | null;
};

export function teamNumber(teamKey: string): string {
  return teamKey.replace(/^frc/i, '');
}

export function hasPitEntry(entry: PitScoutingEntry | null | undefined): boolean {
  return Boolean(entry && Object.keys(entry.payload ?? {}).length > 0);
}

// Unscouted teams first so the next robot to visit is always at the top; each group stays in number order.
export function orderPitTeams(teams: PitTeam[], entries: Map<string, PitScoutingEntry>): PitTeam[] {
  const open = teams.filter((team) => !hasPitEntry(entries.get(team.team_key)));
  const done = teams.filter((team) => hasPitEntry(entries.get(team.team_key)));
  return [...open, ...done];
}

export function filterPitTeams(
  teams: PitTeam[],
  entries: Map<string, PitScoutingEntry>,
  { query, unscoutedOnly, keepKey }: { query: string; unscoutedOnly: boolean; keepKey?: string },
): PitTeam[] {
  const needle = query.trim().toLowerCase().replace(/^#|^frc/, '');
  return orderPitTeams(teams, entries).filter((team) => {
    if (team.team_key === keepKey) return true;
    if (unscoutedOnly && hasPitEntry(entries.get(team.team_key))) return false;
    if (!needle) return true;
    return String(team.team_number).includes(needle) || (team.nickname ?? '').toLowerCase().includes(needle);
  });
}

// The next team after this one in number order that has no notes yet, wrapping round to the start.
export function nextUnscoutedTeam(
  teams: PitTeam[],
  entries: Map<string, PitScoutingEntry>,
  currentKey: string,
): string | null {
  const index = teams.findIndex((team) => team.team_key === currentKey);
  const ring = [...teams.slice(index + 1), ...teams.slice(0, Math.max(index, 0))];
  return ring.find((team) => team.team_key !== currentKey && !hasPitEntry(entries.get(team.team_key)))?.team_key ?? null;
}
