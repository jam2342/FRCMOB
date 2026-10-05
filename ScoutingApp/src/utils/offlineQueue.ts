import { isNativeApp } from '../platform/runtime';
import { SecureStorage } from '../platform/secureStorage';
/**
 * Offline mutation queue.
 *
 * When a POST/PUT/DELETE request fails due to network error while the
 * device is offline, the request is persisted to IndexedDB.  When
 * connectivity returns the queue is replayed automatically.
 *
 * Each queued item includes a `clientEntryId` so the backend can
 * de-duplicate if the same request was already partially received.
 *
 * Storage: IndexedDB is the primary store (no practical quota for this
 * volume of data, survives localStorage eviction).  localStorage is the
 * fallback when IndexedDB is unavailable (some private-browsing modes).
 * Legacy localStorage queues are migrated into IndexedDB on first load.
 * Rejected writes remain stored for recovery. Storage failures dispatch an
 * `offlinequeue:dropped` event and reject the save so the form stays open.
 */
import { recordConfirmedSync, recordSyncIssue } from '../features/offline/syncReceipt';

// ── Types ──────────────────────────────────────────────────────

export interface QueuedMutation {
  /** Unique id for deduplication. */
  id: string;
  /** ISO timestamp when queued. */
  queuedAt: string;
  /** Destination URL (relative to API base or absolute). */
  url: string;
  /** HTTP method. */
  method: string;
  /** Serialised request body (JSON string). */
  body: string | null;
  /** Extra headers (content-type, auth tokens). */
  headers: Record<string, string>;
  /** Number of replay attempts so far. */
  attempts: number;
  /** Human-readable label for the UI (e.g. "Save scouting entry"). */
  label?: string;
  /** Retained for recovery; blocked writes are never automatically replayed. */
  failure?: { reason: 'server-rejected' | 'conflict'; status: number };
}

export interface DroppedMutationInfo {
  /** Why the item could not be saved or delivered. */
  reason: 'storage-failure' | 'server-rejected' | 'conflict';
  count: number;
  labels: string[];
}

// ── Storage keys / constants ───────────────────────────────────

const LEGACY_STORAGE_KEY = 'frcmob_offline_queue_v1';
const IDB_NAME = 'frcmob_offline_queue';
const IDB_VERSION = 1;
const IDB_STORE = 'mutations';
const REPLAY_TIMEOUT_MS = 12_000;

// ── Module state ───────────────────────────────────────────────

/** Last-known queue length, kept fresh so size() can stay synchronous. */
let knownCount = 0;
/** Set when IndexedDB open failed and we fell back to localStorage. */
let idbUnavailable = false;
let initPromise: Promise<void> | null = null;

function emitChange(count: number): void {
  knownCount = count;
  window.dispatchEvent(new CustomEvent('offlinequeue:change', { detail: { count } }));
}

function emitDropped(info: DroppedMutationInfo): void {
  recordSyncIssue(info);
  window.dispatchEvent(new CustomEvent('offlinequeue:dropped', { detail: info }));
}

// ── IndexedDB plumbing ─────────────────────────────────────────

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB unavailable'));
      return;
    }
    const req = indexedDB.open(IDB_NAME, IDB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(IDB_STORE)) {
        db.createObjectStore(IDB_STORE, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error('IndexedDB open failed'));
    req.onblocked = () => reject(new Error('IndexedDB open blocked'));
  });
}

function idbRequest<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error('IndexedDB request failed'));
  });
}

type ProtectedMutation = { id: string; secureVersion: 1; ciphertext: string };

async function idbReadAll(): Promise<QueuedMutation[]> {
  const db = await openDb();
  try {
    const tx = db.transaction(IDB_STORE, 'readonly');
    const rows = await idbRequest(tx.objectStore(IDB_STORE).getAll());
    const items: QueuedMutation[] = [];
    for (const row of (Array.isArray(rows) ? rows : []) as (QueuedMutation | ProtectedMutation)[]) {
      if ('secureVersion' in row) {
        if (!isNativeApp()) throw new Error('Protected changes require the native app.');
        const value = JSON.parse((await SecureStorage.decrypt({ value: row.ciphertext })).value) as QueuedMutation;
        if (value.id !== row.id) throw new Error('Protected change identity mismatch.');
        items.push(value);
      } else items.push(row);
    }
    return items.sort((a, b) => (a.queuedAt < b.queuedAt ? -1 : 1));
  } finally {
    db.close();
  }
}

function transactionDone(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(tx.error ?? new Error('Offline storage transaction aborted'));
    tx.onerror = () => reject(tx.error ?? new Error('Offline storage transaction failed'));
  });
}

async function idbPut(item: QueuedMutation): Promise<void> {
  const stored = isNativeApp()
    ? { id: item.id, secureVersion: 1, ciphertext: (await SecureStorage.encrypt({ value: JSON.stringify(item) })).value }
    : item;
  const db = await openDb();
  try {
    const tx = db.transaction(IDB_STORE, 'readwrite');
    const done = transactionDone(tx);
    tx.objectStore(IDB_STORE).put(stored);
    await done;
  } finally {
    db.close();
  }
}

async function idbDelete(ids: string[]): Promise<void> {
  if (ids.length === 0) return;
  const db = await openDb();
  try {
    const tx = db.transaction(IDB_STORE, 'readwrite');
    const store = tx.objectStore(IDB_STORE);
    const done = transactionDone(tx);
    for (const id of ids) store.delete(id);
    await done;
  } finally {
    db.close();
  }
}

// ── localStorage fallback (private-mode browsers without IDB) ──

function lsRead(): QueuedMutation[] {
  try {
    const raw = window.localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function lsWrite(queue: QueuedMutation[]): void {
  // Never sacrifice older scouting work to make a new save fit.
  window.localStorage.setItem(LEGACY_STORAGE_KEY, JSON.stringify(queue));
}

function lsClear(): void {
  try {
    window.localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    // ignore
  }
}

// ── Init / migration ───────────────────────────────────────────

/**
 * One-time startup: migrate any legacy localStorage queue into IndexedDB
 * and refresh the synchronous count cache.
 */
function ensureInit(): Promise<void> {
  if (initPromise) return initPromise;
  initPromise = (async () => {
    if (isNativeApp()) {
      const existing = await idbReadAll();
      const rows = new Map(existing.map(item => [item.id, item]));
      // Interrupted migration may leave duplicate legacy entries. IDB is newer.
      for (const item of lsRead()) if (!rows.has(item.id)) rows.set(item.id, item);
      for (const item of rows.values()) await idbPut(item);
      lsClear();
      emitChange([...rows.values()].filter(item => !item.failure).length);
      return;
    }
    try {
      const legacy = lsRead();
      if (legacy.length > 0) {
        for (const item of legacy) {
          await idbPut(item);
        }
        lsClear();
      }
      emitChange((await idbReadAll()).filter((item) => !item.failure).length);
    } catch {
      idbUnavailable = true;
      emitChange(lsRead().filter((item) => !item.failure).length);
    }
  })().catch(error => { initPromise = null; throw error; });
  return initPromise;
}

async function readQueue(): Promise<QueuedMutation[]> {
  await ensureInit();
  if (idbUnavailable) return lsRead();
  return idbReadAll();
}

// ── Public API ─────────────────────────────────────────────────

/** Enqueue a failed mutation for later replay. */
export async function enqueue(mutation: Omit<QueuedMutation, 'id' | 'queuedAt' | 'attempts' | 'failure'>): Promise<void> {
  const item: QueuedMutation = {
    ...mutation,
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    queuedAt: new Date().toISOString(),
    attempts: 0,
  };
  await ensureInit();
  try {
    await saveQueuedItem(item);
  } catch {
    emitDropped({ reason: 'storage-failure', count: 1, labels: [item.label ?? `${item.method} ${item.url}`] });
    throw new Error('This change could not be saved on this phone. Keep your form open and try again.');
  }
  emitChange((await readQueue()).filter((item) => !item.failure).length);
}

async function saveQueuedItem(item: QueuedMutation): Promise<void> {
  if (!idbUnavailable) return idbPut(item);
  const queue = lsRead();
  const index = queue.findIndex((row) => row.id === item.id);
  if (index < 0) queue.push(item);
  else queue[index] = item;
  lsWrite(queue);
}

async function removeQueuedItem(id: string): Promise<void> {
  if (!idbUnavailable) return idbDelete([id]);
  lsWrite(lsRead().filter((item) => item.id !== id));
}

/** Original edits remain on this device when the server refuses them. */
export async function listFailedMutations(): Promise<QueuedMutation[]> {
  return (await readQueue()).filter((item) => item.failure);
}

/** Recovery files contain scouting data, never stored authorization headers. */
export async function recoveryChanges(): Promise<unknown[]> {
  return (await listFailedMutations()).map(({ id, queuedAt, method, body, label, failure }) =>
    ({ id, queuedAt, method, body, label, failure }));
}

/**
 * Number of items waiting to be replayed (last-known synchronous value;
 * kept fresh via `offlinequeue:change` events).
 */
export function size(): number {
  void ensureInit().catch(() => {});
  return knownCount;
}

/**
 * Replay all queued mutations sequentially.
 * Returns the number of successfully replayed items.
 */
async function isConflictBody(res: Response): Promise<boolean> {
  // Read the complete acknowledgement while the replay timeout is still active.
  // A connection failure during the body must keep the original edit for retry.
  const text = await res.clone().text();
  if (!text) return false;
  let body: { ok?: unknown; conflict?: unknown } | null;
  try { body = JSON.parse(text); }
  catch (error) {
    if (res.headers.get('content-type')?.includes('application/json')) throw error;
    return false;
  }
  return Boolean(body && body.ok === false && body.conflict === true);
}

let flushInFlight: Promise<number> | null = null;

// Single-flight: the online event, the retry timer and a tab coming back can
// all ask at once, and two overlapping flushes would send the same write twice.
export function flush(): Promise<number> {
  if (flushInFlight) return flushInFlight;
  flushInFlight = flushQueue().finally(() => {
    flushInFlight = null;
  });
  return flushInFlight;
}

// Writes to the same record replay in order. A pit save is a whole-entry replace, so
// a retried older edit that lands after a newer one would undo the correction.
function recordKey(item: QueuedMutation): string {
  let body: Record<string, unknown> = {};
  try { body = item.body ? (JSON.parse(item.body) as Record<string, unknown>) : {}; } catch { /* not JSON */ }
  const part = (name: string) => (typeof body[name] === 'string' ? String(body[name]).toLowerCase() : '');
  return [item.method, item.url.split('?')[0], part('event_key'), part('team_key'), part('client_entry_id')].join('|');
}

async function flushQueue(): Promise<number> {
  const queue = await readQueue();
  if (queue.length === 0) return 0;

  let successCount = 0;
  const waiting = new Set<string>();
  for (const item of queue) {
    if (item.failure) continue;
    const key = recordKey(item);
    if (waiting.has(key)) continue;
    let res: Response;
    let conflict: boolean;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REPLAY_TIMEOUT_MS);
    try {
      res = await fetch(item.url, {
        method: item.method,
        headers: item.headers,
        body: item.body ?? undefined,
        signal: controller.signal,
      });
      conflict = res.status === 409 || (res.ok && await isConflictBody(res));
    } catch {
      await saveQueuedItem({ ...item, attempts: item.attempts + 1 });
      waiting.add(key);
      continue;
    } finally {
      clearTimeout(timeout);
    }
    if (res.ok && !conflict) {
      // Delete only this confirmed item, so concurrent saves cannot be overwritten.
      await removeQueuedItem(item.id);
      successCount++;
    } else if (res.status >= 500 || res.status === 408 || res.status === 429) {
      await saveQueuedItem({ ...item, attempts: item.attempts + 1 });
      waiting.add(key);
    } else {
      const reason = conflict ? 'conflict' : 'server-rejected';
      await saveQueuedItem({ ...item, attempts: item.attempts + 1, failure: { reason, status: res.status } });
      emitDropped({ reason, count: 1, labels: [item.label ?? `${item.method} ${item.url}`] });
    }
  }

  emitChange((await readQueue()).filter((item) => !item.failure).length);
  if (successCount > 0) recordConfirmedSync();
  return successCount;
}

// ── Auto-flush on reconnect ────────────────────────────────────

let _autoFlushBound = false;

/** Start listening for the `online` event and auto-flush. Call once at boot. */
export function startAutoFlush(): void {
  if (_autoFlushBound) return;
  _autoFlushBound = true;
  void ensureInit().catch(() => {});
  window.addEventListener('online', () => {
    // Small delay to let the network stabilise.
    setTimeout(() => {
      flush().catch(() => {});
    }, 1500);
  });
  // Venue wifi usually fails while the browser still says "online", so no
  // online event ever fires; retry on a timer and when the app comes back.
  window.setInterval(() => {
    if (navigator.onLine) flush().catch(() => {});
  }, 30_000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && navigator.onLine) flush().catch(() => {});
  });
}

export const bootstrapNativeQueue = () => isNativeApp() ? ensureInit() : Promise.resolve();
