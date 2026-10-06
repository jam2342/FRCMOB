import { describe, expect, it } from 'vitest';
import type { EventScheduleItem, TeamRoomAssignment, TeamRoomAssignmentChange } from '../../api';
import {
  clearUpcomingChanges,
  coverageSummary,
  myAssignments,
  nextAssignment,
  planAutoAssign,
} from './teamAssignments';

function match(number: number, completed = false, base = 100): EventScheduleItem {
  const team = (offset: number) => ({ team_key: `frc${base + number * 10 + offset}`, station: '' });
  return {
    match_key: `2026test_qm${number}`,
    display_name: `QM ${number}`,
    comp_level: 'qm',
    set_number: 1,
    match_number: number,
    scheduled_time: null,
    has_time: false,
    is_completed: completed,
    red: [team(1), team(2), team(3)],
    blue: [team(4), team(5), team(6)],
  } as EventScheduleItem;
}

function row(matchNumber: number, offset: number, memberId: number | null, extra: Partial<TeamRoomAssignment> = {}): TeamRoomAssignment {
  return {
    match_key: `2026test_qm${matchNumber}`,
    team_key: `frc${100 + matchNumber * 10 + offset}`,
    assigned_member_id: memberId,
    assigned_display_name: memberId ? `Scout ${memberId}` : '',
    member_active: true,
    covered: false,
    covered_by_me: false,
    ...extra,
  };
}

function apply(rows: TeamRoomAssignment[], changes: TeamRoomAssignmentChange[]): Map<string, number | null> {
  const result = new Map<string, number | null>();
  for (const item of rows) result.set(`${item.match_key}:${item.team_key}`, item.member_active ? item.assigned_member_id : null);
  for (const change of changes) result.set(`${change.match_key}:${change.team_key}`, change.assigned_member_id);
  return result;
}

function perMatchCounts(schedule: EventScheduleItem[], state: Map<string, number | null>) {
  return schedule.map((m) => {
    const ids = [...m.red, ...m.blue]
      .map((team) => state.get(`${m.match_key}:${team.team_key}`) ?? null)
      .filter((id): id is number => id !== null);
    return { ids, unique: new Set(ids).size };
  });
}

describe('planAutoAssign', () => {
  const schedule = [match(1), match(2), match(3), match(4)];

  it('never gives one scout two teams in the same match when there are fewer than six', () => {
    const changes = planAutoAssign(schedule, [], [1, 2, 3], 'fill');
    const counts = perMatchCounts(schedule, apply([], changes));
    for (const item of counts) {
      expect(item.ids).toHaveLength(3);
      expect(item.unique).toBe(3);
    }
  });

  it('spreads the work evenly', () => {
    const changes = planAutoAssign(schedule, [], [1, 2, 3, 4, 5, 6, 7], 'fill');
    const load = new Map<number, number>();
    for (const change of changes) load.set(change.assigned_member_id!, (load.get(change.assigned_member_id!) ?? 0) + 1);
    const values = [...load.values()];
    expect(changes).toHaveLength(24);
    expect(Math.max(...values) - Math.min(...values)).toBeLessThanOrEqual(1);
  });

  it('keeps existing assignments in fill mode and works around them', () => {
    const existing = [row(1, 1, 1), row(1, 2, 2)];
    const changes = planAutoAssign(schedule, existing, [1, 2, 3, 4, 5, 6], 'fill');
    expect(changes.some((c) => c.match_key === '2026test_qm1' && (c.team_key === 'frc111' || c.team_key === 'frc112'))).toBe(false);
    const state = apply(existing, changes);
    expect(perMatchCounts(schedule, state)[0].unique).toBe(6);
  });

  it('skips played matches', () => {
    const changes = planAutoAssign([match(1, true), match(2)], [], [1, 2, 3, 4, 5, 6], 'fill');
    expect(changes.every((c) => c.match_key === '2026test_qm2')).toBe(true);
  });

  it('redo mode clears a slot rather than leaving a double booking', () => {
    // Scout 1 is the only scout left: they take the first slot and their old
    // slot is cleared, so nobody holds two teams in the match.
    const existing = [row(1, 1, 2), row(1, 2, 1)];
    const changes = planAutoAssign([match(1)], existing, [1], 'redo');
    const state = apply(existing, changes);
    const ids = perMatchCounts([match(1)], state)[0].ids;
    expect(ids).toEqual([1]);
  });

  it("leaves a removed scout's slot flagged when nobody is free", () => {
    const existing = [row(1, 1, 9, { member_active: false })];
    const changes = planAutoAssign([match(1)], existing, [], 'fill');
    expect(changes).toEqual([]);
    expect(coverageSummary(existing, [match(1)]).needsReassignment).toBe(1);
  });

  it('reassigns a removed scout\'s slot when someone is free', () => {
    const existing = [row(1, 1, 9, { member_active: false })];
    const changes = planAutoAssign([match(1)], existing, [3], 'fill');
    expect(changes).toContainEqual({ match_key: '2026test_qm1', team_key: 'frc111', assigned_member_id: 3 });
  });
});

describe('my assignments', () => {
  it('orders by schedule, marks done, and finds the next one', () => {
    const schedule = [match(3), match(1), match(2, true)];
    const rows = [row(3, 4, 7), row(1, 1, 7, { covered_by_me: true }), row(2, 2, 7), row(1, 2, 8)];
    const mine = myAssignments(7, rows, schedule);
    expect(mine.map((m) => [m.match_label, m.alliance, m.team_number, m.done])).toEqual([
      ['QM 1', 'red', '111', true],
      ['QM 2', 'red', '122', true],
      ['QM 3', 'blue', '134', false],
    ]);
    expect(nextAssignment(mine)?.match_label).toBe('QM 3');
  });
});

describe('coverage and clearing', () => {
  it('counts only active assignments in upcoming matches', () => {
    const schedule = [match(1, true), match(2)];
    const rows = [row(1, 1, 1), row(2, 1, 1), row(2, 2, 5, { member_active: false })];
    expect(coverageSummary(rows, schedule)).toEqual({ upcomingSlots: 6, assignedSlots: 1, needsReassignment: 1 });
  });

  it('clears upcoming slots only', () => {
    const schedule = [match(1, true), match(2)];
    const rows = [row(1, 1, 1), row(2, 1, 1)];
    expect(clearUpcomingChanges(schedule, rows)).toEqual([
      { match_key: '2026test_qm2', team_key: 'frc121', assigned_member_id: null },
    ]);
  });
});
