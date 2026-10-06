import { afterEach, describe, expect, it, vi } from 'vitest';
import { coalesceRefresh } from './sessionChanges';

afterEach(() => vi.useRealTimers());

describe('session refresh coalescing', () => {
  it('combines notifications and an explicit sync refresh into one read', async () => {
    vi.useFakeTimers();
    const read = vi.fn(async () => {});
    const refresh = coalesceRefresh(read);
    for (let i = 0; i < 6; i++) refresh.schedule();
    await refresh.flush();
    await vi.runAllTimersAsync();
    expect(read).toHaveBeenCalledTimes(1);
    refresh.cancel();
  });

  it('queues one follow-up behind a slow read and cancels work on unmount', async () => {
    vi.useFakeTimers();
    let finish!: () => void;
    const read = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const refresh = coalesceRefresh(read);
    refresh.schedule();
    await vi.advanceTimersByTimeAsync(0);
    for (let i = 0; i < 6; i++) refresh.schedule();
    expect(read).toHaveBeenCalledTimes(1);
    finish();
    await vi.advanceTimersByTimeAsync(0);
    expect(read).toHaveBeenCalledTimes(2);
    refresh.schedule();
    refresh.cancel();
    finish();
    await vi.runAllTimersAsync();
    expect(read).toHaveBeenCalledTimes(2);
  });
});
