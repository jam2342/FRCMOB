import { afterEach, describe, expect, it, vi } from 'vitest';

const enqueue = vi.fn();
vi.mock('./utils/offlineQueue', () => ({ enqueue: (...args: unknown[]) => enqueue(...args) }));

describe('queueing writes on flaky wifi', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
    enqueue.mockReset();
    enqueue.mockResolvedValue(undefined);
  });

  it('knows which writes are safe to replay', async () => {
    const { isReplaySafeWrite } = await import('./api');
    expect(isReplaySafeWrite('POST', '/api/scouting/rooms/room-ab12cd/entries')).toBe(true);
    expect(isReplaySafeWrite('POST', 'https://x.test/api/pit-scouting')).toBe(true);
    expect(isReplaySafeWrite('PUT', '/api/picklists/42')).toBe(true);
    expect(isReplaySafeWrite('POST', '/api/workspaces')).toBe(false);
    expect(isReplaySafeWrite('POST', '/api/picklists')).toBe(false);
    expect(isReplaySafeWrite('DELETE', '/api/picklists/42')).toBe(false);
  });

  it('queues a dropped pit save even though the browser still says online', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const api = await import('./api');
    await expect(
      api.upsertPitEntry({ event_key: '2026arc', team_key: 'frc254', payload: { drivetrain: 'swerve' } }),
    ).rejects.toThrow(/queued/);
    expect(enqueue).toHaveBeenCalledTimes(1);
    expect(enqueue.mock.calls[0][0]).toMatchObject({ method: 'POST', url: '/api/pit-scouting' });
  });

  it('marks a queued room entry so the page can say "will sync", not "failed"', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const api = await import('./api');
    const saving = api.saveScoutingRoomEntry('room-ab12cd', { entry: {}, scout_profile: 'ann' });
    await expect(saving).rejects.toBeInstanceOf(api.QueuedForSyncError);
  });

  it('never queues a write that would duplicate, like creating a workspace', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const api = await import('./api');
    const creating = api.createWorkspace({ name: 'Team', display_name: 'Ann' });
    await expect(creating).rejects.toThrow();
    await expect(creating).rejects.not.toBeInstanceOf(api.QueuedForSyncError);
    expect(enqueue).not.toHaveBeenCalled();
  });
  it('does not claim a write is queued if durable storage fails', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    enqueue.mockRejectedValue(new Error('This change could not be saved on this phone.'));
    const api = await import('./api');
    const saving = api.upsertPitEntry({ event_key: '2026arc', team_key: 'frc254', payload: {} });
    await expect(saving).rejects.toThrow(/could not be saved/);
    await expect(saving).rejects.not.toBeInstanceOf(api.QueuedForSyncError);
  });

  it('never queues workspace creation even when the browser reports offline', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const api = await import('./api');
    const creating = api.createWorkspace({ name: 'Team', display_name: 'Ann' });
    await expect(creating).rejects.not.toBeInstanceOf(api.QueuedForSyncError);
    expect(enqueue).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

});
