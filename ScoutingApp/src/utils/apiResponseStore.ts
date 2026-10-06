import { isNativeApp } from '../platform/runtime';
import { getDatabaseResponse, putDatabaseResponse, pruneDatabaseResponses } from './apiResponseDatabase';
// Saved API responses too big for localStorage. Events are usually run with little or
// no signal, and the responses a scout needs most are the big ones: an event's ratings
// (1 MB at a regional, 6.6 MB at Championship), the schedule with synergy (1.5 MB) and
// team intel (1.2 MB). localStorage caps an origin at ~5 MB and the API client skips
// anything over 450 KB there, so without this those pages came up empty offline.
// Cache Storage holds hundreds of MB. The service worker leaves "frcmob-api-*" alone.

const STORE_NAME = 'frcmob-api-v1';
const KEY_ORIGIN = 'https://frcmob-api-cache.invalid/';
// A prepared event may have 75+ team pages in addition to schedules, ratings and
// predictions. Some of those pages spill here when localStorage is full.
const MAX_ENTRIES = 250;
const PRUNE_EVERY_MS = 60_000;

export type StoredResponse = { response: Response; storedAtMs: number };

function available(): boolean {
  return typeof caches !== 'undefined' && typeof Request !== 'undefined';
}

function keyRequest(requestKey: string): Request {
  return new Request(KEY_ORIGIN + encodeURIComponent(requestKey));
}

async function open(): Promise<Cache | null> {
  if (!available()) return null;
  try {
    return await caches.open(STORE_NAME);
  } catch {
    return null; // private browsing and some embedded webviews refuse Cache Storage
  }
}

export async function putStoredResponse(
  requestKey: string,
  bodyText: string,
  meta: { status: number; statusText: string; headers: Headers; retainUntilMs: number },
): Promise<boolean> {
  const fallback = async () => {
    const saved = await putDatabaseResponse({key: requestKey, body: bodyText, status: meta.status, statusText: meta.statusText, headers: Array.from(meta.headers.entries()), storedAt: Date.now(), retainUntil: meta.retainUntilMs});
    if (saved) void pruneStoredResponses();
    return saved;
  };
  const cache = isNativeApp() ? null : await open();
  if (!cache) return fallback();
  const headers = new Headers(meta.headers);
  headers.set('x-frcmob-stored-at', String(Date.now()));
  headers.set('x-frcmob-retain-until', String(meta.retainUntilMs));
  try {
    await cache.put(keyRequest(requestKey), new Response(bodyText, { status: meta.status, statusText: meta.statusText, headers }));
    void pruneStoredResponses();
    return Boolean(await cache.match(keyRequest(requestKey)));
  } catch {
    // quota or storage failure: the app still works online
    return fallback();
  }
}

export async function getStoredResponse(requestKey: string): Promise<StoredResponse | null> {
  const fallback = async () => {
    const row = await getDatabaseResponse(requestKey);
    return row ? {response: new Response(row.body, {status: row.status, statusText: row.statusText, headers: row.headers}), storedAtMs: row.storedAt} : null;
  };
  const cache = isNativeApp() ? null : await open();
  if (!cache) return fallback();
  try {
    const hit = await cache.match(keyRequest(requestKey));
    if (!hit) return fallback();
    const retainUntil = Number(hit.headers.get('x-frcmob-retain-until') || 0);
    if (retainUntil && retainUntil <= Date.now()) {
      void cache.delete(keyRequest(requestKey));
      return fallback();
    }
    return { response: hit, storedAtMs: Number(hit.headers.get('x-frcmob-stored-at') || 0) };
  } catch {
    return fallback();
  }
}

let lastPruneAtMs = 0;

// Drop expired entries, then the oldest beyond MAX_ENTRIES.
async function pruneStoredResponses(): Promise<void> {
  if (Date.now() - lastPruneAtMs < PRUNE_EVERY_MS) return;
  lastPruneAtMs = Date.now();
  await pruneDatabaseResponses(MAX_ENTRIES);
  const cache = await open();
  if (!cache) return;
  try {
    const keys = await cache.keys();
    const rows: { req: Request; storedAtMs: number }[] = [];
    for (const req of keys) {
      const res = await cache.match(req);
      const retainUntil = Number(res?.headers.get('x-frcmob-retain-until') || 0);
      if (!res || (retainUntil && retainUntil <= Date.now())) {
        await cache.delete(req);
        continue;
      }
      rows.push({ req, storedAtMs: Number(res.headers.get('x-frcmob-stored-at') || 0) });
    }
    rows.sort((a, b) => a.storedAtMs - b.storedAtMs);
    for (const row of rows.slice(0, Math.max(0, rows.length - MAX_ENTRIES))) await cache.delete(row.req);
  } catch {
    // best effort
  }
}
