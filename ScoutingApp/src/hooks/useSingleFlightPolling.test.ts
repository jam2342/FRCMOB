import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSingleFlightPolling, type SingleFlightPollReason } from './useSingleFlightPolling';

describe('useSingleFlightPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('runs a refresh requested mid-request as soon as that request settles', async () => {
    const reasons: SingleFlightPollReason[] = [];
    let release: () => void = () => undefined;
    const run = vi.fn((reason: SingleFlightPollReason) => {
      reasons.push(reason);
      return new Promise<void>((resolve) => {
        release = resolve;
      });
    });
    const { result } = renderHook(() => useSingleFlightPolling({ enabled: true, intervalMs: 60000, run }));
    expect(reasons).toEqual(['initial']);

    // The user picks another event while the first load is still running.
    act(() => result.current.triggerNow('manual'));
    expect(reasons).toEqual(['initial']);

    await act(async () => {
      release();
      await Promise.resolve();
    });
    expect(reasons).toEqual(['initial', 'manual']);
  });

  it('still waits for the interval between ordinary polls', async () => {
    const run = vi.fn(() => Promise.resolve());
    renderHook(() => useSingleFlightPolling({ enabled: true, intervalMs: 60000, run }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(run).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(59000);
      await Promise.resolve();
    });
    expect(run).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(1500);
      await Promise.resolve();
    });
    expect(run).toHaveBeenCalledTimes(2);
  });
});
