import type { EventScheduleItem, EventSearchItem } from '../api';
import { liveTimerLabel } from './centerUtils';

export type HomeFilter = 'all' | 'live' | 'upcoming' | 'completed';

function normalizeHomeEventKey(eventKey: string | null | undefined): string {
  return String(eventKey || '').trim().toLowerCase();
}

function parseHomeEventDateToken(value: string | null | undefined): string | null {
  if (!value || typeof value !== 'string') return null;
  const token = value.trim().slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(token) ? token : null;
}

function localDateTokenFromMs(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function eventRunsOnDateToken(event: EventSearchItem | undefined, targetToken: string): boolean {
  const startToken = parseHomeEventDateToken(event?.start_date ?? null);
  const endToken = parseHomeEventDateToken(event?.end_date ?? null) ?? startToken;
  const first = startToken ?? endToken;
  const last = endToken ?? startToken;
  if (!first || !last) return false;
  const low = first <= last ? first : last;
  const high = first <= last ? last : first;
  return targetToken >= low && targetToken <= high;
}

export function hasResolvedMatchScores(match: Pick<EventScheduleItem, 'red_score' | 'blue_score'>): boolean {
  const red = match.red_score;
  const blue = match.blue_score;
  return (
    typeof red === 'number' &&
    Number.isFinite(red) &&
    red >= 0 &&
    typeof blue === 'number' &&
    Number.isFinite(blue) &&
    blue >= 0
  );
}

export function resolveHomeMatchState(
  match: EventScheduleItem,
  nowMs: number,
): ReturnType<typeof liveTimerLabel>['state'] {
  const timer = liveTimerLabel(match.scheduled_time, nowMs);
  const winner = match.winner_alliance || null;
  const isCompleted =
    Boolean(match.is_completed) ||
    winner === 'red' ||
    winner === 'blue' ||
    winner === 'tie' ||
    hasResolvedMatchScores(match);
  if (isCompleted) return 'ended';
  return timer.state;
}

function isSameLocalDate(unixSeconds: number | null, nowMs: number): boolean {
  if (typeof unixSeconds !== 'number' || !Number.isFinite(unixSeconds)) return false;
  const matchDate = new Date(unixSeconds * 1000);
  const nowDate = new Date(nowMs);
  return (
    matchDate.getFullYear() === nowDate.getFullYear() &&
    matchDate.getMonth() === nowDate.getMonth() &&
    matchDate.getDate() === nowDate.getDate()
  );
}

export function selectHomeWindowMatches(matches: EventScheduleItem[], nowMs: number): EventScheduleItem[] {
  if (matches.length === 0) return [];
  const nowSec = Math.floor(nowMs / 1000);
  const timedMatches = matches.filter(
    (match): match is EventScheduleItem & { scheduled_time: number } =>
      typeof match.scheduled_time === 'number' && Number.isFinite(match.scheduled_time),
  );

  // First preference: rolling "today" window so events in neighboring timezones still show expected current-day rows.
  const rollingWindowMatches = timedMatches.filter(
    (match) => match.scheduled_time >= (nowSec - (12 * 3600)) && match.scheduled_time <= (nowSec + (18 * 3600)),
  );
  if (rollingWindowMatches.length > 0) return rollingWindowMatches;

  const todayMatches = timedMatches.filter((match) => isSameLocalDate(match.scheduled_time, nowMs));
  if (todayMatches.length > 0) return todayMatches;

  if (timedMatches.length === 0) return matches;

  const latestScheduledSec = Math.max(...timedMatches.map((match) => match.scheduled_time));
  return timedMatches.filter((match) => isSameLocalDate(match.scheduled_time, latestScheduledSec * 1000));
}

export function selectHomeDayMatches(matches: EventScheduleItem[], dayMs: number): EventScheduleItem[] {
  if (matches.length === 0) return [];
  return matches.filter((match) => isSameLocalDate(match.scheduled_time, dayMs));
}

export function filterHomeWindowMatches(
  windowMatches: EventScheduleItem[],
  activeFilter: HomeFilter,
  nowMs: number,
): EventScheduleItem[] {
  if (activeFilter === 'all') return windowMatches;
  return windowMatches.filter((match) => {
    const state = resolveHomeMatchState(match, nowMs);
    if (activeFilter === 'live') return state === 'live';
    if (activeFilter === 'upcoming') return state === 'upcoming' || state === 'unknown';
    return state === 'ended';
  });
}

export function buildHomeFilterCounts(
  feedEventKeys: string[],
  scheduleByEvent: Record<string, { matches?: EventScheduleItem[] | null }>,
  nowMs: number,
  options?: { dayMs?: number | null },
): Record<HomeFilter, number> {
  const counts: Record<HomeFilter, number> = {
    all: 0,
    live: 0,
    upcoming: 0,
    completed: 0,
  };
  const dayMs = typeof options?.dayMs === 'number' && Number.isFinite(options.dayMs)
    ? options.dayMs
    : null;
  for (const eventKey of feedEventKeys) {
    const schedule = scheduleByEvent[eventKey];
    const windowMatches = dayMs !== null
      ? selectHomeDayMatches(schedule?.matches || [], dayMs)
      : selectHomeWindowMatches(schedule?.matches || [], nowMs);
    counts.all += windowMatches.length;
    for (const match of windowMatches) {
      const state = resolveHomeMatchState(match, nowMs);
      if (state === 'live') counts.live += 1;
      else if (state === 'upcoming' || state === 'unknown') counts.upcoming += 1;
      else counts.completed += 1;
    }
  }
  return counts;
}

export function pickMobileHomeAutoEventKey(
  events: EventSearchItem[],
  scheduleByEvent: Record<string, { matches?: EventScheduleItem[] | null }>,
  nowMs: number,
): string {
  const todayToken = localDateTokenFromMs(nowMs);
  const ranked = events
    .map((event) => {
      const eventKey = normalizeHomeEventKey(event.event_key);
      if (!eventKey) return null;

      const dayMatches = selectHomeDayMatches(scheduleByEvent[eventKey]?.matches || [], nowMs);
      const runsTodayByDate = eventRunsOnDateToken(event, todayToken);
      if (!runsTodayByDate && dayMatches.length === 0) return null;

      let liveCount = 0;
      let pendingCount = 0;
      let nextPendingSec = Number.POSITIVE_INFINITY;

      for (const match of dayMatches) {
        const state = resolveHomeMatchState(match, nowMs);
        if (state === 'live') liveCount += 1;
        if (state !== 'ended') {
          pendingCount += 1;
          if (typeof match.scheduled_time === 'number' && Number.isFinite(match.scheduled_time)) {
            nextPendingSec = Math.min(nextPendingSec, Math.floor(match.scheduled_time));
          }
        }
      }

      const priority =
        liveCount > 0
          ? 4
          : pendingCount > 0
            ? 3
            : dayMatches.length > 0
              ? 2
              : 1;

      return {
        eventKey,
        priority,
        liveCount,
        pendingCount,
        dayMatchCount: dayMatches.length,
        nextPendingSec,
      };
    })
    .filter((row): row is {
      eventKey: string;
      priority: number;
      liveCount: number;
      pendingCount: number;
      dayMatchCount: number;
      nextPendingSec: number;
    } => Boolean(row))
    .sort((a, b) => {
      if (a.priority !== b.priority) return b.priority - a.priority;
      if (a.liveCount !== b.liveCount) return b.liveCount - a.liveCount;
      if (a.pendingCount !== b.pendingCount) return b.pendingCount - a.pendingCount;
      if (a.dayMatchCount !== b.dayMatchCount) return b.dayMatchCount - a.dayMatchCount;
      if (a.nextPendingSec !== b.nextPendingSec) return a.nextPendingSec - b.nextPendingSec;
      return a.eventKey.localeCompare(b.eventKey);
    });

  return ranked[0]?.eventKey || '';
}
