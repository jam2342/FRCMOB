import { expect, it } from 'vitest';
import type { PitScoutingEntry } from '../../api';
import { filterPitTeams, hasPitEntry, nextUnscoutedTeam, orderPitTeams } from './pitTeams';

const team = (n: number, nickname: string | null = null) => ({ team_key: `frc${n}`, team_number: n, nickname });
const entry = (payload: Record<string, unknown>) => ({ payload, photos: [] }) as unknown as PitScoutingEntry;
const teams = [team(100, 'Alpha'), team(200, 'Bravo Bots'), team(300, 'Charlie'), team(400, null)];
const entries = new Map([['frc200', entry({ notes: 'x' })], ['frc300', entry({})]]);

it('counts an entry with an empty payload as unscouted', () => {
  expect(hasPitEntry(entries.get('frc200'))).toBe(true);
  expect(hasPitEntry(entries.get('frc300'))).toBe(false);
  expect(hasPitEntry(undefined)).toBe(false);
});

it('lists unscouted teams first, each group in number order', () => {
  expect(orderPitTeams(teams, entries).map((t) => t.team_number)).toEqual([100, 300, 400, 200]);
});

it('filters by number or nickname and keeps the selected team', () => {
  const run = (query: string, unscoutedOnly = false, keepKey?: string) =>
    filterPitTeams(teams, entries, { query, unscoutedOnly, keepKey }).map((t) => t.team_number);
  expect(run('bravo')).toEqual([200]);
  expect(run('frc3')).toEqual([300]);
  expect(run('0', true)).toEqual([100, 300, 400]);
  expect(run('', true, 'frc200')).toEqual([100, 300, 400, 200]);
});

it('picks the next unscouted team after this one, wrapping to the start', () => {
  expect(nextUnscoutedTeam(teams, entries, 'frc100')).toBe('frc300');
  expect(nextUnscoutedTeam(teams, entries, 'frc400')).toBe('frc100');
  expect(nextUnscoutedTeam(teams, new Map(teams.map((t) => [t.team_key, entry({ a: 1 })])), 'frc100')).toBeNull();
});
