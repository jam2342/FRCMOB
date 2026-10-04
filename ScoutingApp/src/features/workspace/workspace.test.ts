import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Modules are re-imported per test (vi.resetModules) so the session store and
// the API client's caches start empty each time.
async function load() {
  const session = await import('./workspaceSession');
  const api = await import('../../api');
  return { session, api };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const workspaceBody = (id: number, name: string, token?: string, joinCode?: string) => ({
  ok: true,
  workspace: { id, name, frc_team_number: 118, join_code_rotated_at: null, created_at: null },
  me: { id: 10, display_name: 'Ann', role: 'leader', joined_at: null, last_seen_at: null },
  members: [],
  ...(joinCode ? { join_code: joinCode } : {}),
  ...(token ? { access: { token, expires_at_unix: Math.floor(Date.now() / 1000) + 3600 } } : {}),
});

describe('team workspace session', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_API_URL', '/api');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
    window.localStorage.clear();
  });

  it('creating a workspace signs the device in and keeps the issued code for the team view', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(workspaceBody(7, 'Robonauts', 'tok-7', 'ABCDE-12345')));
    vi.stubGlobal('fetch', fetchMock);
    const { session, api } = await load();

    await api.createWorkspace({ name: 'Robonauts', display_name: 'Ann' });

    expect(session.getWorkspaceToken()).toBe('tok-7');
    expect(session.getWorkspaceSession()?.workspace.name).toBe('Robonauts');
    expect(session.issuedJoinCodeFor(7)).toBe('ABCDE-12345');
    expect(session.issuedJoinCodeFor(8)).toBeNull();
    // Stored for the next visit, but never the join code.
    const stored = window.localStorage.getItem('frcmob_workspace_session_v1') ?? '';
    expect(stored).toContain('tok-7');
    expect(stored).not.toContain('ABCDE');
  });

  it('sends workspace access on every request and on the room socket', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true, picklists: [], count: 0, event_key: '2026txhou' }));
    vi.stubGlobal('fetch', fetchMock);
    const { session, api } = await load();
    session.setWorkspaceSession({
      token: 'tok-1',
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
      workspace: { id: 1, name: 'A', frc_team_number: null },
      me: { id: 1, display_name: 'Ann', role: 'member' },
    });

    await api.listPicklists('2026txhou');

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get('X-Workspace-Access')).toBe('tok-1');
    const socketUrl = new URL(api.scoutingRoomWebSocketUrl('room-a', { scout_profile: 'Ann' }));
    expect(socketUrl.searchParams.get('workspace_access')).toBe('tok-1');
    // The socket never reads an admin session, so one must never ride its URL.
    expect(socketUrl.searchParams.has('admin_session')).toBe(false);
  });

  it("never serves one team's cached data to another team", async () => {
    const fetchMock = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) =>
      json({ summary: { covered_slots: new Headers(init?.headers).get('X-Workspace-Access') === 'tok-1' ? 5 : 0 } }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { session, api } = await load();
    const signIn = (id: number) =>
      session.setWorkspaceSession({
        token: `tok-${id}`,
        expiresAt: Math.floor(Date.now() / 1000) + 3600,
        workspace: { id, name: `Team ${id}`, frc_team_number: null },
        me: { id, display_name: 'Ann', role: 'leader' },
      });

    // Coverage is cached for 15 s by URL. Same URL, two teams: team 2 must get
    // its own answer from the server, not team 1's cached one.
    signIn(1);
    const first = await api.getScoutingCoverage('2026txhou');
    signIn(2);
    const second = await api.getScoutingCoverage('2026txhou');

    expect(first.summary.covered_slots).toBe(5);
    expect(second.summary.covered_slots).toBe(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('a 401 on the current token signs the device out as revoked', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ detail: 'Your workspace access is no longer valid.' }, 401));
    vi.stubGlobal('fetch', fetchMock);
    const { session, api } = await load();
    session.setWorkspaceSession({
      token: 'tok-gone',
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
      workspace: { id: 3, name: 'C', frc_team_number: null },
      me: { id: 3, display_name: 'Ann', role: 'member' },
    });
    const listener = vi.fn();
    session.subscribeWorkspaceSession(listener);

    await expect(api.listPicklists('2026txhou')).rejects.toThrow();

    expect(session.getWorkspaceSession()).toBeNull();
    expect(session.workspaceEndReason()).toBe('revoked');
    expect(listener).toHaveBeenCalled();
  });

  it('a reload after removal still says the device was signed out', async () => {
    const first = await load();
    first.session.setWorkspaceSession({
      token: 'tok-9',
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
      workspace: { id: 9, name: 'I', frc_team_number: null },
      me: { id: 9, display_name: 'Ann', role: 'member' },
    });
    first.session.clearWorkspaceSession('revoked');

    vi.resetModules();
    const reloaded = await load();
    expect(reloaded.session.getWorkspaceSession()).toBeNull();
    expect(reloaded.session.workspaceEndReason()).toBe('revoked');
  });

  it("keeps each team's scouting entries apart on one device", async () => {
    const { readStoredEntries, scoutingEntriesStorageKey } = await import('../../pages/scoutingPage.helpers');
    const entry = (id: string, room_key: string) => ({
      id, created_at: '2026-01-01T00:00:00Z', event_key: '2026txhou', match_key: '2026txhou_qm1',
      team_key: 'frc118', scout_profile: 'Ann', room_key, form: {},
    });
    // Before joining any team: one personal note, one entry from team A's room.
    window.localStorage.setItem(
      scoutingEntriesStorageKey(null),
      JSON.stringify([entry('mine', ''), entry('from-room-a', 'room-a')]),
    );
    window.localStorage.setItem(scoutingEntriesStorageKey(1), JSON.stringify([entry('team-a', 'room-a')]));

    expect(readStoredEntries(1).map((e) => e.id)).toEqual(['team-a']);
    // A new team on this device starts with the scout's own notes only.
    expect(readStoredEntries(2).map((e) => e.id)).toEqual(['mine']);
  });

  it('an expired session is dropped on read', async () => {
    const { session } = await load();
    session.setWorkspaceSession({
      token: 'tok-old',
      expiresAt: Math.floor(Date.now() / 1000) - 1,
      workspace: { id: 4, name: 'D', frc_team_number: null },
      me: { id: 4, display_name: 'Ann', role: 'member' },
    });
    expect(session.getWorkspaceSession()).toBeNull();
    expect(session.workspaceEndReason()).toBe('expired');
  });

  it('leaving clears the session and the stored token', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(workspaceBody(5, 'E', 'tok-5')))
      .mockResolvedValueOnce(json({ ok: true, left_workspace_id: 5 }));
    vi.stubGlobal('fetch', fetchMock);
    const { session, api } = await load();
    await api.joinWorkspace({ join_code: 'ABCDE-12345', display_name: 'Ann' });
    expect(session.getWorkspaceToken()).toBe('tok-5');

    await api.leaveWorkspace();

    expect(session.getWorkspaceSession()).toBeNull();
    expect(window.localStorage.getItem('frcmob_workspace_session_v1')).toBeNull();
    expect(session.workspaceEndReason()).toBe('left');
  });
});
