// Regression from the 2026-10-04 QA pass.
import { afterEach, describe, expect, it, vi } from 'vitest';

describe('QA: prepared event packs serve the actual page requests', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
    window.localStorage.clear();
  });

  it.each([
    ['pit scouting', false],
    ['picklist team pool', true],
    ['scouting room team pool', true],
  ])('opens the %s team list offline after a verified download', async (_page, fallback) => {
    vi.stubEnv('VITE_API_URL', '/api');
    vi.stubGlobal('navigator', { onLine: true });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      ok: true, teams: [{ team_key: 'frc254', team_number: 254 }], entries: [], picklists: [],
    }), { headers: { 'Content-Type': 'application/json' } })));
    const { prepareEventOffline, readSavedEventPack, verifySavedEventPack, isEventPackReady } = await import('./offlineReady');
    await prepareEventOffline('2026arc', () => {});
    expect(isEventPackReady(await verifySavedEventPack(readSavedEventPack('2026arc')))).toBe(true);

    vi.stubGlobal('navigator', { onLine: false });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const { getEventTeamsIntel } = await import('../../api');
    // These are the exact options PitScoutingPage, PicklistPage and ScoutingPage use.
    const teams = await getEventTeamsIntel('2026arc', {
      include_tba: true, include_statbotics: false, include_season_fallback: fallback,
      include_rating_details: false, include_rating_signals: false,
    });
    expect(teams.teams).toHaveLength(1);
  });
});
