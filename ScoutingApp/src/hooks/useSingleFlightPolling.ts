import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

// 'update': a background refresh replaced data the page may be showing (a saved
// copy it was answered with first); re-running reads the fresh copy from memory.
export type SingleFlightPollReason = 'initial' | 'poll' | 'manual' | 'resume' | 'update';

const UPDATE_DEBOUNCE_MS = 250;
const REFRESH_WAIT_LIMIT_MS = 15000;

// Every poll running on screen, so pull-to-refresh can refresh what the page shows.
const activeRefreshers = new Set<() => Promise<void>>();

export function activePollCount(): number {
  return activeRefreshers.size;
}

// Runs every visible poll now, as a manual refresh, and resolves when they have all answered.
export async function refreshActivePolls(): Promise<number> {
  const runs = Array.from(activeRefreshers, (refresh) => refresh().catch(() => undefined));
  await Promise.all(runs);
  return runs.length;
}

type UseSingleFlightPollingOptions = {
  enabled: boolean;
  intervalMs: number;
  run: (reason: SingleFlightPollReason) => Promise<boolean | void> | boolean | void;
  visible?: boolean;
  backoffMultiplier?: number;
  maxBackoffMs?: number;
  minBackoffMs?: number;
};

type UseSingleFlightPollingResult = {
  requestInFlight: boolean;
  triggerNow: (reason?: SingleFlightPollReason) => void;
};

function normalizedIntervalMs(raw: number): number {
  if (!Number.isFinite(raw)) return 10000;
  return Math.max(1000, Math.floor(raw));
}

export function useSingleFlightPolling(options: UseSingleFlightPollingOptions): UseSingleFlightPollingResult {
  const {
    enabled,
    intervalMs,
    run,
    visible = true,
    backoffMultiplier = 1.75,
    maxBackoffMs = 120000,
    minBackoffMs = 2000,
  } = options;
  const [requestInFlight, setRequestInFlight] = useState(false);
  const triggerRef = useRef<(reason: SingleFlightPollReason) => void>(() => undefined);
  const safeIntervalMs = useMemo(() => normalizedIntervalMs(intervalMs), [intervalMs]);

  // Use a ref for `run` so that changing the callback identity does NOT
  // tear down and restart the timer / re-execute 'initial'.  The effect
  // below only depends on scheduling-related values.
  const runRef = useRef(run);
  runRef.current = run;

  const triggerNow = useCallback((reason: SingleFlightPollReason = 'manual') => {
    triggerRef.current(reason);
  }, []);

  useEffect(() => {
    if (!enabled || !visible) {
      triggerRef.current = () => undefined;
      setRequestInFlight(false);
      return;
    }

    let disposed = false;
    let timer: number | null = null;
    let requestInFlightFlag = false;
    // A manual/initial refresh asked for while a request is running (e.g. the user picked
    // another event) runs as soon as that request settles, not a whole interval later.
    let pendingReason: SingleFlightPollReason | null = null;
    let nextDelayMs = safeIntervalMs;

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = (delayMs: number) => {
      if (disposed) return;
      clearTimer();
      const bounded = Math.max(300, Math.floor(delayMs));
      timer = window.setTimeout(() => {
        void execute('poll');
      }, bounded);
    };

    const execute = async (reason: SingleFlightPollReason) => {
      if (disposed || !enabled || !visible) return;
      if (requestInFlightFlag) {
        if (reason !== 'poll') pendingReason = reason;
        else schedule(nextDelayMs);
        return;
      }

      requestInFlightFlag = true;
      setRequestInFlight(true);
      let successful = true;
      try {
        const result = await runRef.current(reason);
        successful = result !== false;
      } catch {
        successful = false;
      } finally {
        requestInFlightFlag = false;
        if (!disposed) setRequestInFlight(false);
        if (disposed || !enabled || !visible) {
          clearTimer();
        } else {
          if (successful) {
            nextDelayMs = safeIntervalMs;
          } else {
            const lowerBoundMs = Math.max(Math.floor(minBackoffMs), safeIntervalMs);
            const upperBoundMs = Math.max(lowerBoundMs, Math.floor(maxBackoffMs));
            const stepped = Math.round(nextDelayMs * Math.max(1.1, backoffMultiplier));
            nextDelayMs = Math.max(lowerBoundMs, Math.min(upperBoundMs, stepped));
          }
          if (pendingReason) {
            const queued = pendingReason;
            pendingReason = null;
            void execute(queued);
          } else {
            schedule(nextDelayMs);
          }
        }
      }
    };

    triggerRef.current = (reason: SingleFlightPollReason) => {
      clearTimer();
      void execute(reason);
    };

    const refreshNow = async () => {
      // A run already in flight answers first; then a fresh manual one.
      const startedAt = Date.now();
      while (requestInFlightFlag && !disposed && Date.now() - startedAt < REFRESH_WAIT_LIMIT_MS) {
        await new Promise((resolve) => window.setTimeout(resolve, 80));
      }
      if (disposed) return;
      clearTimer();
      await execute('manual');
    };
    activeRefreshers.add(refreshNow);

    void execute('initial');
    let updateTimer: number | null = null;
    const onUpdated = () => {
      if (updateTimer !== null) window.clearTimeout(updateTimer);
      updateTimer = window.setTimeout(() => {
        updateTimer = null;
        void execute('update');
      }, UPDATE_DEBOUNCE_MS);
    };
    window.addEventListener('api:updated', onUpdated);
    return () => {
      disposed = true;
      activeRefreshers.delete(refreshNow);
      triggerRef.current = () => undefined;
      clearTimer();
      window.removeEventListener('api:updated', onUpdated);
      if (updateTimer !== null) window.clearTimeout(updateTimer);
    };
  }, [
    backoffMultiplier,
    enabled,
    maxBackoffMs,
    minBackoffMs,
    safeIntervalMs,
    visible,
  ]);

  return {
    requestInFlight,
    triggerNow,
  };
}
