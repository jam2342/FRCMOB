import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export type SingleFlightPollReason = 'initial' | 'poll' | 'manual' | 'resume';

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
        schedule(nextDelayMs);
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
          schedule(nextDelayMs);
        }
      }
    };

    triggerRef.current = (reason: SingleFlightPollReason) => {
      clearTimer();
      void execute(reason);
    };

    void execute('initial');
    return () => {
      disposed = true;
      triggerRef.current = () => undefined;
      clearTimer();
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
