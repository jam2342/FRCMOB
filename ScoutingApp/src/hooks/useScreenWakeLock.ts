import { useEffect } from 'react';

// Keeps the screen awake while `active`. A scout watches the robot, not the phone, and a
// recorder points the camera at the field: thirty seconds without a touch and the phone dims,
// then locks, mid-match. The browser drops the lock whenever the page is hidden, so it is taken
// again on the way back. Released as soon as `active` goes false. Where the Screen Wake Lock
// API is missing or refuses (older iOS, low battery mode), the screen simply sleeps as before.
export function useScreenWakeLock(active: boolean): void {
  useEffect(() => {
    if (!active || typeof navigator === 'undefined' || !('wakeLock' in navigator)) return undefined;
    let sentinel: WakeLockSentinel | null = null;
    let cancelled = false;

    const acquire = async () => {
      if (cancelled || document.visibilityState !== 'visible' || (sentinel && !sentinel.released)) return;
      try {
        const next = await navigator.wakeLock.request('screen');
        if (cancelled) {
          void next.release().catch(() => undefined);
          return;
        }
        sentinel = next;
      } catch {
        // Refused (battery saver, permissions policy): the screen sleeps as it normally would.
      }
    };

    const onVisibility = () => {
      if (document.visibilityState === 'visible') void acquire();
    };

    void acquire();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      cancelled = true;
      document.removeEventListener('visibilitychange', onVisibility);
      if (sentinel && !sentinel.released) void sentinel.release().catch(() => undefined);
      sentinel = null;
    };
  }, [active]);
}
