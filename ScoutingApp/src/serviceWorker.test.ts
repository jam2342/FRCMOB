// @vitest-environment node
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8');

function worker() {
  const handlers = new Map<string, (event: unknown) => void>();
  const store = new Map<string, Map<string, Response>>();
  const key = (request: string | Request) => new URL(typeof request === 'string' ? request : request.url, 'https://app.test').href;
  const caches = {
    open: vi.fn(async (name: string) => {
      if (!store.has(name)) store.set(name, new Map());
      const rows = store.get(name)!;
      return { match: async (request: string | Request) => rows.get(key(request))?.clone(), put: async (request: string | Request, response: Response) => { rows.set(key(request), response); } };
    }),
    match: vi.fn(async (request: string | Request) => {
      for (const rows of store.values()) if (rows.has(key(request))) return rows.get(key(request))?.clone();
    }),
    keys: async () => [...store.keys()],
    delete: vi.fn(async (name: string) => store.delete(name)),
  };
  const fetch = vi.fn().mockRejectedValue(new TypeError('Offline'));
  const self = { location: { origin: 'https://app.test' }, addEventListener: (name: string, fn: (event: unknown) => void) => handlers.set(name, fn), skipWaiting: vi.fn(), clients: { claim: vi.fn() } };
  runInNewContext(source, { self, caches, fetch, URL, Set, Promise, Error });
  return {
    caches, fetch, self,
    async navigate(path: string) {
      let result!: Promise<Response>;
      const writes: Promise<unknown>[] = [];
      handlers.get('fetch')!({ request: { url: `https://app.test${path}`, method: 'GET', mode: 'navigate' }, respondWith: (value: Promise<Response>) => { result = value; }, waitUntil: (value: Promise<unknown>) => writes.push(value) });
      const response = await result;
      await Promise.all(writes);
      return response;
    },
    async lifecycle(name: string) {
      let result!: Promise<void>;
      handlers.get(name)!({ waitUntil: (value: Promise<void>) => { result = value; } });
      await result;
    },
  };
}

describe('offline service worker', () => {
  it('reopens the recorder with isolation headers for an unseen query string', async () => {
    const sw = worker();
    const cache = await sw.caches.open('frcmob-v10');
    await cache.put('/', new Response('main'));
    await cache.put('/record.html', new Response('recorder', { headers: { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' } }));
    const result = await sw.navigate('/record.html?event=2026arc');
    expect(await result.text()).toBe('recorder');
    expect(result.headers.get('Cross-Origin-Embedder-Policy')).toBe('require-corp');
  });

  it('uses the saved shell when the server is unavailable without replacing it with an error', async () => {
    const sw = worker();
    const cache = await sw.caches.open('frcmob-v10');
    await cache.put('/', new Response('saved app'));
    sw.fetch.mockResolvedValue(new Response('Unavailable', { status: 503 }));
    expect(await (await sw.navigate('/')).text()).toBe('saved app');
    expect(await (await cache.match('/'))!.text()).toBe('saved app');
  });

  it('keeps the working worker when replacement shell downloads fail', async () => {
    const sw = worker();
    await expect(sw.lifecycle('install')).rejects.toThrow('Offline');
    expect(sw.self.skipWaiting).not.toHaveBeenCalled();
  });

  it('retains the preceding shell and all saved scouting stores on activation', async () => {
    const sw = worker();
    for (const name of ['frcmob-v8', 'frcmob-v9', 'frcmob-v10', 'frcmob-api-v1', 'frcmob-offline-models-v1', 'frcmob-offline-shell-v1']) await sw.caches.open(name);
    await sw.lifecycle('activate');
    expect(await sw.caches.keys()).toEqual(['frcmob-v9', 'frcmob-v10', 'frcmob-api-v1', 'frcmob-offline-models-v1', 'frcmob-offline-shell-v1']);
  });
});
