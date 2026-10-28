import { resolveApiBaseUrl } from '../../platform/runtime';

// A team's matches at an event as a calendar feed. `subscribe` (webcal:) keeps a calendar app
// re-reading it as times move; `download` is the same file over https for apps that only import.
export function teamCalendarUrls(eventKey: string, teamKey: string): { subscribe: string; download: string } {
  const path = `${resolveApiBaseUrl()}/matches/event/${encodeURIComponent(eventKey)}/team/${encodeURIComponent(teamKey)}/calendar.ics`;
  const download = new URL(path, typeof window === 'undefined' ? 'https://localhost/' : window.location.origin).href;
  return { subscribe: download.replace(/^https?:/, 'webcal:'), download };
}
