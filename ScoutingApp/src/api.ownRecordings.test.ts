import { afterEach, describe, expect, it, vi } from 'vitest';

// Team Center asks for the team's own unreviewed recordings with include_workspace;
// without it the request must stay the public, edge-cacheable URL.
describe('own recordings in track requests', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('adds include_workspace only when asked', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');

    await api.getTeamHeatmap('frc254', '2026arc', { include_workspace: true });
    await api.getTeamShiftPlay('frc254', '2026arc', { include_workspace: true });
    await api.getTeamShiftPlay('frc254', '2026cur');

    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls[0]).toContain('/api/tracks/heatmap/frc254?');
    expect(urls[0]).toContain('include_workspace=true');
    expect(urls[1]).toContain('/api/tracks/shift-play/frc254?');
    expect(urls[1]).toContain('include_workspace=true');
    expect(urls[2]).not.toContain('include_workspace');
  });
});
