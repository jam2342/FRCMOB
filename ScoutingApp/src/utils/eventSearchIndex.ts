import { type EventSearchItem } from '../api';
import { loadSeasonEventCatalog, loadSeasonSearchFallback } from '../features/events/eventCatalog';

const EVENT_SEARCH_INDEX_TTL_MS = 5 * 60 * 1000;
const DEFAULT_INDEX_LIMIT = 260;

const eventSearchIndexByKey = new Map<string, EventSearchItem>();
let eventSearchIndexWarmPromise: Promise<EventSearchItem[]> | null = null;
let eventSearchIndexWarmedAtMs = 0;

function normalizedEventKey(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase();
}

function mergeEventLists(...lists: EventSearchItem[][]): EventSearchItem[] {
  const byKey = new Map<string, EventSearchItem>();
  for (const list of lists) {
    for (const event of list) {
      const key = normalizedEventKey(event.event_key);
      if (!key) continue;
      const previous = byKey.get(key);
      if (!previous) {
        byKey.set(key, event);
        continue;
      }
      byKey.set(key, {
        ...previous,
        ...event,
        start_date: event.start_date || previous.start_date,
        end_date: event.end_date || previous.end_date,
      });
    }
  }
  return Array.from(byKey.values());
}

function sortedIndexSnapshot(): EventSearchItem[] {
  return Array.from(eventSearchIndexByKey.values()).sort((a, b) => {
    if (a.year !== b.year) return b.year - a.year;
    return a.event_key.localeCompare(b.event_key);
  });
}

export function getEventSearchIndexSnapshot(): EventSearchItem[] {
  return sortedIndexSnapshot();
}

export function mergeEventsIntoSearchIndex(events: EventSearchItem[]): void {
  for (const event of events) {
    const key = normalizedEventKey(event.event_key);
    if (!key) continue;
    const previous = eventSearchIndexByKey.get(key);
    if (!previous) {
      eventSearchIndexByKey.set(key, event);
      continue;
    }
    eventSearchIndexByKey.set(key, {
      ...previous,
      ...event,
      start_date: event.start_date || previous.start_date,
      end_date: event.end_date || previous.end_date,
    });
  }
}

export async function preloadEventSearchIndex(options?: {
  preferredYear?: number;
  fallbackYear?: number;
  limit?: number;
  force?: boolean;
}): Promise<EventSearchItem[]> {
  const preferredYear = Number.isFinite(options?.preferredYear) ? Number(options?.preferredYear) : new Date().getUTCFullYear();
  const fallbackYear = Number.isFinite(options?.fallbackYear) ? Number(options?.fallbackYear) : preferredYear - 1;
  const limit = Math.max(40, Math.min(options?.limit ?? DEFAULT_INDEX_LIMIT, 400));
  const force = options?.force === true;

  const now = Date.now();
  if (!force && eventSearchIndexByKey.size > 0 && now - eventSearchIndexWarmedAtMs < EVENT_SEARCH_INDEX_TTL_MS) {
    return sortedIndexSnapshot();
  }
  if (eventSearchIndexWarmPromise) return eventSearchIndexWarmPromise;

  eventSearchIndexWarmPromise = (async () => {
    try {
      let events: EventSearchItem[] = [];
      try {
        events = await loadSeasonEventCatalog({
          preferredYear,
          fallbackYear,
          limit,
          minTarget: Math.min(120, limit),
          preferLiveNow: true,
          remoteTeamCountFetchLimit: 0,
        });
      } catch {
        events = await loadSeasonSearchFallback({ preferredYear, fallbackYear, limit });
      }

      if (events.length > 0 && events.length < Math.min(120, limit)) {
        const fallback = await loadSeasonSearchFallback({ preferredYear, fallbackYear, limit });
        events = mergeEventLists(events, fallback);
      }

      mergeEventsIntoSearchIndex(events.slice(0, limit));
      eventSearchIndexWarmedAtMs = Date.now();
      return sortedIndexSnapshot();
    } catch {
      return sortedIndexSnapshot();
    } finally {
      eventSearchIndexWarmPromise = null;
    }
  })();

  return eventSearchIndexWarmPromise;
}
