import type { EventSearchItem } from '../../api';

// Read off the name; an event that matches none (a Championship division, an
// off-season) gets no tag rather than a generic "Event".
export function eventTypeTags(event: EventSearchItem): string[] {
  const name = (event.name || '').toLowerCase();
  const tags: string[] = [];
  if (name.includes('district')) tags.push('District');
  if (name.includes('regional')) tags.push('Regional');
  if (name.includes('league')) tags.push('League');
  if (name.includes('championship') || name.includes('cmp')) tags.push('Championship');
  return tags;
}
