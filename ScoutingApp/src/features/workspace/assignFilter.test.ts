import { describe, expect, it } from 'vitest';
import type { EventScheduleItem, TeamRoomAssignment } from '../../api';
import { defaultAssignFilter, filterCounts, filterMatches } from './assignFilter';
import { assignmentIndex } from './teamAssignments';

function match(n: number, completed = false): EventScheduleItem {
  const team = (k: number) => ({ team_key: `frc${n * 10 + k}`, station: '' });
  return {
    match_key: `e_qm${n}`, display_name: `QM ${n}`, comp_level: 'qm', set_number: 1, match_number: n,
    scheduled_time: null, has_time: false, is_completed: completed,
    red: [team(1), team(2), team(3)], blue: [team(4), team(5), team(6)],
  } as EventScheduleItem;
}

function row(n: number, k: number, id: number | null, active = true): TeamRoomAssignment {
  return {
    match_key: `e_qm${n}`, team_key: `frc${n * 10 + k}`, assigned_member_id: id,
    assigned_display_name: id ? 'X' : '', member_active: active, covered: false, covered_by_me: false,
  };
}

function fullyAssigned(n: number, id: number) {
  return [1, 2, 3, 4, 5, 6].map((k) => row(n, k, id));
}

const schedule = [match(1, true), match(2), match(3), match(4), match(5), match(6), match(7), match(8)];
const numbers = (list: EventScheduleItem[]) => list.map((m) => m.match_number);

describe('assignment filters', () => {
  const rows = [...fullyAssigned(2, 7), ...fullyAssigned(3, 8), row(4, 1, 7), ...fullyAssigned(1, 7)];
  const index = assignmentIndex(rows);

  it('mine keeps upcoming matches where I hold a slot', () => {
    expect(numbers(filterMatches(schedule, index, 'mine', 7))).toEqual([2, 4]);
  });

  it('unassigned keeps upcoming matches with an open slot, treating a removed scout as open', () => {
    const withRemoved = assignmentIndex([...rows, ...[1, 2, 3, 4, 5, 6].map((k) => row(5, k, 7, k !== 3))]);
    expect(numbers(filterMatches(schedule, withRemoved, 'unassigned', 7))).toEqual([4, 5, 6, 7, 8]);
  });

  it('next 5 is the first five unplayed matches', () => {
    expect(numbers(filterMatches(schedule, index, 'next5', 7))).toEqual([2, 3, 4, 5, 6]);
  });

  it('played matches only show under all, after the upcoming ones', () => {
    expect(numbers(filterMatches(schedule, index, 'all', 7))).toEqual([2, 3, 4, 5, 6, 7, 8, 1]);
    for (const filter of ['mine', 'unassigned', 'next5'] as const) {
      expect(numbers(filterMatches(schedule, index, filter, 7))).not.toContain(1);
    }
  });

  it('counts each tab', () => {
    expect(filterCounts(schedule, index, 7)).toEqual({ mine: 2, unassigned: 5, next5: 5, all: 8 });
  });

  it('a leader lands on unassigned and a member on mine', () => {
    expect(defaultAssignFilter(true)).toBe('unassigned');
    expect(defaultAssignFilter(false)).toBe('mine');
  });
});
