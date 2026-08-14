import { useCallback, useMemo, useRef, useState } from 'react';
import { getEventRatingsLive, type EventTeamRatingItem } from '../api';
import { useLiveRefreshSetting } from './useLiveRefreshSetting';
import { useOnlineStatus } from './useOnlineStatus';
import { usePageVisibility } from './usePageVisibility';
import {
  useSingleFlightPolling,
  type SingleFlightPollReason,
} from './useSingleFlightPolling';

// Floor on the poll cadence. The user's liveRefreshSec setting is shared with
// other live views; ratings recompute is cheap server-side but we never poll
// faster than this to stay friendly to venue wifi.
const MIN_LIVE_INTERVAL_MS = 8000;
const DEFAULT_LIVE_INTERVAL_MS = 20000;

export type LiveRatingChange = {
  teamKey: string;
  previous: number;
  next: number;
  delta: number;
};

export type UseLiveRatingsResult = {
  ratings: EventTeamRatingItem[];
  lastUpdatedAt: string | null;
  // Server clock at last successful poll (ms epoch) so the UI can show
  // "updated Ns ago" without trusting the device clock against the server.
  lastFetchedAtMs: number | null;
  loading: boolean;
  error: string | null;
  // team_key -> change since the previous successful poll, for flash animation.
  recentChanges: Map<string, LiveRatingChange>;
  refreshNow: () => void;
};

function intervalMsFromSetting(liveRefreshSec: number): number {
  const raw = Number(liveRefreshSec);
  if (!Number.isFinite(raw) || raw <= 0) return DEFAULT_LIVE_INTERVAL_MS;
  return Math.max(MIN_LIVE_INTERVAL_MS, Math.floor(raw * 1000));
}

export function useLiveRatings(
  eventKey: string | null | undefined,
  options?: { enabled?: boolean },
): UseLiveRatingsResult {
  const enabledOption = options?.enabled ?? true;
  const visible = usePageVisibility();
  const { online } = useOnlineStatus();
  const liveRefreshSec = useLiveRefreshSetting();
  const intervalMs = useMemo(() => intervalMsFromSetting(liveRefreshSec), [liveRefreshSec]);

  const [ratings, setRatings] = useState<EventTeamRatingItem[]>([]);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const [lastFetchedAtMs, setLastFetchedAtMs] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recentChanges, setRecentChanges] = useState<Map<string, LiveRatingChange>>(
    () => new Map(),
  );

  // Previous rating-by-team for diffing, kept in a ref so a poll doesn't
  // depend on render state.
  const prevByTeamRef = useRef<Map<string, number>>(new Map());

  const enabled = Boolean(eventKey) && enabledOption && online;

  const run = useCallback(
    async (reason: SingleFlightPollReason) => {
      // useSingleFlightPolling guarantees only one run at a time, so we don't
      // pre-empt an in-flight request — that previously surfaced spurious
      // "signal is aborted" errors. We still pass a signal for clean teardown.
      if (!eventKey) return false;
      if (reason === 'initial') setLoading(true);
      try {
        const resp = await getEventRatingsLive(eventKey);
        const nextRatings = resp.ratings ?? [];

        const changes = new Map<string, LiveRatingChange>();
        const nextByTeam = new Map<string, number>();
        for (const row of nextRatings) {
          const next = Number(row.rating_0_100);
          nextByTeam.set(row.team_key, next);
          const previous = prevByTeamRef.current.get(row.team_key);
          if (previous != null && Math.abs(next - previous) >= 0.05) {
            changes.set(row.team_key, {
              teamKey: row.team_key,
              previous,
              next,
              delta: Number((next - previous).toFixed(2)),
            });
          }
        }
        prevByTeamRef.current = nextByTeam;

        setRatings(nextRatings);
        setLastUpdatedAt(resp.last_updated_at ?? null);
        setLastFetchedAtMs(Date.now());
        setError(null);
        // Only surface changes on incremental polls, not the first load
        // (everything would "change" from empty otherwise).
        if (reason !== 'initial') setRecentChanges(changes);
        return true;
      } catch (err) {
        // Aborted requests (navigation/teardown) are not real errors.
        if (err instanceof DOMException && err.name === 'AbortError') return false;
        setError(err instanceof Error ? err.message : 'Failed to load live ratings');
        return false;
      } finally {
        if (reason === 'initial') setLoading(false);
      }
    },
    [eventKey],
  );

  const { triggerNow } = useSingleFlightPolling({
    enabled,
    intervalMs,
    run,
    visible,
  });

  const refreshNow = useCallback(() => triggerNow('manual'), [triggerNow]);

  return {
    ratings,
    lastUpdatedAt,
    lastFetchedAtMs,
    loading,
    error,
    recentChanges,
    refreshNow,
  };
}
