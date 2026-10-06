import type { EventSearchItem } from '../api';

export function mergeEventLists(...lists: EventSearchItem[][]): EventSearchItem[] {
  const byKey = new Map<string, EventSearchItem>();
  for (const list of lists) {
    for (const event of list) {
      const key = String(event.event_key || '')
        .trim()
        .toLowerCase();
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
