import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PullToRefresh } from './PullToRefresh';

const refresh = vi.fn(() => Promise.resolve());
vi.mock('../../hooks/useSingleFlightPolling', () => ({
  activePollCount: () => 1,
  refreshActivePolls: () => refresh(),
}));
vi.mock('../../api', () => ({ readFreshFor: () => () => undefined }));
vi.mock('../../utils/haptics', () => ({ hapticTap: () => undefined }));

function setup() {
  const area = document.createElement('div');
  document.body.appendChild(area);
  render(<PullToRefresh areaRef={{ current: area }} pathname="/events" />);
  const touch = (type: 'touchStart' | 'touchMove' | 'touchEnd', x: number, y: number) =>
    act(() => {
      fireEvent[type](area, { touches: type === 'touchEnd' ? [] : [{ clientX: x, clientY: y }] });
    });
  return { touch };
}

describe('PullToRefresh', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    refresh.mockClear();
    window.localStorage.setItem('frcmob_pull_refresh_force', '1');
  });

  // Swiping a tab strip sideways drifts a few pixels down; that used to start a pull and then
  // block the strip's own scrolling.
  it('leaves a sideways swipe alone even when it drifts down', () => {
    const { touch } = setup();
    touch('touchStart', 300, 100);
    touch('touchMove', 260, 104);
    touch('touchMove', 120, 240);
    expect(screen.queryByRole('status')).toBeNull();
    touch('touchEnd', 120, 240);
    expect(refresh).not.toHaveBeenCalled();
  });

  it('refreshes on a pull straight down', () => {
    const { touch } = setup();
    touch('touchStart', 200, 100);
    touch('touchMove', 202, 120);
    touch('touchMove', 204, 260);
    expect(screen.getByRole('status')).toHaveTextContent('Release to refresh');
    touch('touchEnd', 204, 260);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('decides once: a swipe that starts sideways never turns into a pull', () => {
    const { touch } = setup();
    touch('touchStart', 300, 100);
    touch('touchMove', 280, 101);
    touch('touchMove', 280, 300);
    expect(screen.queryByRole('status')).toBeNull();
  });
});
