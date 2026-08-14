import { getSuggestedEvents, searchEvents, type EventSearchItem } from '../../api';

type EventCatalogOptions = {
  preferredYear?: number;
  fallbackYear?: number;
  limit?: number;
  minTarget?: number;
  preferLiveNow?: boolean;
  remoteTeamCountFetchLimit?: number;
};

function normalizeCatalogEventKey(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase();
}

function mergeCatalogEventLists(...lists: EventSearchItem[][]): EventSearchItem[] {
  const byKey = new Map<string, EventSearchItem>();
  for (const list of lists) {
    for (const event of list) {
      const key = normalizeCatalogEventKey(event.event_key);
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

export async function loadSeasonSearchFallback(options?: EventCatalogOptions): Promise<EventSearchItem[]> {
  const preferredYear = options?.preferredYear ?? new Date().getUTCFullYear();
  const fallbackYear = options?.fallbackYear ?? preferredYear - 1;
  const limit = Math.max(1, Math.min(options?.limit ?? 120, 900));

  const [primarySearch, fallbackSearch] = await Promise.allSettled([
    searchEvents(String(preferredYear)),
    searchEvents(String(fallbackYear)),
  ]);

  return mergeCatalogEventLists(
    primarySearch.status === 'fulfilled' ? primarySearch.value.events || [] : [],
    fallbackSearch.status === 'fulfilled' ? fallbackSearch.value.events || [] : [],
  ).slice(0, limit);
}

export async function loadSeasonEventCatalog(options?: EventCatalogOptions): Promise<EventSearchItem[]> {
  const preferredYear = options?.preferredYear ?? new Date().getUTCFullYear();
  const fallbackYear = options?.fallbackYear ?? preferredYear - 1;
  const limit = Math.max(1, Math.min(options?.limit ?? 120, 900));
  const minTarget = Math.max(0, Math.min(options?.minTarget ?? 0, limit));
  const preferLiveNow = options?.preferLiveNow ?? true;
  const remoteTeamCountFetchLimit = Math.max(0, Math.floor(options?.remoteTeamCountFetchLimit ?? 0));

  let suggested: EventSearchItem[] = [];
  try {
    const payload = await getSuggestedEvents(preferredYear, fallbackYear, limit, {
      preferLiveNow,
      remoteTeamCountFetchLimit,
    });
    suggested = payload.events || [];
  } catch {
    suggested = [];
  }

  if (suggested.length === 0) {
    return loadSeasonSearchFallback({ preferredYear, fallbackYear, limit });
  }

  if (minTarget > 0 && suggested.length < minTarget) {
    const fallback = await loadSeasonSearchFallback({ preferredYear, fallbackYear, limit });
    return mergeCatalogEventLists(suggested, fallback).slice(0, limit);
  }

  return suggested.slice(0, limit);
}
