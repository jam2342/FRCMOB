import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// A page that polls (useSingleFlightPolling) may be answered with a saved copy at
// once and re-render when the refresh brings something new.
describe('saved copy first', () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    vi.stubEnv('VITE_API_URL', '/api');
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  const body = (version: string) =>
    new Response(JSON.stringify({ ok: true, event_key: '2026arc', rankings: [], version }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });

  async function primeThenAge(ageMs: number) {
    const fetchMock = vi.fn(() => Promise.resolve(body('old')));
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');
    await api.getEventRankings('2026arc');
    await new Promise((resolve) => setTimeout(resolve, 0)); // saved copy written
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(Date.now() + ageMs);
    return { api, fetchMock };
  }

  it('answers with the saved copy at once, refreshes, and announces the change', async () => {
    const { api, fetchMock } = await primeThenAge(30 * 60_000);
    let release: (r: Response) => void = () => undefined;
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => { release = resolve; }));
    const updated = vi.fn();
    window.addEventListener('api:updated', updated);

    const first = (await api.getEventRankings('2026arc', { showSavedMaxAgeMs: 60 * 60_000 })) as unknown as { version: string };
    expect(first.version).toBe('old');
    expect(fetchMock).toHaveBeenCalledTimes(2);

    release(body('new'));
    await vi.waitFor(() => expect(updated).toHaveBeenCalledTimes(1));
    const again = (await api.getEventRankings('2026arc')) as unknown as { version: string };
    expect(again.version).toBe('new');
    window.removeEventListener('api:updated', updated);
  });

  it('stays quiet when the refresh brings the same data', async () => {
    const { api, fetchMock } = await primeThenAge(30 * 60_000);
    fetchMock.mockImplementation(() => Promise.resolve(body('old')));
    const updated = vi.fn();
    window.addEventListener('api:updated', updated);
    await api.getEventRankings('2026arc', { showSavedMaxAgeMs: 60 * 60_000 });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(updated).not.toHaveBeenCalled();
    window.removeEventListener('api:updated', updated);
  });

  it('waits for the network without the option, or when the copy is too old', async () => {
    const { api, fetchMock } = await primeThenAge(30 * 60_000);
    fetchMock.mockImplementation(() => Promise.resolve(body('new')));
    const plain = (await api.getEventRankings('2026arc')) as unknown as { version: string };
    expect(plain.version).toBe('new');

    localStorage.clear();
    vi.resetModules();
    const aged = await primeThenAge(2 * 60 * 60_000);
    aged.fetchMock.mockImplementation(() => Promise.resolve(body('new')));
    const tooOld = (await aged.api.getEventRankings('2026arc', { showSavedMaxAgeMs: 60 * 60_000 })) as unknown as { version: string };
    expect(tooOld.version).toBe('new');
  });
});
