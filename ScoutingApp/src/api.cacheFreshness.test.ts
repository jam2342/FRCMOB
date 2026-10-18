import { afterEach, describe, expect, it, vi } from 'vitest';

// The device cache used to store every GET as fresh for six hours, so a reload
// kept showing an old live schedule. Fresh now means the endpoint's own TTL; the
// long retention only serves when there is no network.
function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

describe('device cache freshness', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.useRealTimers();
    vi.resetModules();
    window.localStorage.clear();
  });

  it('refetches after the endpoint TTL even when a saved copy exists, and uses it offline', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-04-30T15:00:00Z'));
    let version = 1;
    const fetchMock = vi.fn().mockImplementation(async () => json({ ok: true, version: version++ }));
    vi.stubGlobal('fetch', fetchMock);

    const first = await import('./api');
    await first.getEventSchedule('2026arc');
    await new Promise((resolve) => setTimeout(resolve, 0)); // let the persistent write finish
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Ten minutes later the app is reopened: memory cache gone, device copy still there.
    vi.setSystemTime(new Date('2026-04-30T15:10:00Z'));
    vi.resetModules();
    const reopened = await import('./api');
    const fresh = (await reopened.getEventSchedule('2026arc')) as unknown as { version: number };
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fresh.version).toBe(2);

    // No network at all: the saved copy is still better than nothing.
    vi.setSystemTime(new Date('2026-04-30T16:00:00Z'));
    vi.resetModules();
    const offline = await import('./api');
    vi.stubGlobal('navigator', { ...navigator, onLine: false });
    fetchMock.mockImplementation(async () => {
      throw new TypeError('Failed to fetch');
    });
    const fallback = (await offline.getEventSchedule('2026arc')) as unknown as { version: number };
    expect(fallback.version).toBe(2);
  });

  it('never goes back to an older copy after a live read', async () => {
    // Match Center polls live matches past the cache; when the match ended it went back
    // to cached reads and the pre-match copy in memory hid the final score for a poll.
    vi.stubEnv('VITE_API_URL', '/api');
    let version = 1;
    const fetchMock = vi.fn().mockImplementation(async () => json({ ok: true, version: version++ }));
    vi.stubGlobal('fetch', fetchMock);
    const api = await import('./api');
    const before = (await api.getEventSchedule('2026arc')) as unknown as { version: number };
    expect(before.version).toBe(1);
    const live = (await api.getEventSchedule('2026arc', false, undefined, { bypassCache: true, cacheTtlMs: 0, staleWhileRevalidateMs: 0 })) as unknown as { version: number };
    expect(live.version).toBe(2);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const after = (await api.getEventSchedule('2026arc')) as unknown as { version: number };
    expect(after.version).toBeGreaterThanOrEqual(2);
  });
});
