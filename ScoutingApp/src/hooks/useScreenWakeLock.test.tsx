import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useScreenWakeLock } from './useScreenWakeLock';

type FakeSentinel = { released: boolean; release: ReturnType<typeof vi.fn> };

const sentinels: FakeSentinel[] = [];
const request = vi.fn(async () => {
  const sentinel: FakeSentinel = { released: false, release: vi.fn(async () => { sentinel.released = true; }) };
  sentinels.push(sentinel);
  return sentinel;
});

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  Object.defineProperty(navigator, 'wakeLock', { value: { request }, configurable: true });
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
});

afterEach(() => {
  sentinels.length = 0;
  request.mockClear();
  delete (navigator as { wakeLock?: unknown }).wakeLock;
});

describe('useScreenWakeLock', () => {
  it('does nothing while inactive', () => {
    renderHook(() => useScreenWakeLock(false));
    expect(request).not.toHaveBeenCalled();
  });

  it('holds the screen awake while active and lets go when the match is over', async () => {
    const { rerender } = renderHook(({ active }) => useScreenWakeLock(active), { initialProps: { active: true } });
    await vi.waitFor(() => expect(sentinels).toHaveLength(1));
    expect(request).toHaveBeenCalledWith('screen');
    rerender({ active: false });
    expect(sentinels[0].release).toHaveBeenCalled();
  });

  it('takes the lock again when the scout comes back to the app', async () => {
    renderHook(() => useScreenWakeLock(true));
    await vi.waitFor(() => expect(sentinels).toHaveLength(1));
    sentinels[0].released = true; // the browser drops it on hide
    setVisibility('hidden');
    setVisibility('visible');
    await vi.waitFor(() => expect(sentinels).toHaveLength(2));
  });

  it('carries on quietly when the browser refuses', async () => {
    request.mockRejectedValueOnce(new Error('NotAllowedError'));
    expect(() => renderHook(() => useScreenWakeLock(true))).not.toThrow();
    await vi.waitFor(() => expect(request).toHaveBeenCalled());
  });

  it('is a no-op where the API is missing', () => {
    delete (navigator as { wakeLock?: unknown }).wakeLock;
    expect(() => renderHook(() => useScreenWakeLock(true))).not.toThrow();
  });
});
