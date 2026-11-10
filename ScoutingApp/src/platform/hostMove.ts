import { isNativeApp } from './runtime';
import { listQueuedMutations } from '../utils/offlineQueue';
import { listPendingSessions, openDb } from '../features/onDevice/offlineStore';

// The site moved to frcmob.com on 2026-11-10. Browser storage belongs to each address,
// so the old one hands people over only when nothing here is still waiting to reach
// the server: queued edits, recordings not yet uploaded, unsaved pit drafts. Until
// then it keeps working (and syncing) as before, and moves them on a later visit.
export const OLD_HOST = 'scouting-app-iryg.vercel.app';
export const NEW_ORIGIN = 'https://frcmob.com';
const PIT_DRAFT_PREFIX = 'frcmob_pit_draft_v1:';

function hasPitDrafts(): boolean {
  for (let i = 0; i < localStorage.length; i += 1) {
    if (localStorage.key(i)?.startsWith(PIT_DRAFT_PREFIX)) return true;
  }
  return false;
}

// Anything unreadable counts as "something might be here": stay rather than strand it.
export async function nothingLeftHere(): Promise<boolean> {
  try {
    if ((await listQueuedMutations()).length > 0) return false;
    if (hasPitDrafts()) return false;
    if (typeof indexedDB !== 'undefined') {
      const db = await openDb();
      try {
        if ((await listPendingSessions(db)).length > 0) return false;
      } finally {
        db.close();
      }
    }
    return true;
  } catch {
    return false;
  }
}

let moving = false;
type Place = Pick<Location, 'hostname' | 'search' | 'hash' | 'replace'>;

export async function moveToNewHostIfSafe(place: Place = window.location): Promise<boolean> {
  if (moving || isNativeApp() || place.hostname !== OLD_HOST) return false;
  if (!(await nothingLeftHere())) return false;
  moving = true;
  place.replace(`${NEW_ORIGIN}/${place.search}${place.hash}`);
  return true;
}

// Try at start, and again whenever the queue changes (it empties as it syncs).
export function startHostMove(): void {
  if (isNativeApp() || typeof window === 'undefined' || window.location.hostname !== OLD_HOST) return;
  void moveToNewHostIfSafe();
  window.addEventListener('offlinequeue:change', () => { void moveToNewHostIfSafe(); });
}
