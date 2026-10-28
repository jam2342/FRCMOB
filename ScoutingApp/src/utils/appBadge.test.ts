import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { clearAppBadge, resetAppBadgeForTests, setAppBadgeCount } from './appBadge';
import { useAssignmentBadge } from '../hooks/useAssignmentBadge';
import type { MyAssignment } from '../features/workspace/teamAssignments';

const setAppBadge = vi.fn(async () => undefined);
const clear = vi.fn(async () => undefined);

beforeEach(() => {
  Object.defineProperty(navigator, 'setAppBadge', { value: setAppBadge, configurable: true });
  Object.defineProperty(navigator, 'clearAppBadge', { value: clear, configurable: true });
});
afterEach(() => {
  vi.clearAllMocks();
  resetAppBadgeForTests();
  delete (navigator as { setAppBadge?: unknown }).setAppBadge;
  delete (navigator as { clearAppBadge?: unknown }).clearAppBadge;
});

const item = (done: boolean): MyAssignment => ({ match_key: 'm', team_key: 't', match_label: 'QM 1', team_number: '1', alliance: 'red', start_time: null, done });

describe('app badge', () => {
  it('shows a count, clears at zero, and skips repeats', () => {
    setAppBadgeCount(3);
    setAppBadgeCount(3);
    setAppBadgeCount(0);
    expect(setAppBadge.mock.calls).toEqual([[3]]);
    expect(clear).toHaveBeenCalledTimes(1);
    clearAppBadge();
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it('is a no-op without the Badging API', () => {
    delete (navigator as { setAppBadge?: unknown }).setAppBadge;
    expect(() => setAppBadgeCount(2)).not.toThrow();
  });

  it("counts the scout's assignments still to do, and leaves the badge alone while loading", () => {
    const { rerender } = renderHook(({ list }) => useAssignmentBadge(list), { initialProps: { list: null as MyAssignment[] | null } });
    expect(setAppBadge).not.toHaveBeenCalled();
    rerender({ list: [item(false), item(true), item(false)] });
    expect(setAppBadge).toHaveBeenLastCalledWith(2);
    rerender({ list: [item(true)] });
    expect(clear).toHaveBeenCalled();
  });
});
