import { TUTORIAL_SCOPES, type TutorialScope } from '../layout/userSettings';

const TUTORIAL_SEEN_STORAGE_KEY = 'scouting_tutorial_seen_v1';
const TUTORIAL_CHECKLIST_STORAGE_KEY = 'scouting_tutorial_checklist_v2';

export const SCOUTING_TUTORIAL_PROGRESS_EVENT = 'scouting:tutorial-progress';

export type TutorialSeenMap = Partial<Record<TutorialScope, number>>;

function readRawMap(): TutorialSeenMap {
  if (typeof window === 'undefined') return {};
  const raw = window.localStorage.getItem(TUTORIAL_SEEN_STORAGE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const normalized: TutorialSeenMap = {};
    for (const scope of TUTORIAL_SCOPES) {
      const value = Number(parsed[scope]);
      if (Number.isFinite(value) && value > 0) {
        normalized[scope] = Math.floor(value);
      }
    }
    return normalized;
  } catch {
    return {};
  }
}

function writeRawMap(map: TutorialSeenMap): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(TUTORIAL_SEEN_STORAGE_KEY, JSON.stringify(map));
}

function emitTutorialProgressEvent(map: TutorialSeenMap): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<TutorialSeenMap>(SCOUTING_TUTORIAL_PROGRESS_EVENT, { detail: map }));
}

export function hasSeenTutorial(scope: TutorialScope): boolean {
  const seen = readRawMap()[scope];
  return Number.isFinite(seen) && Number(seen) > 0;
}

export function markTutorialSeen(scope: TutorialScope, seenAtMs: number = Date.now()): TutorialSeenMap {
  const current = readRawMap();
  current[scope] = Math.max(1, Math.floor(seenAtMs));
  writeRawMap(current);
  emitTutorialProgressEvent(current);
  return current;
}

export function markAllTutorialsSeen(seenAtMs: number = Date.now()): TutorialSeenMap {
  const seenAt = Math.max(1, Math.floor(seenAtMs));
  const next: TutorialSeenMap = {};
  for (const scope of TUTORIAL_SCOPES) {
    next[scope] = seenAt;
  }
  writeRawMap(next);
  emitTutorialProgressEvent(next);
  return next;
}

export function clearTutorialSeen(scope?: TutorialScope): TutorialSeenMap {
  if (!scope) {
    writeRawMap({});
    emitTutorialProgressEvent({});
    return {};
  }
  const current = readRawMap();
  delete current[scope];
  writeRawMap(current);
  emitTutorialProgressEvent(current);
  return current;
}

// Remove data left by the retired checklist feature when Settings resets tutorials.
export function clearTutorialChecklist(): void {
  if (typeof window !== 'undefined') window.localStorage.removeItem(TUTORIAL_CHECKLIST_STORAGE_KEY);
}

export function countSeenTutorials(): number {
  const map = readRawMap();
  return TUTORIAL_SCOPES.reduce((count, scope) => (map[scope] ? count + 1 : count), 0);
}
