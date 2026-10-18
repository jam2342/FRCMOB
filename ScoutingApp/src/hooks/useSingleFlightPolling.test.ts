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

  it('re-runs once when a background refresh replaces data the page showed', async () => {
    const reasons: SingleFlightPollReason[] = [];
    const run = vi.fn((reason: SingleFlightPollReason) => {
      reasons.push(reason);
      return Promise.resolve();
    });
    const { unmount } = renderHook(() => useSingleFlightPolling({ enabled: true, intervalMs: 60000, run }));
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      window.dispatchEvent(new CustomEvent('api:updated', { detail: { url: '/api/a' } }));
      window.dispatchEvent(new CustomEvent('api:updated', { detail: { url: '/api/b' } }));
      vi.advanceTimersByTime(300);
      await Promise.resolve();
    });
    expect(reasons).toEqual(['initial', 'update']);
    unmount();
    await act(async () => {
      window.dispatchEvent(new CustomEvent('api:updated', { detail: { url: '/api/c' } }));
      vi.advanceTimersByTime(300);
      await Promise.resolve();
    });
    expect(reasons).toEqual(['initial', 'update']);
  });
});

describe('pull-to-refresh registry', () => {
  it('refreshes every visible poll once and forgets polls that stop', async () => {
    const { activePollCount, refreshActivePolls } = await import('./useSingleFlightPolling');
    const runs: SingleFlightPollReason[] = [];
    const run = vi.fn(async (reason: SingleFlightPollReason) => {
      runs.push(reason);
    });
    const hidden = vi.fn();
    const visible = renderHook(() => useSingleFlightPolling({ enabled: true, intervalMs: 60000, run }));
    renderHook(() => useSingleFlightPolling({ enabled: true, visible: false, intervalMs: 60000, run: hidden }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(activePollCount()).toBe(1);
    await act(async () => {
      expect(await refreshActivePolls()).toBe(1);
    });
    expect(runs).toEqual(['initial', 'manual']);
    expect(hidden).not.toHaveBeenCalled();
    visible.unmount();
    expect(activePollCount()).toBe(0);
  });
});
