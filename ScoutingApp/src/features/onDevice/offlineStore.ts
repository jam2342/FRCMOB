// Offline store for the on-device flow (IndexedDB). Scouting happens where there's no
// connectivity, so calibrations and produced match breakdowns are persisted locally and
// flushed to the server when back online. Promise-wrapped; the IDBFactory is injectable
// so it unit-tests under fake-indexeddb.

export type StoredCalibration = {
  id: string; // e.g. event key, or "current"
  homography: number[][];
  createdAt: number;
};

export type StoredSession = {
  id: string;
  eventKey?: string;
  matchKey?: string;
  createdAt: number;
  synced: boolean;
  payload: unknown; // produced points-by-team / shift-play result to sync upstream
};

const DB_NAME = 'frcmob-ondevice';
const DB_VERSION = 1;
const CALIBRATIONS = 'calibrations';
const SESSIONS = 'sessions';

function req<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export function openDb(factory: IDBFactory = globalThis.indexedDB): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const open = factory.open(DB_NAME, DB_VERSION);
    open.onupgradeneeded = () => {
      const db = open.result;
      if (!db.objectStoreNames.contains(CALIBRATIONS)) db.createObjectStore(CALIBRATIONS, { keyPath: 'id' });
      if (!db.objectStoreNames.contains(SESSIONS)) db.createObjectStore(SESSIONS, { keyPath: 'id' });
    };
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}

async function withStore<T>(
  db: IDBDatabase,
  store: string,
  mode: IDBTransactionMode,
  fn: (s: IDBObjectStore) => Promise<T> | T,
): Promise<T> {
  const tx = db.transaction(store, mode);
  const result = await fn(tx.objectStore(store));
  await new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
  return result;
}

export async function saveCalibration(db: IDBDatabase, cal: StoredCalibration): Promise<void> {
  await withStore(db, CALIBRATIONS, 'readwrite', (s) => req(s.put(cal)));
}

export async function getCalibration(db: IDBDatabase, id: string): Promise<StoredCalibration | undefined> {
  return withStore(db, CALIBRATIONS, 'readonly', (s) => req<StoredCalibration | undefined>(s.get(id)));
}

export async function saveSession(db: IDBDatabase, session: StoredSession): Promise<void> {
  await withStore(db, SESSIONS, 'readwrite', (s) => req(s.put(session)));
}

// Internal: listPendingSessions is the entry point callers actually use.
async function listSessions(db: IDBDatabase): Promise<StoredSession[]> {
  return withStore(db, SESSIONS, 'readonly', (s) => req<StoredSession[]>(s.getAll()));
}

export async function listPendingSessions(db: IDBDatabase): Promise<StoredSession[]> {
  return (await listSessions(db)).filter((s) => !s.synced);
}

export async function markSessionSynced(db: IDBDatabase, id: string): Promise<void> {
  const existing = await withStore(db, SESSIONS, 'readonly', (s) => req<StoredSession | undefined>(s.get(id)));
  if (!existing) return;
  await saveSession(db, { ...existing, synced: true });
}

// Flush pending sessions upstream when back online. `post` is injected (it targets
// POST /tracks/on-device-session — see features/onDevice/sync.ts) so the store stays
// decoupled from the API client and unit-tests without a network.
export async function syncPendingSessions(
  db: IDBDatabase,
  post: (session: StoredSession) => Promise<void>,
): Promise<{ synced: number; failed: number }> {
  let synced = 0;
  let failed = 0;
  for (const session of await listPendingSessions(db)) {
    try {
      await post(session);
      await markSessionSynced(db, session.id);
      synced += 1;
    } catch {
      failed += 1;
    }
  }
  return { synced, failed };
}
