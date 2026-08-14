import { useEffect, useMemo, useState } from 'react';
import { useLiveRatings } from '../hooks/useLiveRatings';
import { RatingSparkline } from './ui/RatingSparkline';
import { RatingTrendBadge } from './ui/RatingTrendBadge';
import './LiveRatingsPanel.css';

type LiveRatingsPanelProps = {
  eventKey: string | null | undefined;
  enabled?: boolean;
  maxRows?: number;
  title?: string;
};

function formatAgo(fetchedAtMs: number | null, nowMs: number): string {
  if (fetchedAtMs == null) return 'never';
  const sec = Math.max(0, Math.round((nowMs - fetchedAtMs) / 1000));
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  return `${min}m ago`;
}

// SofaScore-style live rating board: ranked teams whose values update on a
// poll, with a momentum sparkline + delta badge and a green flash on any row
// whose rating moved since the last poll.
export function LiveRatingsPanel({
  eventKey,
  enabled = true,
  maxRows = 50,
  title = 'Live ratings',
}: LiveRatingsPanelProps) {
  const { ratings, lastFetchedAtMs, loading, error, recentChanges, refreshNow } =
    useLiveRatings(eventKey, { enabled });

  // Tick a clock so "updated Ns ago" stays current between polls.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  // Flash rows that changed on the latest poll, then clear after one pulse.
  // The flash set is derived from recentChanges (the hook resets it each poll),
  // and a separate effect only *schedules* the clear — no synchronous setState
  // in an effect body.
  const [clearedChangeRef, setClearedChangeRef] = useState<unknown>(null);
  useEffect(() => {
    if (recentChanges.size === 0) return;
    const timer = window.setTimeout(() => setClearedChangeRef(recentChanges), 1500);
    return () => window.clearTimeout(timer);
  }, [recentChanges]);
  const flashTeams = useMemo<Set<string>>(
    () =>
      clearedChangeRef === recentChanges ? new Set() : new Set(recentChanges.keys()),
    [clearedChangeRef, recentChanges],
  );

  const rows = useMemo(() => ratings.slice(0, maxRows), [ratings, maxRows]);

  if (!eventKey) return null;

  return (
    <section className="live-ratings-panel" aria-live="polite">
      <header className="live-ratings-panel__header">
        <div className="live-ratings-panel__title">
          <span className="live-ratings-panel__dot" aria-hidden="true" />
          <h3>{title}</h3>
        </div>
        <div className="live-ratings-panel__meta">
          <span className="live-ratings-panel__ago">
            Updated {formatAgo(lastFetchedAtMs, nowMs)}
          </span>
          <button
            type="button"
            className="live-ratings-panel__refresh"
            onClick={refreshNow}
            disabled={loading}
          >
            Refresh
          </button>
        </div>
      </header>

      {error ? (
        <p className="live-ratings-panel__error">{error}</p>
      ) : null}

      {rows.length === 0 && loading ? (
        <p className="live-ratings-panel__empty">Loading live ratings…</p>
      ) : null}

      <ol className="live-ratings-panel__list">
        {rows.map((row, index) => {
          const change = recentChanges.get(row.team_key);
          const flashing = flashTeams.has(row.team_key);
          const flashDir =
            change && change.delta > 0 ? 'up' : change && change.delta < 0 ? 'down' : '';
          return (
            <li
              key={row.team_key}
              className={`live-ratings-row${flashing ? ` is-flashing flash-${flashDir}` : ''}`}
            >
              <span className="live-ratings-row__rank">{index + 1}</span>
              <span className="live-ratings-row__team">
                <span className="live-ratings-row__number">
                  {row.team_number ?? row.team_key.replace(/^frc/, '')}
                </span>
                {row.nickname ? (
                  <span className="live-ratings-row__nick">{row.nickname}</span>
                ) : null}
              </span>
              <RatingSparkline trend={row.rating_trend} className="live-ratings-row__spark" />
              <RatingTrendBadge trend={row.rating_trend} className="live-ratings-row__badge" />
              <span className="live-ratings-row__rating">
                {Number(row.rating_0_100).toFixed(1)}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
