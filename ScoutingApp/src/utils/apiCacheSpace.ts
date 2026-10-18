// The app keeps copies of API answers in localStorage for offline use. Left alone
// they grew until the origin's ~5 MB was full (browsing 6-7 Championship divisions
// did it), and then a scout's Save failed with "storage is full" — over data the
// app can fetch again. So the copies get a budget, and scouting work always wins:
// a save that hits the limit throws the copies out and tries again.

export const API_CACHE_PREFIX = 'scouting_api_cache_v1:';
// Room for plenty of saved pages while leaving most of the quota to scouting.
export const API_CACHE_BUDGET_CHARS = 1_500_000;

type Row = { key: string; chars: number; storedAtMs: number };

function cacheRows(): Row[] {
  const rows: Row[] = [];
  try {
    const storage = window.localStorage;
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (!key || !key.startsWith(API_CACHE_PREFIX)) continue;
      const raw = storage.getItem(key) ?? '';
      // The timestamp leads the JSON; no need to parse whole response bodies.
      const stored = /"storedAtMs":(\d+)/.exec(raw.slice(0, 80));
      rows.push({ key, chars: key.length + raw.length, storedAtMs: stored ? Number(stored[1]) : 0 });
    }
  } catch {
    // Storage unreadable: nothing to free.
  }
  return rows;
}

export function apiCacheChars(): number {
  return cacheRows().reduce((sum, row) => sum + row.chars, 0);
}

// Removes the oldest saved API copies until they use at most `targetChars`.
// Returns how many characters were freed.
export function freeApiCacheSpace(targetChars = 0): number {
  const rows = cacheRows().sort((a, b) => a.storedAtMs - b.storedAtMs);
  let total = rows.reduce((sum, row) => sum + row.chars, 0);
  let freed = 0;
  for (const row of rows) {
    if (total <= Math.max(0, targetChars)) break;
    try {
      window.localStorage.removeItem(row.key);
      total -= row.chars;
      freed += row.chars;
    } catch {
      break;
    }
  }
  return freed;
}

// For scouting work that must be kept: if storage is full, the app's own saved API
// copies go first, then the write is tried once more. Still throws if it can't fit.
export function setItemMakingRoom(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    if (!freeApiCacheSpace(0)) throw error;
    window.localStorage.setItem(key, value);
  }
}
