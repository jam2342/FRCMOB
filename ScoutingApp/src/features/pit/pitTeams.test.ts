import { expect, it } from 'vitest';
import type { PitAssignment, PitScoutingEntry } from '../../api';
import { buildQueueRows, deepLinkTeam, hasPitEntry, nextUnscoutedTeam, pitStatus, pitUpdatedAgo, queueRowLabel, rowsForTab, searchQueueRows, tabCounts, teamNumber } from './pitTeams';

const team = (n: number, nickname: string | null = null) => ({ team_key: `frc${n}`, team_number: n, nickname });
const entry = (payload: Record<string, unknown>) => ({ payload, photos: [] }) as unknown as PitScoutingEntry;
const teams = [team(100, 'Alpha'), team(200, 'Bravo Bots'), team(300, 'Charlie'), team(400, null)];
const entries = new Map([['frc200', entry({ notes: 'x' })], ['frc300', entry({})]]);

it('counts an entry with an empty payload as unscouted', () => {
  expect(hasPitEntry(entries.get('frc200'))).toBe(true);
  expect(hasPitEntry(entries.get('frc300'))).toBe(false);
  expect(hasPitEntry(undefined)).toBe(false);
});

const none = { drafts: new Set<string>(), queued: new Set<string>() };
const assignment = (teamKey: string, memberId: number | null, name: string | null) =>
  ({ team_key: teamKey, member_id: memberId, member_name: name, source: 'claim', assigned_by_member_id: memberId, updated_at: null }) as PitAssignment;
const rowsFor = (assigned: PitAssignment[] = [], local = none, me: number | null = 7) =>
  buildQueueRows(teams, entries, new Map(assigned.map((a) => [a.team_key, a])), local, me);

it('derives a status: waiting to sync beats draft, draft beats done', () => {
  const local = { drafts: new Set(['frc100', 'frc200']), queued: new Set(['frc200', 'frc400']) };
  expect(pitStatus(undefined, 'frc300', none)).toBe('todo');
  expect(pitStatus(entries.get('frc200'), 'frc300', none)).toBe('done');
  expect(pitStatus(undefined, 'frc100', local)).toBe('draft');
  expect(pitStatus(entries.get('frc200'), 'frc200', local)).toBe('queued');
  expect(pitStatus(undefined, 'frc400', local)).toBe('queued');
});

it('sorts to-do teams first, then by number, and counts a queued save as done', () => {
  const rows = rowsFor([], { drafts: new Set(), queued: new Set(['frc100']) });
  expect(rows.map((r) => r.team.team_number)).toEqual([300, 400, 100, 200]);
  expect(rows.map((r) => r.done)).toEqual([false, false, true, true]);
});

it('splits rows into Mine, Left and Done with matching counts', () => {
  const rows = rowsFor([assignment('frc100', 7, 'Jamal'), assignment('frc300', 8, 'Alex'), assignment('frc200', 7, 'Jamal')]);
  expect(rowsForTab(rows, 'mine').map((r) => r.team.team_number)).toEqual([100]);
  expect(rowsForTab(rows, 'left').map((r) => r.team.team_number)).toEqual([100, 300, 400]);
  expect(rowsForTab(rows, 'done').map((r) => r.team.team_number)).toEqual([200]);
  expect(tabCounts(rows)).toEqual({ mine: 1, left: 3, done: 1 });
});

it('ignores an assignment row with no member', () => {
  const [first] = rowsFor([assignment('frc300', null, null)]);
  expect(first.assigneeId).toBeNull();
});

it('searches by number or nickname and keeps the open team', () => {
  const rows = rowsFor();
  const run = (query: string, keep?: string) => searchQueueRows(rows, query, keep).map((r) => r.team.team_number);
  expect(run('bravo')).toEqual([200]);
  expect(run('frc3')).toEqual([300]);
  expect(run('0')).toEqual([100, 300, 400, 200]);
  expect(run('zzz', 'frc400')).toEqual([400]);
});

it('says how long ago a team was updated', () => {
  const now = Date.parse('2026-11-10T12:00:00Z');
  expect(pitUpdatedAgo('2026-11-10T11:48:00Z', now)).toBe('12 min ago');
  expect(pitUpdatedAgo('2026-11-10T11:59:50Z', now)).toBe('just now');
  expect(pitUpdatedAgo('2026-11-10T09:00:00Z', now)).toBe('3 h ago');
  expect(pitUpdatedAgo('2026-11-07T12:00:00Z', now)).toBe('3 d ago');
  expect(pitUpdatedAgo(null, now)).toBeNull();
});

it('reads a row as one sentence for screen readers', () => {
  const rows = rowsFor([assignment('frc300', 8, 'Alex'), assignment('frc100', 7, 'Jamal')]);
  const label = (n: number) => queueRowLabel(rows.find((r) => r.team.team_number === n)!);
  expect(label(300)).toBe('Team 300, Charlie, assigned to Alex, to do');
  expect(label(100)).toBe('Team 100, Alpha, assigned to you, to do');
  expect(label(400)).toBe('Team 400, not assigned, to do');
  expect(label(200)).toBe('Team 200, Bravo Bots, not assigned, done');
});

it('picks the next unscouted team after this one, wrapping to the start', () => {
  expect(nextUnscoutedTeam(teams, entries, 'frc100')).toBe('frc300');
  expect(nextUnscoutedTeam(teams, entries, 'frc400')).toBe('frc100');
  expect(nextUnscoutedTeam(teams, new Map(teams.map((t) => [t.team_key, entry({ a: 1 })])), 'frc100')).toBeNull();
});

it('accepts a deep-link team only when it is well formed and in the event', () => {
  expect(deepLinkTeam('frc300', teams)).toBe('frc300');
  expect(deepLinkTeam(' FRC300 ', teams)).toBe('frc300');
  expect(deepLinkTeam('frc999', teams)).toBeNull();
  expect(deepLinkTeam('300', teams)).toBeNull();
  expect(deepLinkTeam('frc300;drop', teams)).toBeNull();
  expect(deepLinkTeam(null, teams)).toBeNull();
});

const bTeams = [team(100, 'Alpha'), { team_key: 'frc100b', team_number: 100, nickname: 'Alpha B' }, team(101, null)];

it('opens a B team from a deep link in any letter case', () => {
  expect(deepLinkTeam('frc100b', bTeams)).toBe('frc100b');
  expect(deepLinkTeam(' FRC100B ', bTeams)).toBe('frc100b');
  expect(deepLinkTeam('frc101b', bTeams)).toBeNull();
  expect(deepLinkTeam('frc100bb', bTeams)).toBeNull();
});

it('shows a B team as "100B", sorts it after its main team and finds it by search', () => {
  expect(teamNumber('frc100b')).toBe('100B');
  expect(teamNumber('frc254')).toBe('254');
  const rows = buildQueueRows([bTeams[1], bTeams[2], bTeams[0]], new Map(), new Map(), none, 7);
  expect(rows.map((r) => r.team.team_key)).toEqual(['frc100', 'frc100b', 'frc101']);
  expect(queueRowLabel(rows[1])).toBe('Team 100B, Alpha B, not assigned, to do');
  expect(searchQueueRows(rows, '100b').map((r) => r.team.team_key)).toEqual(['frc100b']);
  expect(searchQueueRows(rows, 'frc100B').map((r) => r.team.team_key)).toEqual(['frc100b']);
  expect(searchQueueRows(rows, '100').map((r) => r.team.team_key)).toEqual(['frc100', 'frc100b']);
});
