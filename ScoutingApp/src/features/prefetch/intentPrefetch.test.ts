import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./pageRequests', () => ({
  loadTeamCenterIntel: vi.fn(() => Promise.resolve({})),
  loadTeamCenterLogo: vi.fn(() => Promise.resolve({})),
  loadMatchCenterPredictions: vi.fn(() => Promise.resolve({})),
  loadEventsTeamsHistory: vi.fn(() => Promise.resolve({})),
  loadEventsRankings: vi.fn(() => Promise.resolve({})),
}));
vi.mock('../../pages/TeamCenterPage', () => ({ default: () => null }));
vi.mock('../../pages/MatchCenterPage', () => ({ default: () => null }));
vi.mock('../../pages/EventsPage', () => ({ default: () => null }));

import * as requests from './pageRequests';
import { prefetchForHref } from './intentPrefetch';

const BASE = 'https://frcmob.test/#/home';

describe('prefetchForHref', () => {
  beforeEach(() => vi.clearAllMocks());

  it('starts Team Center intel and logo for a team link, with its event', async () => {
    const prefetch = prefetchForHref('#/team-center?team=FRC254&event=2026ARC&tab=overview', BASE);
    expect(prefetch?.key).toBe('team|frc254|2026arc');
    await Promise.all(prefetch!.run());
    expect(requests.loadTeamCenterIntel).toHaveBeenCalledWith('frc254', '2026arc');
    expect(requests.loadTeamCenterLogo).toHaveBeenCalledWith('frc254', '2026arc');
  });

  it('passes no event when the link has none, like the page', async () => {
    await Promise.all(prefetchForHref('#/team-center?team=frc1678', BASE)!.run());
    expect(requests.loadTeamCenterIntel).toHaveBeenCalledWith('frc1678', undefined);
  });

  it('covers Match Center and Events links that name an event', async () => {
    await Promise.all(prefetchForHref('#/match-center?event=2026arc&match=2026arc_qm1', BASE)!.run());
    expect(requests.loadMatchCenterPredictions).toHaveBeenCalledWith('2026arc');
    await Promise.all(prefetchForHref('/#/events?event=2026casac', BASE)!.run());
    expect(requests.loadEventsTeamsHistory).toHaveBeenCalledWith('2026casac');
    expect(requests.loadEventsRankings).toHaveBeenCalledWith('2026casac');
  });

  it('ignores everything else', () => {
    for (const href of ['#/settings', '#/team-center', '#/team-center?team=robots', '#/events', 'https://other.test/#/events?event=2026arc', '#/match-center/predictions?event=2026arc', 'mailto:x@y.z']) {
      expect(prefetchForHref(href, BASE), href).toBeNull();
    }
  });
});
