import { TUTORIAL_SCOPES, type TutorialScope } from '../layout/userSettings';

const TUTORIAL_SEEN_STORAGE_KEY = 'scouting_tutorial_seen_v1';
const TUTORIAL_CHECKLIST_STORAGE_KEY = 'scouting_tutorial_checklist_v2';

export const SCOUTING_TUTORIAL_PROGRESS_EVENT = 'scouting:tutorial-progress';

export type TutorialSeenMap = Partial<Record<TutorialScope, number>>;
export type TutorialChecklistScopeMap = Record<string, number>;
export type TutorialChecklistMap = Partial<Record<TutorialScope, TutorialChecklistScopeMap>>;

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

function normalizeChecklistScopeMap(value: unknown): TutorialChecklistScopeMap {
  if (!value || typeof value !== 'object') return {};
  const parsed = value as Record<string, unknown>;
  const normalized: TutorialChecklistScopeMap = {};
  for (const [itemId, rawValue] of Object.entries(parsed)) {
    const key = String(itemId || '').trim();
    if (!key) continue;
    const checkedAt = Number(rawValue);
    if (!Number.isFinite(checkedAt) || checkedAt <= 0) continue;
    normalized[key] = Math.floor(checkedAt);
  }
  return normalized;
}

function readRawChecklistMap(): TutorialChecklistMap {
  if (typeof window === 'undefined') return {};
  const raw = window.localStorage.getItem(TUTORIAL_CHECKLIST_STORAGE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const normalized: TutorialChecklistMap = {};
    for (const scope of TUTORIAL_SCOPES) {
      const scopeMap = normalizeChecklistScopeMap(parsed[scope]);
      if (Object.keys(scopeMap).length) {
        normalized[scope] = scopeMap;
      }
    }
    return normalized;
  } catch {
    return {};
  }
}

function writeRawChecklistMap(map: TutorialChecklistMap): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(TUTORIAL_CHECKLIST_STORAGE_KEY, JSON.stringify(map));
}

export function readTutorialSeenMap(): TutorialSeenMap {
  return readRawMap();
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

export function readTutorialChecklistScope(scope: TutorialScope): TutorialChecklistScopeMap {
  const scopeMap = readRawChecklistMap()[scope];
  if (!scopeMap) return {};
  return { ...scopeMap };
}

export function isTutorialChecklistItemChecked(scope: TutorialScope, itemId: string): boolean {
  const normalizedItemId = String(itemId || '').trim();
  if (!normalizedItemId) return false;
  const scopeMap = readRawChecklistMap()[scope];
  if (!scopeMap) return false;
  return Number.isFinite(scopeMap[normalizedItemId]) && Number(scopeMap[normalizedItemId]) > 0;
}

export function setTutorialChecklistItem(
  scope: TutorialScope,
  itemId: string,
  checked: boolean,
  checkedAtMs: number = Date.now(),
): TutorialChecklistMap {
  const normalizedItemId = String(itemId || '').trim();
  if (!normalizedItemId) return readRawChecklistMap();
  const current = readRawChecklistMap();
  const scopeMap = { ...(current[scope] || {}) };
  if (checked) {
    scopeMap[normalizedItemId] = Math.max(1, Math.floor(checkedAtMs));
  } else {
    delete scopeMap[normalizedItemId];
  }
  if (Object.keys(scopeMap).length) {
    current[scope] = scopeMap;
  } else {
    delete current[scope];
  }
  writeRawChecklistMap(current);
  emitTutorialProgressEvent(readRawMap());
  return current;
}

export function clearTutorialChecklist(scope?: TutorialScope): TutorialChecklistMap {
  if (!scope) {
    writeRawChecklistMap({});
    emitTutorialProgressEvent(readRawMap());
    return {};
  }
  const current = readRawChecklistMap();
  delete current[scope];
  writeRawChecklistMap(current);
  emitTutorialProgressEvent(readRawMap());
  return current;
}

export function countCompletedChecklistItems(scope: TutorialScope, itemIds: string[]): number {
  const normalizedItemIds = Array.from(
    new Set(
      itemIds
        .map((value) => String(value || '').trim())
        .filter(Boolean),
    ),
  );
  if (!normalizedItemIds.length) return 0;
  const scopeMap = readRawChecklistMap()[scope];
  if (!scopeMap) return 0;
  return normalizedItemIds.reduce((count, itemId) => (
    Number.isFinite(scopeMap[itemId]) && Number(scopeMap[itemId]) > 0 ? count + 1 : count
  ), 0);
}

export function countSeenTutorials(): number {
  const map = readRawMap();
  return TUTORIAL_SCOPES.reduce((count, scope) => (map[scope] ? count + 1 : count), 0);
}
