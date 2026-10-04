import { IDBFactory, IDBObjectStore } from 'fake-indexeddb';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal('indexedDB', new IDBFactory());
  window.localStorage.clear();
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const change = { method: 'POST', url: '/api/pit-scouting', body: '{"notes":"match notes"}', headers: {}, label: 'Pit notes' };

describe('durable scouting queue', () => {
  it('recovers committed writes after reopening the application', async () => {
    const queue = await import('./offlineQueue');
    await queue.enqueue(change);
    vi.resetModules();
    const reopened = await import('./offlineQueue');
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    expect(await reopened.flush()).toBe(1);
    expect(fetch).toHaveBeenCalledWith(change.url, expect.objectContaining({ body: change.body }));
    expect(reopened.size()).toBe(0);
  });

  it('rejects a save when the transaction aborts after its put request succeeds', async () => {
    const queue = await import('./offlineQueue');
    await queue.enqueue(change);
    const put = IDBObjectStore.prototype.put;
    vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (this: IDBObjectStore, ...args) {
      const request = put.apply(this, args);
      request.addEventListener('success', () => this.transaction.abort());
      return request;
    });
    await expect(queue.enqueue({ ...change, body: '{"notes":"new"}' })).rejects.toThrow(/could not be saved/);
    vi.restoreAllMocks();
    vi.resetModules();
    const reopened = await import('./offlineQueue');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}')));
    expect(await reopened.flush()).toBe(1);
  });

  it('retains rejected payloads in IndexedDB after a restart', async () => {
    const queue = await import('./offlineQueue');
    await queue.enqueue(change);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 403 })));
    expect(await queue.flush()).toBe(0);
    vi.resetModules();
    const reopened = await import('./offlineQueue');
    expect(await reopened.listFailedMutations()).toEqual([expect.objectContaining({ body: change.body, failure: { reason: 'server-rejected', status: 403 } })]);
  });
});
