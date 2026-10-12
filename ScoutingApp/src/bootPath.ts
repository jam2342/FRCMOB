import { SEASON } from './config/season';

// The suggested-events list every page asks for at startup. index.html starts this
// request before the app's JS has downloaded (build/bootPrefetch.ts writes that
// script from bootSuggestedEventsPath), and apiFetch takes the answer from there
// (bootPrefetch.ts). No DOM here: the build script imports this file too.
export const SUGGESTED_EVENTS_SHARED_LIMIT = 260;
export const SUGGESTED_EVENTS_SHARED_TEAM_COUNT_FETCHES = 12;

export function suggestedEventsQuery(
  preferredYear: number,
  fallbackYear: number,
  limit: number,
  prioritizeRunning: boolean,
  teamCountFetchLimit: number,
): string {
  return new URLSearchParams({
    preferred_year: String(preferredYear),
    fallback_year: String(fallbackYear),
    limit: String(limit),
    prioritize_running: String(prioritizeRunning),
    team_count_fetch_limit: String(teamCountFetchLimit),
  }).toString();
}

export function bootSuggestedEventsPath(): string {
  return `/events/suggested?${suggestedEventsQuery(
    SEASON.year,
    SEASON.fallbackYear,
    SUGGESTED_EVENTS_SHARED_LIMIT,
    true,
    SUGGESTED_EVENTS_SHARED_TEAM_COUNT_FETCHES,
  )}`;
}
