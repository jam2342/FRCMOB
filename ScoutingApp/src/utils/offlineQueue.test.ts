import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DroppedMutationInfo } from './offlineQueue';
import { lastConfirmedSync, lastSyncIssue } from '../features/offline/syncReceipt';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

async function flushWith(response: Response) {
  const queue = await import('./offlineQueue');
  await queue.enqueue({ method: 'PUT', url: '/api/picklists/7', body: '{"version":3}', headers: {}, label: 'Picklist edit' });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
  const dropped: DroppedMutationInfo[] = [];
  const onDropped = (e: Event) => dropped.push((e as CustomEvent<DroppedMutationInfo>).detail);
  window.addEventListener('offlinequeue:dropped', onDropped);
  const delivered = await queue.flush();
  window.removeEventListener('offlinequeue:dropped', onDropped);
  return { delivered, dropped, remaining: queue.size() };
}

describe('replaying queued writes', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.resetModules();
    window.localStorage.clear();
  });

  it('reports a write the server refused because someone else changed it first', async () => {
    const { delivered, dropped, remaining } = await flushWith(
      jsonResponse({ ok: false, conflict: true, picklist: { version: 4 } }),
    );
    expect(delivered).toBe(0);
    expect(remaining).toBe(0);
    expect(dropped).toEqual([{ reason: 'conflict', count: 1, labels: ['Picklist edit'] }]);
    expect(lastSyncIssue()).toMatchObject({ reason: 'conflict', count: 1, labels: ['Picklist edit'] });
  });

  it('still counts an ordinary success as delivered, with no warning', async () => {
    const { delivered, dropped } = await flushWith(jsonResponse({ ok: true, picklist: { version: 4 } }));
    expect(delivered).toBe(1);
    expect(dropped).toEqual([]);
    expect(lastConfirmedSync()).not.toBeNull();
  });
  it('retains a 409 conflict across a restart and excludes credentials from recovery', async () => {
    const queue = await import('./offlineQueue');
    await queue.enqueue({ method: 'POST', url: '/api/scouting/rooms/test/entries', body: '{"fuel":42}', headers: { 'X-Workspace-Access': 'fake-private-token' }, label: 'Match entry' });
    const fetch = vi.fn().mockResolvedValue(jsonResponse({ detail: 'Profile already claimed' }, 409));
    vi.stubGlobal('fetch', fetch);
    expect(await queue.flush()).toBe(0);
    expect(lastConfirmedSync()).toBeNull();
    vi.resetModules();
    const reopened = await import('./offlineQueue');
    expect(await reopened.listFailedMutations()).toHaveLength(1);
    const recovery = JSON.stringify(await reopened.recoveryChanges());
    expect(recovery).toContain('fuel');
    expect(recovery).not.toContain('fake-private-token');
    expect(await reopened.flush()).toBe(0);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('keeps a new save made while an older write is syncing', async () => {
    const queue = await import('./offlineQueue');
    await queue.enqueue({ method: 'PUT', url: '/api/picklists/1', body: '{}', headers: {} });
    let resolve!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
    const syncing = queue.flush();
    await vi.waitFor(() => expect(resolve).toBeDefined());
    await queue.enqueue({ method: 'POST', url: '/api/pit-scouting', body: '{"notes":"new"}', headers: {} });
    resolve(jsonResponse({ ok: true }));
    expect(await syncing).toBe(1);
    expect(queue.size()).toBe(1);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ok: true })));
    expect(await queue.flush()).toBe(1);
    expect(queue.size()).toBe(0);
  });

  it('does not discard older work or report queued when storage is full', async () => {
    vi.stubGlobal('indexedDB', undefined);
    const queue = await import('./offlineQueue');
    await queue.enqueue({ method: 'PUT', url: '/api/picklists/1', body: '{}', headers: {} });
    const before = window.localStorage.getItem('frcmob_offline_queue_v1');
    const storage = window.localStorage;
    const descriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')!;
    // jsdom's native Storage intercepts instance property assignments, so spying
    // on setItem works only with the plain-object storage fallback used by Node 26.
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: storage.getItem.bind(storage),
        setItem: () => { throw new DOMException('Full', 'QuotaExceededError'); },
      },
    });
    try {
      await expect(queue.enqueue({ method: 'POST', url: '/api/pit-scouting', body: '{}', headers: {} })).rejects.toThrow(/could not be saved/);
    } finally {
      Object.defineProperty(window, 'localStorage', descriptor);
    }
    expect(window.localStorage.getItem('frcmob_offline_queue_v1')).toBe(before);
    expect(queue.size()).toBe(1);
  });

  it('retries rate limits and server errors without losing the edit', async () => {
    const { remaining, delivered } = await flushWith(jsonResponse({}, 429));
    expect(delivered).toBe(0);
    expect(remaining).toBe(1);
    const queue = await import('./offlineQueue');
    expect(await queue.listFailedMutations()).toHaveLength(0);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({}, 503)));
    expect(await queue.flush()).toBe(0);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ok: true })));
    expect(await queue.flush()).toBe(1);
  });

  it('ends a hung replay without losing the edit and allows a later retry', async () => {
    vi.useFakeTimers();
    const queue = await import('./offlineQueue');
    await queue.enqueue({ method: 'POST', url: '/api/pit-scouting', body: '{"notes":"keep"}', headers: {} });
    const fetch = vi.fn((_url, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Timed out', 'AbortError')));
    }));
    vi.stubGlobal('fetch', fetch);
    const syncing = queue.flush();
    await vi.advanceTimersByTimeAsync(12_000);
    expect(await syncing).toBe(0);
    expect(queue.size()).toBe(1);
    expect(await queue.listFailedMutations()).toHaveLength(0);
    fetch.mockResolvedValue(new Response('{}'));
    expect(await queue.flush()).toBe(1);
  });

  it('keeps the edit when headers arrive but the acknowledgement body stalls', async () => {
    vi.useFakeTimers();
    const queue = await import('./offlineQueue');
    await queue.enqueue({ method: 'PUT', url: '/api/picklists/1', body: '{}', headers: {} });
    vi.stubGlobal('fetch', vi.fn(async (_url, init: RequestInit) => new Response(new ReadableStream({
      start(controller) {
        init.signal?.addEventListener('abort', () => controller.error(new DOMException('Timed out', 'AbortError')));
      },
    }), { headers: { 'Content-Type': 'application/json' } })));
    const syncing = queue.flush();
    await vi.advanceTimersByTimeAsync(12_000);
    expect(await syncing).toBe(0);
    expect(queue.size()).toBe(1);
    expect(lastConfirmedSync()).toBeNull();
  });

});
