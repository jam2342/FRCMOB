// Regression from the 2026-10-04 QA pass.
import { afterEach, describe, expect, it, vi } from 'vitest';

describe('QA: pit edit ordering after reconnect', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
    window.localStorage.clear();
  });

  it('does not let a transiently failed older pit edit overwrite a newer edit', async () => {
    vi.stubGlobal('indexedDB', undefined);
    const queue = await import('./offlineQueue');
    const write = (notes: string) => ({
      method: 'POST', url: '/api/pit-scouting', headers: {},
      body: JSON.stringify({ event_key: '2026arc', team_key: 'frc254', payload: { notes } }),
    });
    await queue.enqueue(write('old notes'));
    await new Promise(resolve => setTimeout(resolve, 2));
    await queue.enqueue(write('corrected notes'));
    let stored = '';
    let first = true;
    vi.stubGlobal('fetch', vi.fn(async (_url, init: RequestInit) => {
      if (first) { first = false; return new Response('{}', { status: 503 }); }
      stored = JSON.parse(String(init.body)).payload.notes;
      return new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json' } });
    }));
    await queue.flush();
    await queue.flush();
    expect(queue.size()).toBe(0);
    expect(stored).toBe('corrected notes');
  });
});
