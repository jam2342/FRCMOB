import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isEventPackReady, prepareEventOffline, readSavedEventPack, verifySavedEventPack, type SavedEventPack } from './offlineReady';

const key = 'GET:w0:/api/events/2026arc/schedule';
const pack: SavedEventPack = {
  eventKey: '2026arc', workspaceId: 0, checkedAt: Date.now(), expectedSteps: 1,
  steps: [{ label: 'schedule', required: true, saved: 1, total: 1, savedAt: Date.now(), keys: [key] }],
};

describe('offline pack readiness', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it('loses ready status when a saved response has been evicted', async () => {
    window.localStorage.setItem('frcmob_offline_pack_v2:2026arc', JSON.stringify(pack));
    window.localStorage.setItem(`scouting_api_cache_v1:${key}`, JSON.stringify({ retainUntilMs: Date.now() + 60_000 }));
    expect(isEventPackReady(await verifySavedEventPack(readSavedEventPack('2026arc')))).toBe(true);
    window.localStorage.removeItem(`scouting_api_cache_v1:${key}`);
    const verified = await verifySavedEventPack(readSavedEventPack('2026arc'));
    expect(isEventPackReady(verified)).toBe(false);
    expect(verified?.steps[0].saved).toBe(0);
  });

  it('does not mark an interrupted checklist ready after reopening', async () => {
    window.localStorage.setItem('frcmob_offline_pack_v2:2026arc', JSON.stringify({ ...pack, expectedSteps: 20 }));
    window.localStorage.setItem(`scouting_api_cache_v1:${key}`, JSON.stringify({ retainUntilMs: Date.now() + 60_000 }));
    expect(isEventPackReady(await verifySavedEventPack(readSavedEventPack('2026arc')))).toBe(false);
  });

  it('requires older packs to be prepared again before claiming readiness', () => {
    const legacy = JSON.parse(JSON.stringify(pack));
    delete legacy.expectedSteps;
    expect(isEventPackReady(legacy)).toBe(false);
  });

  it('does not reuse another workspace\'s pack', () => {
    window.localStorage.setItem('frcmob_offline_pack_v2:2026arc', JSON.stringify({ ...pack, workspaceId: 42 }));
    expect(readSavedEventPack('2026arc')).toBeNull();
  });
  it('does not verify team pages when the event returned no teams', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => new Response(JSON.stringify({ teams: [] }), { headers: { 'Content-Type': 'application/json' } })));
    await prepareEventOffline('2026empty', () => {});
    const saved = readSavedEventPack('2026empty');
    const pages = saved?.steps.find((step) => step.label === 'team pages');
    expect(pages?.saved).toBeGreaterThan(0);
    expect(pages?.savedAt).toBeNull();
    expect(isEventPackReady(await verifySavedEventPack(saved))).toBe(false);
  });

});
