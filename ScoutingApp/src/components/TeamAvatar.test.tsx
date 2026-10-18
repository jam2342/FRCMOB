import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const getTeamLogo = vi.hoisted(() => vi.fn(async () => ({ available: true, image_url: '/logo.png' })));
vi.mock('../api', () => ({ getTeamLogo }));

import { TeamAvatar } from './TeamAvatar';

type Callback = (entries: Array<{ isIntersecting: boolean }>) => void;
const observers: Callback[] = [];

afterEach(() => {
  vi.unstubAllGlobals();
  observers.length = 0;
});

it('asks for a logo only once the avatar comes near the screen', async () => {
  vi.stubGlobal('IntersectionObserver', class {
    constructor(cb: Callback) { observers.push(cb); }
    observe() {}
    disconnect() {}
  });
  render(<TeamAvatar teamKey="frc9999" teamNumber={9999} />);
  expect(getTeamLogo).not.toHaveBeenCalled();
  await act(async () => {
    observers[0]([{ isIntersecting: true }]);
    await Promise.resolve();
  });
  expect(getTeamLogo).toHaveBeenCalledWith('frc9999', undefined);
});
