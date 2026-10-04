import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// jsdom has no Cache Storage; a Map-backed stand-in with the calls the store uses.
function fakeCaches() {
  const stores = new Map<string, Map<string, Response>>();
  const cacheFor = (name: string) => {
    if (!stores.has(name)) stores.set(name, new Map());
    const m = stores.get(name)!;
    return {
      put: async (req: Request, res: Response) => void m.set(req.url, res.clone()),
      match: async (req: Request) => m.get(req.url)?.clone(),
      delete: async (req: Request) => m.delete(req.url),
      keys: async () => [...m.keys()].map((u) => new Request(u)),
    };
  };
  return { open: async (name: string) => cacheFor(name), stores };
}

const bigBody = JSON.stringify({ ratings: 'x'.repeat(600_000) }); // over the 450 KB localStorage cap

describe('saved API responses (offline at events)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal('caches', fakeCaches());
    vi.stubEnv('VITE_API_URL', '/api');
    window.localStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  });

  it('does not count unrelated background reads in an event preparation audit', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(new Response('{}', {status:200,headers:{'Content-Type':'application/json'}}))));
    const api = await import('../api');
    const audit = api.beginOfflineCacheAudit(url => url.includes('/2026arc/'));
    try { await Promise.all([api.getEventRatings('2026arc'), api.getEventRatings('2026other')]); }
    finally { audit.stop(); }
    expect(audit.calls).toHaveLength(1);expect(audit.calls[0].url).toContain('/2026arc/');expect(audit.calls[0].saved).toBe(true);
  });

  it('keeps and expires entries by their retain time', async () => {
    const store = await import('./apiResponseStore');
    await store.putStoredResponse('GET:w0:/api/x', '{"a":1}', {
      status: 200, statusText: 'OK', headers: new Headers({ 'Content-Type': 'application/json' }), retainUntilMs: Date.now() + 60_000,
    });
    const hit = await store.getStoredResponse('GET:w0:/api/x');
    expect(await hit?.response.json()).toEqual({ a: 1 });
    await store.putStoredResponse('GET:w0:/api/old', '{}', {
      status: 200, statusText: 'OK', headers: new Headers(), retainUntilMs: Date.now() - 1,
    });
    expect(await store.getStoredResponse('GET:w0:/api/old')).toBeNull();
  });

  it('serves a response too big for localStorage when the event has no signal', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(bigBody, { status: 200, headers: { 'Content-Type': 'application/json' } })));
    const api = await import('../api');
    await api.getEventRatings('2026arc');
    await new Promise((r) => setTimeout(r, 0)); // the write is fire-and-forget

    vi.resetModules(); // a fresh page load: nothing in memory
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    const reloaded = await import('../api');
    const payload = (await reloaded.getEventRatings('2026arc')) as unknown as { ratings: string };
    expect(payload.ratings.length).toBe(600_000);
  });

  it('shows the saved copy when a weak signal leaves the request hanging', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(bigBody, { status: 200, headers: { 'Content-Type': 'application/json' } })));
    const api = await import('../api');
    await api.getEventRatings('2026arc');
    await new Promise((r) => setTimeout(r, 0));

    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {}))); // connected, never answers
    const reloaded = await import('../api');
    const started = Date.now();
    const payload = (await reloaded.getEventRatings('2026arc')) as unknown as { ratings: string };
    expect(payload.ratings.length).toBe(600_000);
    expect(Date.now() - started).toBeLessThan(8000); // not the 12 s request timeout
  }, 15_000);
});
