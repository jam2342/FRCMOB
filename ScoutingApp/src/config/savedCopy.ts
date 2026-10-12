// How old a saved copy may be and still be shown while the network catches up
// (the API client's showSavedMaxAgeMs). Opt-in per call, and only for pages that
// re-render on "api:updated" (useSingleFlightPolling does): the fresh answer
// replaces the saved one a moment later. A page that loads once would keep it.
export const SAVED_COPY_MAX_AGE_MS = {
  eventData: 24 * 60 * 60_000,
  matchData: 60 * 60_000,
  schedule: 10 * 60_000,
} as const;
