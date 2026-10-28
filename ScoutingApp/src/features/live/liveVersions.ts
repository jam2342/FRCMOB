// What the live results socket (liveResults.ts) has said about each event, read by
// api.ts. Kept free of imports so api.ts can use it without a cycle.
//
// When an event's results change, its live reads skip this tab's cached copies for a
// moment and carry the new version in the URL (`lv`), a new key for the edge cache:
// the first phone gets the fresh answer from the origin and the rest from the edge.
// `lv` is left out of cache keys, so saved copies stay under the plain URL offline.

const FRESH_WINDOW_MS = 10_000;
// An event is in play while a match without a result is due within this window.
const IN_PLAY_WINDOW_SEC = 60 * 60;

const versions = new Map<string, string>();
const freshUntil = new Map<string, number>();
let interestListener: ((eventKey: string) => void) | null = null;

export function setLiveInterestListener(listener: ((eventKey: string) => void) | null): void {
  interestListener = listener;
}

type ScheduleMatch = {
  is_completed?: boolean;
  actual_time?: number | null;
  predicted_time?: number | null;
  scheduled_time?: number | null;
};

export function eventInPlay(matches: readonly ScheduleMatch[], nowSec = Date.now() / 1000): boolean {
  return matches.some((match) => {
    if (match.is_completed) return false;
    const at = match.actual_time ?? match.predicted_time ?? match.scheduled_time;
    return typeof at === 'number' && Math.abs(at - nowSec) <= IN_PLAY_WINDOW_SEC;
  });
}

// A page just loaded this event's schedule: worth a socket if a match is due soon.
export function noteLiveSchedule(eventKey: string, matches: readonly ScheduleMatch[] | undefined): void {
  if (!interestListener || !Array.isArray(matches) || !eventInPlay(matches)) return;
  interestListener(eventKey.trim().toLowerCase());
}

// Returns true when this is news: a version that differs from one already known.
// The first version an event's socket reports is only remembered.
export function applyLiveVersion(eventKey: string, version: string, nowMs = Date.now()): boolean {
  const key = eventKey.trim().toLowerCase();
  const previous = versions.get(key);
  versions.set(key, version);
  if (previous === undefined || previous === version) return false;
  freshUntil.set(key, nowMs + FRESH_WINDOW_MS);
  return true;
}

const EVENT_IN_PATH = /\/(?:matches\/event|events)\/([0-9]{4}[a-z0-9]+)\//i;

function eventsInUrl(url: string): string[] {
  const inPath = EVENT_IN_PATH.exec(url);
  if (inPath) return [inPath[1].toLowerCase()];
  const query = url.split('?')[1];
  if (!query || !url.includes('/matches/schedules')) return [];
  const keys = new URLSearchParams(query).get('event_keys');
  return keys ? keys.split(',').map((key) => key.trim().toLowerCase()).filter(Boolean) : [];
}

// A live read for an event whose results just changed skips cached copies.
export function liveFreshRead(url: string, nowMs = Date.now()): boolean {
  if (!url.includes('lv=')) return false;
  return eventsInUrl(url).some((key) => (freshUntil.get(key) ?? 0) > nowMs);
}

// The URL with the known results version of its event(s), once one has changed.
export function withLiveVersion(url: string, eventKeys: readonly string[]): string {
  const known = eventKeys
    .map((key) => key.trim().toLowerCase())
    .filter((key) => freshUntil.has(key) && versions.has(key))
    .sort()
    .map((key) => versions.get(key))
    .join('.');
  if (!known) return url;
  return `${url}${url.includes('?') ? '&' : '?'}lv=${encodeURIComponent(known)}`;
}

export function stripLiveVersion(url: string): string {
  return url.replace(/([?&])lv=[^&]*(&?)/, (_match, lead: string, tail: string) => (tail ? lead : '')).replace(/[?&]$/, '');
}

export function resetLiveVersionsForTests(): void {
  versions.clear();
  freshUntil.clear();
  interestListener = null;
}
