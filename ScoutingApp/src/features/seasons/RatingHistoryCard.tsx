import { useEffect, useState } from 'react';
import { getTeamRatingHistory, type RatingHistoryResponse } from '../../api';
import { EmptyState } from '../../components/ui/EmptyState';
import { SkeletonBlock } from '../../components/ui/SkeletonBlock';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { seasonLabel } from '../../config/season';
import { metric } from '../../pages/centerUtils';
import styles from './RatingHistoryCard.module.css';

type Props = { teamKey: string; onOpenEvent: (eventKey: string) => void };

// A team's FRCMOB rating at each event it played, season by season. Each season is its
// own game and scale, so they're listed apart and never compared.
export function RatingHistoryCard({ teamKey, onOpenEvent }: Props) {
  const [data, setData] = useState<{ teamKey: string; history: RatingHistoryResponse } | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setFailed(false);
    getTeamRatingHistory(teamKey)
      .then((history) => { if (live) setData({ teamKey, history }); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [teamKey]);

  const history = data?.teamKey === teamKey ? data.history : null;
  const firstRated = history?.first_rated_season ?? null;
  return (
    <SurfaceCard title="Ratings by season" subtitle="Each season is a different game, so its ratings only compare within that season." compactable>
      {!history && !failed ? <SkeletonBlock rows={3} compact /> : null}
      {failed ? <EmptyState compact type="offline" title="Couldn't load rating history" description="It retries when you come back to this tab." /> : null}
      {history && history.seasons.length === 0 ? (
        <EmptyState compact title="No rated events yet" description="Ratings appear once the team plays official matches." />
      ) : null}
      {history?.seasons.map((season) => (
        <section key={season.season} className={styles.season} aria-label={`${season.season} ratings`}>
          <h4 className={styles.heading}>
            {seasonLabel(season.season)}
            {season.current ? null : <span className={styles.tag}>final</span>}
          </h4>
          <ul className={styles.list}>
            {season.events.map((event) => (
              <li key={event.event_key}>
                <button type="button" className={styles.row} onClick={() => onOpenEvent(event.event_key)}>
                  <span className={styles.name}>{event.event_name || event.event_key}</span>
                  <span className={styles.figures}>
                    <strong>{metric(event.rating_0_100, 1)}</strong>
                    {event.rank && event.field_size ? ` · ${event.rank} of ${event.field_size}` : ''}
                    {typeof event.points_per_match === 'number' ? ` · ${metric(event.points_per_match, 0)} pts/match` : ''}
                    {event.won_event ? <span className={styles.won}>Won</span> : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {history && firstRated ? (
        <p className={styles.note}>Seasons before {firstRated} have results but no FRCMOB ratings.</p>
      ) : null}
    </SurfaceCard>
  );
}
