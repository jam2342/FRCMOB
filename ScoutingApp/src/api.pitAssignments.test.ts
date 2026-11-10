import { afterEach, describe, expect, it, vi } from 'vitest';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('pit assignment client', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  async function load(fetchMock: ReturnType<typeof vi.fn>) {
    vi.stubEnv('VITE_API_URL', '/api');
    vi.stubGlobal('fetch', fetchMock);
    return import('./api');
  }

  const row = { team_key: 'frc254', member_id: 3, member_name: 'Alex', source: 'claim', assigned_by_member_id: 3, updated_at: null };

  it('lists assignments for an event', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true, assignments: [row] }));
    const api = await load(fetchMock);
    await expect(api.getPitAssignments('2026arc')).resolves.toEqual([row]);
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/pit-scouting/assignments?event_key=2026arc');
  });

  it('sends claim, release, set and auto-split bodies as the API expects', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => jsonResponse({ ok: true, assignment: row, assignments: [row], released: true }));
    const api = await load(fetchMock);
    await api.claimPitTeam('2026arc', 'frc254');
    await api.claimPitTeam('2026arc', 'frc254', { force: true });
    await api.releasePitTeam('2026arc', 'frc254');
    await api.setPitAssignment('2026arc', 'frc254', null);
    await expect(api.autoSplitPitTeams('2026arc', { teamKeys: ['frc254'], memberIds: [3] })).resolves.toEqual([row]);
    const calls = fetchMock.mock.calls.map(([url, init]) => [String(url), JSON.parse(String(init.body))]);
    expect(calls[0]).toEqual(['/api/pit-scouting/assignments/claim', { event_key: '2026arc', team_key: 'frc254' }]);
    expect(calls[1][1]).toEqual({ event_key: '2026arc', team_key: 'frc254', force: true });
    expect(calls[2][0]).toBe('/api/pit-scouting/assignments/release');
    expect(calls[3]).toEqual(['/api/pit-scouting/assignments/set', { event_key: '2026arc', team_key: 'frc254', member_id: null }]);
    expect(calls[4][1]).toEqual({
      event_key: '2026arc',
      team_keys: ['frc254'],
      member_ids: [3],
      only_unscouted: true,
      replace_existing: false,
    });
  });

  it('surfaces the server sentence when a team is already taken', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: 'frc254 is already taken by Alex.' }, 409));
    const api = await load(fetchMock);
    await expect(api.claimPitTeam('2026arc', 'frc254')).rejects.toThrow('frc254 is already taken by Alex.');
  });
});
