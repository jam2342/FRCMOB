import type { PitAssignment, PitScoutingEntry } from '../../api';

export type PitTeam = {
  team_key: string;
  team_number: number;
  nickname: string | null;
};

// "frc100b" -> "100B": a B team's letter reads as a capital, like the stored key.
export function teamNumber(teamKey: string): string {
  return teamKey.replace(/^frc/i, '').toUpperCase();
}

// Team 100 before 100B before 101; the plain number alone ties.
export function compareTeams(a: PitTeam, b: PitTeam): number {
  return a.team_number - b.team_number || a.team_key.localeCompare(b.team_key);
}

export function hasPitEntry(entry: PitScoutingEntry | null | undefined): boolean {
  return Boolean(entry && Object.keys(entry.payload ?? {}).length > 0);
}

export type PitStatus = 'todo' | 'draft' | 'queued' | 'done';
export type PitTab = 'mine' | 'left' | 'done';

export const PIT_STATUS_LABEL: Record<PitStatus, string> = {
  todo: 'To do',
  draft: 'Draft on this phone',
  queued: 'Waiting to sync',
  done: 'Done',
};

// What this phone holds that the server may not: unsaved drafts and saves waiting in the offline queue.
export type PitLocalState = { drafts: ReadonlySet<string>; queued: ReadonlySet<string> };

export type PitQueueRow = {
  team: PitTeam;
  status: PitStatus;
  // A save waiting to sync counts as done: the scout finished the robot, only the network is late.
  done: boolean;
  hasPhotos: boolean;
  // Answers two scouts saved differently, waiting for someone to pick.
  conflicts: number;
  assigneeId: number | null;
  assigneeName: string | null;
  mine: boolean;
  updatedAt: string | null;
};

export function pitStatus(entry: PitScoutingEntry | null | undefined, teamKey: string, local: PitLocalState): PitStatus {
  if (local.queued.has(teamKey)) return 'queued';
  if (local.drafts.has(teamKey)) return 'draft';
  return hasPitEntry(entry) ? 'done' : 'todo';
}

// Not-done teams first so the next robot to visit is at the top; each group stays in number order.
export function buildQueueRows(
  teams: PitTeam[],
  entries: Map<string, PitScoutingEntry>,
  assignments: Map<string, PitAssignment>,
  local: PitLocalState,
  meId: number | null,
): PitQueueRow[] {
  const rows = teams.map((team): PitQueueRow => {
    const entry = entries.get(team.team_key);
    const assignment = assignments.get(team.team_key);
    const status = pitStatus(entry, team.team_key, local);
    const assigneeId = assignment?.member_id ?? null;
    return {
      team,
      status,
      done: hasPitEntry(entry) || status === 'queued',
      hasPhotos: Boolean(entry?.photos?.length),
      conflicts: entry?.conflicts?.length ?? 0,
      assigneeId,
      assigneeName: assigneeId === null ? null : assignment?.member_name?.trim() || 'a teammate',
      mine: meId !== null && assigneeId === meId,
      updatedAt: entry?.updated_at ?? null,
    };
  });
  return rows.sort((a, b) => Number(a.done) - Number(b.done) || compareTeams(a.team, b.team));
}

export function rowsForTab(rows: PitQueueRow[], tab: PitTab): PitQueueRow[] {
  if (tab === 'done') return rows.filter((row) => row.done);
  return rows.filter((row) => !row.done && (tab === 'left' || row.mine));
}

export function tabCounts(rows: PitQueueRow[]): Record<PitTab, number> {
  return {
    mine: rowsForTab(rows, 'mine').length,
    left: rowsForTab(rows, 'left').length,
    done: rowsForTab(rows, 'done').length,
  };
}

export function searchQueueRows(rows: PitQueueRow[], query: string, keepKey?: string): PitQueueRow[] {
  const needle = query.trim().toLowerCase().replace(/^#|^frc/, '');
  if (!needle) return rows;
  return rows.filter(
    (row) =>
      row.team.team_key === keepKey ||
      teamNumber(row.team.team_key).toLowerCase().includes(needle) ||
      (row.team.nickname ?? '').toLowerCase().includes(needle),
  );
}

export function pitUpdatedAgo(iso: string | null, nowMs: number = Date.now()): string | null {
  const then = Date.parse(iso ?? '');
  if (!Number.isFinite(then)) return null;
  const minutes = Math.max(0, Math.floor((nowMs - then) / 60000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

// Read aloud as one line: "Team 254, assigned to Alex, to do".
export function queueRowLabel(row: PitQueueRow, extras: { selected?: boolean } = {}): string {
  const who = row.mine ? 'assigned to you' : row.assigneeName ? `assigned to ${row.assigneeName}` : 'not assigned';
  return [
    `Team ${teamNumber(row.team.team_key)}`,
    row.team.nickname,
    who,
    PIT_STATUS_LABEL[row.status].toLowerCase(),
    row.hasPhotos ? 'has photos' : null,
    row.conflicts ? `${row.conflicts} answer${row.conflicts === 1 ? '' : 's'} differ` : null,
    extras.selected ? 'open now' : null,
  ].filter(Boolean).join(', ');
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

// `?team=frc254` from Team Center, Compare and the picklist: only a well-formed key that is in this event opens.
export function deepLinkTeam(raw: string | null, teams: PitTeam[]): string | null {
  const key = (raw ?? '').trim().toLowerCase();
  if (!/^frc\d{1,5}[a-z]?$/.test(key)) return null;
  return teams.some((team) => team.team_key === key) ? key : null;
}
