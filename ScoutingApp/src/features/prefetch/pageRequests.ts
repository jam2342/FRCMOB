import {
  getEventRankings,
  getEventPredictionSchedule,
  getEventTeamsHistory,
  getTeamIntel,
  getTeamLogo,
} from '../../api';
import { SAVED_COPY_MAX_AGE_MS } from '../../config/savedCopy';
import { SEASON } from '../../config/season';

// The first requests a few pages make, shared by those pages and by intentPrefetch
// (which starts them when a finger touches a link). One definition each, so a
// prefetched answer always has the URL the page then asks for. The pages poll
// through useSingleFlightPolling, so they may be answered with a saved copy first
// (SAVED_COPY_MAX_AGE_MS): the fresh one replaces it a moment later.

const EVENT_TEAMS_HISTORY_LIMIT = 6;

export function loadTeamCenterIntel(teamKey: string, eventKey?: string) {
  return getTeamIntel(teamKey, {
    event_key: eventKey,
    preferred_year: SEASON.year,
    fallback_year: SEASON.fallbackYear,
    include_tba: true,
    allow_season_fallback: true,
    auto_heal_ratings: true,
  }, {
    timeoutMs: 25000,
    showSavedMaxAgeMs: SAVED_COPY_MAX_AGE_MS.eventData,
  });
}

export function loadTeamCenterLogo(teamKey: string, eventKey?: string) {
  return getTeamLogo(teamKey, eventKey);
}

export function loadMatchCenterPredictions(eventKey: string) {
  return getEventPredictionSchedule(eventKey, {
    showSavedMaxAgeMs: SAVED_COPY_MAX_AGE_MS.matchData,
  });
}

export function loadEventsTeamsHistory(eventKey: string) {
  return getEventTeamsHistory(eventKey, EVENT_TEAMS_HISTORY_LIMIT, false, {
    showSavedMaxAgeMs: SAVED_COPY_MAX_AGE_MS.eventData,
  });
}

export function loadEventsRankings(eventKey: string) {
  return getEventRankings(eventKey, { showSavedMaxAgeMs: SAVED_COPY_MAX_AGE_MS.matchData });
}
