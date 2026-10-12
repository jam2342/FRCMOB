import { afterEach, describe, expect, it, vi } from 'vitest';
import { routePattern, startRum, trackingAllowed } from './rum';
import { markData, markRoute, onFirstData } from './routeTiming';

describe('routePattern', () => {
  it('keeps the route and drops anything that could name a team, event or match', () => {
    expect(routePattern('#/team-center?team=frc254&event=2026arc')).toBe('/team-center');
    expect(routePattern('#/match-center/predictions')).toBe('/match-center/predictions');
    expect(routePattern('#/events/2026arc/frc254/qm1/extra')).toBe('/events/id/id');
    expect(routePattern('')).toBe('/');
    expect(routePattern('#/Scouting/Record')).toBe('/scouting/record');
  });
});

describe('trackingAllowed', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('respects Do Not Track and Global Privacy Control', () => {
    vi.stubGlobal('navigator', { ...navigator, doNotTrack: '1' });
    expect(trackingAllowed()).toBe(false);
    vi.stubGlobal('navigator', { ...navigator, doNotTrack: '0', globalPrivacyControl: true });
    expect(trackingAllowed()).toBe(false);
    vi.stubGlobal('navigator', { ...navigator, doNotTrack: '0', globalPrivacyControl: false });
    expect(trackingAllowed()).toBe(true);
  });
});

describe('routeTiming', () => {
  afterEach(() => onFirstData(null));

  it('reports only the first data after a route opens', () => {
    const seen: Array<[string, string]> = [];
    onFirstData((route, _ms, source) => seen.push([route, source]));
    markRoute('/team-center', performance.now());
    markData('saved');
    markData('network');
    markRoute('/events', performance.now());
    markData('network');
    expect(seen).toEqual([['/team-center', 'saved'], ['/events', 'network']]);
  });
});

describe('startRum', () => {
  it('does nothing under test, so tests never send beacons', () => {
    const beacon = vi.fn();
    vi.stubGlobal('navigator', { ...navigator, sendBeacon: beacon });
    const listen = vi.spyOn(document, 'addEventListener');
    startRum('/api');
    expect(listen).not.toHaveBeenCalledWith('visibilitychange', expect.anything());
    listen.mockRestore();
    vi.unstubAllGlobals();
  });
});
