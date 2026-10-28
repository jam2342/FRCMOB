import type { SeasonStoryEvent, SeasonStoryMatch } from '../../api';
import { matchLabelFromKey } from '../../utils/matchLabel';
import { ordinal } from '../../utils/ordinal';

export function recordText(record: SeasonStoryEvent['record']): string {
  return `${record.wins}–${record.losses}${record.ties ? `–${record.ties}` : ''}`;
}

// The one line an event is remembered by.
export function finishText(event: SeasonStoryEvent): string {
  if (event.finish === 'won') return 'Won the event';
  if (event.finish === 'finalist') return 'Finalists';
  if (event.finish === 'playoffs') return 'Made the playoffs';
  if (!event.qual_rank) return 'Played quals';
  return event.field_size ? `Ranked ${ordinal(event.qual_rank)} of ${event.field_size}` : `Ranked ${ordinal(event.qual_rank)}`;
}

// The qual rank as its own figure, unless the finish line already says it.
export function rankFigure(event: SeasonStoryEvent): string | null {
  return event.finish === 'quals' ? null : rankText(event);
}

export function rankText(event: SeasonStoryEvent): string | null {
  return event.qual_rank && event.field_size ? `${ordinal(event.qual_rank)} of ${event.field_size}` : null;
}

export function bestMatchText(match: SeasonStoryMatch | null): string | null {
  if (!match || match.score == null) return null;
  const label = matchLabelFromKey(match.match_key) ?? match.match_key;
  return match.opponent_score == null ? `${label} · ${match.score}` : `${label} · ${match.score}–${match.opponent_score}`;
}
