import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  native: false,
  impact: vi.fn(async () => undefined),
  notification: vi.fn(async () => undefined),
}));
vi.mock('../platform/runtime', () => ({ isNativeApp: () => mocks.native }));
vi.mock('@capacitor/haptics', () => ({
  Haptics: { impact: mocks.impact, notification: mocks.notification },
  ImpactStyle: { Light: 'LIGHT', Medium: 'MEDIUM' },
  NotificationType: { Success: 'SUCCESS', Warning: 'WARNING' },
}));

import { hapticPhase, hapticSuccess, hapticTap, hapticUndo } from './haptics';

const vibrate = vi.fn();

beforeEach(() => {
  Object.defineProperty(navigator, 'vibrate', { value: vibrate, configurable: true });
});

afterEach(() => {
  vi.clearAllMocks();
  mocks.native = false;
});

describe('haptics in a browser', () => {
  it('uses the Vibration API with a distinct pattern per kind', () => {
    hapticTap();
    hapticUndo();
    hapticSuccess();
    hapticPhase();
    expect(vibrate.mock.calls).toEqual([[8], [[12, 30, 12]], [[10, 40, 20]], [[60, 80, 60, 80, 60]]]);
    expect(mocks.impact).not.toHaveBeenCalled();
  });

  it('is silent where vibration throws', () => {
    vibrate.mockImplementationOnce(() => { throw new Error('blocked'); });
    expect(() => hapticTap()).not.toThrow();
  });
});

describe('haptics in the native app', () => {
  it('drives the Taptic Engine instead of the Vibration API, so iPhones buzz', async () => {
    mocks.native = true;
    hapticTap();
    hapticUndo();
    hapticSuccess();
    hapticPhase();
    await vi.waitFor(() => expect(mocks.notification).toHaveBeenCalledTimes(2));
    expect(mocks.impact.mock.calls).toEqual([[{ style: 'LIGHT' }], [{ style: 'MEDIUM' }]]);
    expect(mocks.notification.mock.calls).toEqual([[{ type: 'SUCCESS' }], [{ type: 'WARNING' }]]);
    expect(vibrate).not.toHaveBeenCalled();
  });

  it('swallows a plugin failure', async () => {
    mocks.native = true;
    mocks.impact.mockRejectedValueOnce(new Error('unimplemented'));
    hapticTap();
    await vi.waitFor(() => expect(mocks.impact).toHaveBeenCalled());
  });
});
