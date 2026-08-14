import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useOnlineStatus } from './useOnlineStatus';

function StatusProbe() {
  const { online } = useOnlineStatus();
  return <div data-testid="status">{online ? 'online' : 'offline'}</div>;
}

function setNavigatorOnline(value: boolean) {
  Object.defineProperty(window.navigator, 'onLine', {
    configurable: true,
    value,
  });
}

describe('useOnlineStatus', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    setNavigatorOnline(true);
  });

  it('does not let a stale successful health ping override a newer offline event', async () => {
    vi.useFakeTimers();
    setNavigatorOnline(true);

    let resolveFetch: (value: { ok: boolean }) => void = () => {};
    const pendingFetch = new Promise<{ ok: boolean }>((resolve) => {
      resolveFetch = resolve;
    });
    vi.stubGlobal('fetch', vi.fn(() => pendingFetch));

    const { unmount } = render(<StatusProbe />);

    await act(async () => {
      vi.advanceTimersByTime(0);
      await Promise.resolve();
    });

    expect(fetch).toHaveBeenCalledOnce();
    expect(screen.getByTestId('status')).toHaveTextContent('online');

    act(() => {
      setNavigatorOnline(false);
      window.dispatchEvent(new Event('offline'));
    });

    expect(screen.getByTestId('status')).toHaveTextContent('offline');

    await act(async () => {
      resolveFetch({ ok: true });
      await pendingFetch;
      await Promise.resolve();
    });

    expect(screen.getByTestId('status')).toHaveTextContent('offline');
    unmount();
  });

  it('shares one health poll between consumers', async () => {
    vi.useFakeTimers();
    setNavigatorOnline(true);
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true })));

    const { unmount } = render(
      <>
        <StatusProbe />
        <StatusProbe />
        <StatusProbe />
      </>,
    );

    await act(async () => {
      vi.advanceTimersByTime(0);
      await Promise.resolve();
    });

    expect(fetch).toHaveBeenCalledOnce();
    unmount();
  });

  it('ignores an older health result after a newer check succeeds', async () => {
    vi.useFakeTimers();
    setNavigatorOnline(true);
    let resolveFirst: (value: { ok: boolean }) => void = () => {};
    let resolveSecond: (value: { ok: boolean }) => void = () => {};
    const first = new Promise<{ ok: boolean }>((resolve) => { resolveFirst = resolve; });
    const second = new Promise<{ ok: boolean }>((resolve) => { resolveSecond = resolve; });
    vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second));

    const { unmount } = render(<StatusProbe />);
    await act(async () => {
      vi.advanceTimersByTime(0);
      await Promise.resolve();
    });
    act(() => window.dispatchEvent(new Event('online')));

    await act(async () => {
      resolveSecond({ ok: true });
      await second;
      await Promise.resolve();
    });
    await act(async () => {
      resolveFirst({ ok: false });
      await first;
      await Promise.resolve();
    });

    expect(screen.getByTestId('status')).toHaveTextContent('online');
    unmount();
  });
});
