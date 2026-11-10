import type { EventScheduleItem, TeamRoomAssignment } from '../../api';
import { matchSlots, slotKey } from './teamAssignments';

export type AssignFilter = 'mine' | 'unassigned' | 'next5' | 'all';

export const NEXT_MATCH_COUNT = 5;

export function defaultAssignFilter(isLeader: boolean): AssignFilter {
  return isLeader ? 'unassigned' : 'mine';
}

function activeAssignee(row: TeamRoomAssignment | undefined): number | null {
  return row && row.member_active ? row.assigned_member_id : null;
}

export function unassignedSlotCount(match: EventScheduleItem, index: Map<string, TeamRoomAssignment>): number {
  return matchSlots(match).filter(
    (slot) => activeAssignee(index.get(slotKey(slot.match_key, slot.team_key))) === null,
  ).length;
}

// Played matches only show under "all", after the upcoming ones. "Next 5" counts
// from the first match not yet played, in schedule order: the played flag follows
// the field when it runs late, where a clock guess would not.
export function filterMatches(
  schedule: EventScheduleItem[],
  index: Map<string, TeamRoomAssignment>,
  filter: AssignFilter,
  memberId: number,
): EventScheduleItem[] {
  const upcoming = schedule.filter((match) => !match.is_completed);
  if (filter === 'all') return [...upcoming, ...schedule.filter((match) => match.is_completed)];
  if (filter === 'next5') return upcoming.slice(0, NEXT_MATCH_COUNT);
  if (filter === 'unassigned') return upcoming.filter((match) => unassignedSlotCount(match, index) > 0);
  return upcoming.filter((match) => matchSlots(match).some(
    (slot) => activeAssignee(index.get(slotKey(slot.match_key, slot.team_key))) === memberId,
  ));
}

export function filterCounts(
  schedule: EventScheduleItem[],
  index: Map<string, TeamRoomAssignment>,
  memberId: number,
): Record<AssignFilter, number> {
  return {
    mine: filterMatches(schedule, index, 'mine', memberId).length,
    unassigned: filterMatches(schedule, index, 'unassigned', memberId).length,
    next5: filterMatches(schedule, index, 'next5', memberId).length,
    all: schedule.length,
  };
}
