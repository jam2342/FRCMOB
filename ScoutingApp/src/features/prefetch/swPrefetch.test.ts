import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

// public/sw.js fetches Match Center's first requests when a match alert arrives.
// They only help if they are the exact URLs the page then requests.
describe('service worker alert prefetch', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('fetches exactly what Match Center asks for first', async () => {
    const sw = fs.readFileSync(path.resolve(__dirname, '../../../public/sw.js'), 'utf8');
    const block = /const ALERT_PREFETCH_PATHS = \[([\s\S]*?)\];/.exec(sw)?.[1] || '';
    const swUrls = [...block.matchAll(/"([^"]+)"/g)].map((m) => m[1].replace('{event}', '2026arc')).sort();
    expect(swUrls).toHaveLength(2);

    vi.stubEnv('VITE_API_URL', '/api');
    const seen: string[] = [];
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      seen.push(String(input));
      return Promise.resolve(new Response(JSON.stringify({ ok: true, matches: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }) as unknown as typeof fetch);
    const { loadMatchCenterSynergy } = await import('./pageRequests');
    const { getEventSchedule } = await import('../../api');
    const { SAVED_COPY_MAX_AGE_MS } = await import('../../config/savedCopy');
    await loadMatchCenterSynergy('2026arc');
    await getEventSchedule('2026arc', false, undefined, { showSavedMaxAgeMs: SAVED_COPY_MAX_AGE_MS.schedule });
    expect([...seen].sort()).toEqual(swUrls);
  });
});
