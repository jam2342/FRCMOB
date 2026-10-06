import type { EventScheduleItem, TeamRoomAssignment, TeamRoomAssignmentChange } from '../../api';
import { matchStartTime } from '../../pages/centerUtils';

export type Alliance = 'red' | 'blue';

export type SlotRef = { match_key: string; team_key: string };

export type MyAssignment = {
  match_key: string;
  team_key: string;
  match_label: string;
  team_number: string;
  alliance: Alliance;
  start_time: number | null;
  done: boolean;
};

const COMP_LEVEL_ORDER: Record<string, number> = { qm: 0, ef: 1, qf: 2, sf: 3, f: 4 };

export function slotKey(matchKey: string, teamKey: string): string {
  return `${matchKey.toLowerCase()}:${teamKey.toLowerCase()}`;
}

export function teamNumber(teamKey: string): string {
  return teamKey.replace(/^frc/i, '');
}

export function sortSchedule(schedule: EventScheduleItem[]): EventScheduleItem[] {
  return [...schedule].sort((a, b) => {
    const level = (COMP_LEVEL_ORDER[a.comp_level] ?? 9) - (COMP_LEVEL_ORDER[b.comp_level] ?? 9);
    if (level) return level;
    if (a.set_number !== b.set_number) return a.set_number - b.set_number;
    return a.match_number - b.match_number;
  });
}

export function matchSlots(match: EventScheduleItem): Array<SlotRef & { alliance: Alliance }> {
  return [
    ...match.red.map((team) => ({ match_key: match.match_key, team_key: team.team_key, alliance: 'red' as const })),
    ...match.blue.map((team) => ({ match_key: match.match_key, team_key: team.team_key, alliance: 'blue' as const })),
  ];
}

export function assignmentIndex(assignments: TeamRoomAssignment[]): Map<string, TeamRoomAssignment> {
  const index = new Map<string, TeamRoomAssignment>();
  for (const row of assignments) index.set(slotKey(row.match_key, row.team_key), row);
  return index;
}

export function myAssignments(
  memberId: number,
  assignments: TeamRoomAssignment[],
  schedule: EventScheduleItem[],
): MyAssignment[] {
  const mine = new Map<string, TeamRoomAssignment>();
  for (const row of assignments) {
    if (row.assigned_member_id === memberId) mine.set(slotKey(row.match_key, row.team_key), row);
  }
  const result: MyAssignment[] = [];
  for (const match of sortSchedule(schedule)) {
    for (const slot of matchSlots(match)) {
      const row = mine.get(slotKey(slot.match_key, slot.team_key));
      if (!row) continue;
      result.push({
        match_key: match.match_key,
        team_key: slot.team_key,
        match_label: match.display_name || match.match_key.toUpperCase(),
        team_number: teamNumber(slot.team_key),
        alliance: slot.alliance,
        start_time: matchStartTime(match),
        // A played match is done whether or not this scout got to it.
        done: row.covered_by_me || Boolean(match.is_completed),
      });
    }
  }
  return result;
}

export type CoverageSummary = {
  upcomingSlots: number;
  assignedSlots: number;
  needsReassignment: number;
};

export function coverageSummary(assignments: TeamRoomAssignment[], schedule: EventScheduleItem[]): CoverageSummary {
  const index = assignmentIndex(assignments);
  let upcomingSlots = 0;
  let assignedSlots = 0;
  let needsReassignment = 0;
  for (const match of schedule) {
    if (match.is_completed) continue;
    for (const slot of matchSlots(match)) {
      upcomingSlots += 1;
      const row = index.get(slotKey(slot.match_key, slot.team_key));
      if (!row) continue;
      if (row.member_active && row.assigned_member_id !== null) assignedSlots += 1;
      else needsReassignment += 1;
    }
  }
  return { upcomingSlots, assignedSlots, needsReassignment };
}

export type AutoAssignMode = 'fill' | 'redo';

// Upcoming matches only. Keeps working assignments in "fill" mode, gives each
// scout at most one team per match, and always hands the next slot to whoever
// has the fewest so far, breaking ties by who has rested longest.
export function planAutoAssign(
  schedule: EventScheduleItem[],
  assignments: TeamRoomAssignment[],
  memberIds: number[],
  mode: AutoAssignMode,
): TeamRoomAssignmentChange[] {
  const members = [...new Set(memberIds)];
  const index = assignmentIndex(assignments);
  const upcoming = sortSchedule(schedule).filter((match) => !match.is_completed);
  const load = new Map<number, number>(members.map((id) => [id, 0]));
  const lastMatch = new Map<number, number>(members.map((id) => [id, -1]));
  const keep = (row: TeamRoomAssignment | undefined): row is TeamRoomAssignment & { assigned_member_id: number } =>
    mode === 'fill' && Boolean(row?.member_active) && row?.assigned_member_id !== null && row?.assigned_member_id !== undefined;

  if (mode === 'fill') {
    upcoming.forEach((match, matchIndex) => {
      for (const slot of matchSlots(match)) {
        const row = index.get(slotKey(slot.match_key, slot.team_key));
        if (!keep(row) || !load.has(row.assigned_member_id)) continue;
        load.set(row.assigned_member_id, (load.get(row.assigned_member_id) ?? 0) + 1);
        lastMatch.set(row.assigned_member_id, Math.max(lastMatch.get(row.assigned_member_id) ?? -1, matchIndex));
      }
    });
  }

  const memberOrder = new Map(members.map((id, index) => [id, index]));
  const changes: TeamRoomAssignmentChange[] = [];
  upcoming.forEach((match, matchIndex) => {
    const slots = matchSlots(match);
    const busy = new Set<number>();
    for (const slot of slots) {
      const row = index.get(slotKey(slot.match_key, slot.team_key));
      if (keep(row)) busy.add(row.assigned_member_id);
    }
    for (const slot of slots) {
      const row = index.get(slotKey(slot.match_key, slot.team_key));
      if (keep(row)) continue;
      let pick: number | undefined;
      for (const id of members) {
        if (busy.has(id)) continue;
        if (pick === undefined || (
          (load.get(id) ?? 0) - (load.get(pick) ?? 0)
          || (lastMatch.get(id) ?? -1) - (lastMatch.get(pick) ?? -1)
          || memberOrder.get(id)! - memberOrder.get(pick)!
        ) < 0) pick = id;
      }
      const next = pick ?? null;
      if (next !== null) {
        busy.add(next);
        load.set(next, (load.get(next) ?? 0) + 1);
        lastMatch.set(next, matchIndex);
      }
      const current = row?.member_active ? row.assigned_member_id : null;
      // With nobody free, leave a removed scout's slot as is so it still reads
      // "Needs reassignment". An active scout's old slot is cleared in redo
      // mode, since they may now hold another team in this match.
      if (next === null && current === null) continue;
      if (current !== next) {
        changes.push({ match_key: slot.match_key, team_key: slot.team_key, assigned_member_id: next });
      }
    }
  });
  return changes;
}

export function clearUpcomingChanges(
  schedule: EventScheduleItem[],
  assignments: TeamRoomAssignment[],
): TeamRoomAssignmentChange[] {
  const upcoming = new Set(schedule.filter((match) => !match.is_completed).map((match) => match.match_key.toLowerCase()));
  return assignments
    .filter((row) => upcoming.has(row.match_key.toLowerCase()))
    .map((row) => ({ match_key: row.match_key, team_key: row.team_key, assigned_member_id: null }));
}

export function workloadByMember(assignments: TeamRoomAssignment[], schedule: EventScheduleItem[]): Map<number, number> {
  const upcoming = new Set(schedule.filter((match) => !match.is_completed).map((match) => match.match_key.toLowerCase()));
  const counts = new Map<number, number>();
  for (const row of assignments) {
    if (row.assigned_member_id === null || !upcoming.has(row.match_key.toLowerCase())) continue;
    counts.set(row.assigned_member_id, (counts.get(row.assigned_member_id) ?? 0) + 1);
  }
  return counts;
}

export function scoutLink(eventKey: string, item: SlotRef): string {
  const params = new URLSearchParams({ event: eventKey, match: item.match_key, team: item.team_key });
  return `/scouting?${params.toString()}`;
}
