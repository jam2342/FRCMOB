import { afterEach, expect, it, vi } from 'vitest';
const enqueue = vi.fn(async (...args: unknown[]) => { void args; });
vi.mock('./utils/offlineQueue', () => ({ enqueue: (...args: unknown[]) => enqueue(...args) }));
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.resetModules(); enqueue.mockClear(); });
it('labels queued pit notes with the team instead of an HTTP path', async () => {
  vi.stubEnv('VITE_API_URL', '/api');
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
  const api = await import('./api');
  await expect(api.upsertPitEntry({ event_key: '2026arc', team_key: 'frc254', payload: {} })).rejects.toBeInstanceOf(api.QueuedForSyncError);
  expect(enqueue.mock.calls[0][0]).toMatchObject({ label: 'Pit notes for Team 254' });
});

it('labels queued picklist edits without exposing a request path', async () => {
  vi.stubEnv('VITE_API_URL', '/api');
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
  const api = await import('./api');
  await expect(api.updatePicklist(42, {version:1,slots:[]})).rejects.toBeInstanceOf(api.QueuedForSyncError);
  expect(enqueue.mock.calls[0][0]).toMatchObject({label:'Picklist changes'});
});
