import { Fragment, useEffect, useState } from 'react';
import { getTeamSeasonStory, type SeasonStoryEvent, type SeasonStoryResponse } from '../../api';
import { EmptyState } from '../../components/ui/EmptyState';
import { SkeletonBlock } from '../../components/ui/SkeletonBlock';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button } from '../../components/ui/primitives';
import { seasonLabel } from '../../config/season';
import { metric } from '../../pages/centerUtils';
import { SharePictureDialog } from '../shareCard/SharePictureDialog';
import { renderRecapCard } from './renderRecapCard';
import { bestMatchText, finishText, rankFigure, recordText } from './story';
import styles from './SeasonStory.module.css';

type Props = { teamKey: string; season?: number; title?: string; onOpenEvent?: (eventKey: string) => void };

function teamNumber(teamKey: string): number {
  return Number(teamKey.replace(/^frc/i, '')) || 0;
}

// A team's season told event by event, ending in the season's totals. Each event can be shared
// as a picture. Drawn only once the team has played somewhere this season.
export function SeasonStoryCard({ teamKey, season, title = 'Season story', onOpenEvent }: Props) {
  const scope = `${teamKey}|${season ?? ''}`;
  const [loaded, setLoaded] = useState<{ scope: string; story: SeasonStoryResponse } | null>(null);
  const [failed, setFailed] = useState(false);
  const [sharing, setSharing] = useState<SeasonStoryEvent | null>(null);

  useEffect(() => {
    let live = true;
    setFailed(false);
    getTeamSeasonStory(teamKey, season)
      .then((story) => { if (live) setLoaded({ scope, story }); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [teamKey, season, scope]);

  const story = loaded?.scope === scope ? loaded.story : null;
  if (story && story.events.length === 0) return null;
  const number = teamNumber(teamKey);
  const totals = story?.totals;
  const seasonBest = totals?.best_match ? bestMatchText(totals.best_match) : null;
  // Which event, once there's more than one to choose from.
  const seasonBestAt = story && story.events.length > 1
    ? story.events.find((event) => event.event_key === totals?.best_match?.event_key)?.event_name
    : undefined;

  return (
    <SurfaceCard
      title={title}
      subtitle={story ? seasonLabel(story.season) : undefined}
      compactable
    >
      {!story && !failed ? <SkeletonBlock rows={3} compact /> : null}
      {failed && !story ? <EmptyState compact type="offline" title="Couldn't load the season" description="It retries when you come back." /> : null}
      {story && totals ? (
        <>
          <ol className={styles.timeline}>
            {story.events.map((event) => {
              const rank = rankFigure(event);
              const best = bestMatchText(event.best_match);
              return (
                <li key={event.event_key} className={`${styles.event} ${event.finish === 'won' ? styles.won : ''}`.trim()}>
                  <span className={styles.dot} aria-hidden="true" />
                  <div className={styles.body}>
                    {onOpenEvent ? (
                      <button type="button" className={styles.name} onClick={() => onOpenEvent(event.event_key)}>{event.event_name}</button>
                    ) : (
                      <strong className={styles.name}>{event.event_name}</strong>
                    )}
                    <p className={styles.headline}>{finishText(event)} · {recordText(event.record)}</p>
                    <p className={styles.meta}>
                      {/* Each fact on one line: a narrow screen used to break a score after its dash. */}
                      {[rank ? `Qual rank ${rank}` : null, event.rating_0_100 != null ? `Rating ${metric(event.rating_0_100, 1)}` : null, best ? `Best ${best}` : null]
                        .filter(Boolean)
                        .map((fact, i) => (
                          // The separator sits outside the span, so the line can still break there.
                          <Fragment key={fact}>{i ? ' · ' : ''}<span className={styles.fact}>{fact}</span></Fragment>
                        ))}
                    </p>
                  </div>
                  <Button size="sm" variant="quiet" onClick={() => setSharing(event)} aria-label={`Share ${event.event_name} recap`}>
                    Share
                  </Button>
                </li>
              );
            })}
          </ol>
          <p className={styles.totals}>
            <strong>{totals.events} event{totals.events === 1 ? '' : 's'} · {recordText(totals)}</strong>
            {totals.event_wins ? ` · ${totals.event_wins} event win${totals.event_wins === 1 ? '' : 's'}` : ''}
            {seasonBest ? (
              <>
                {' · season best '}
                <span className={styles.fact}>{seasonBest}</span>
                {seasonBestAt ? ` at ${seasonBestAt}` : ''}
              </>
            ) : null}
          </p>
        </>
      ) : null}
      {sharing && story ? (
        <SharePictureDialog
          open
          onClose={() => setSharing(null)}
          title="Share this event"
          render={(siteLabel) => renderRecapCard({ teamNumber: number, nickname: story.nickname, event: sharing, siteLabel })}
          cardKey={`${teamKey}|${sharing.event_key}|${recordText(sharing.record)}|${sharing.finish}`}
          text={`Team ${number} at ${sharing.event_name}: ${finishText(sharing)}, ${recordText(sharing.record)}.`}
          route={`/team-center?team=${teamKey}&event=${sharing.event_key}`}
          filename={`${number}-${sharing.event_key}-recap.png`}
        />
      ) : null}
    </SurfaceCard>
  );
}
