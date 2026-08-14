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
 * Nothing is ever dropped silently — any forced drop dispatches an
 * `offlinequeue:dropped` event so the UI can warn the user.
 */

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
}

export interface DroppedMutationInfo {
  /** Why the item left the queue without a successful replay. */
  reason: 'storage-failure' | 'server-rejected';
  count: number;
  labels: string[];
}

// ── Storage keys / constants ───────────────────────────────────

const LEGACY_STORAGE_KEY = 'frcmob_offline_queue_v1';
const IDB_NAME = 'frcmob_offline_queue';
const IDB_VERSION = 1;
const IDB_STORE = 'mutations';

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

async function idbReadAll(): Promise<QueuedMutation[]> {
  const db = await openDb();
  try {
    const tx = db.transaction(IDB_STORE, 'readonly');
    const rows = await idbRequest(tx.objectStore(IDB_STORE).getAll());
    const items = (Array.isArray(rows) ? rows : []) as QueuedMutation[];
    return items.sort((a, b) => (a.queuedAt < b.queuedAt ? -1 : 1));
  } finally {
    db.close();
  }
}

async function idbPut(item: QueuedMutation): Promise<void> {
  const db = await openDb();
  try {
    const tx = db.transaction(IDB_STORE, 'readwrite');
    await idbRequest(tx.objectStore(IDB_STORE).put(item));
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
    await Promise.all(ids.map((id) => idbRequest(store.delete(id))));
  } finally {
    db.close();
  }
}

async function idbCount(): Promise<number> {
  const db = await openDb();
  try {
    const tx = db.transaction(IDB_STORE, 'readonly');
    return await idbRequest(tx.objectStore(IDB_STORE).count());
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
  try {
    window.localStorage.setItem(LEGACY_STORAGE_KEY, JSON.stringify(queue));
  } catch {
    // Quota exceeded. Shrink, but tell the user exactly what was lost —
    // silently discarding scouting data is the worst possible failure here.
    const keep = queue.slice(-20);
    const dropped = queue.slice(0, Math.max(0, queue.length - 20));
    try {
      window.localStorage.setItem(LEGACY_STORAGE_KEY, JSON.stringify(keep));
    } catch {
      // Nothing fits at all.
      dropped.push(...keep);
    }
    if (dropped.length > 0) {
      emitDropped({
        reason: 'storage-failure',
        count: dropped.length,
        labels: dropped.map((d) => d.label ?? `${d.method} ${d.url}`),
      });
    }
  }
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
    try {
      const legacy = lsRead();
      if (legacy.length > 0) {
        for (const item of legacy) {
          await idbPut(item);
        }
        lsClear();
      }
      emitChange(await idbCount());
    } catch {
      idbUnavailable = true;
      emitChange(lsRead().length);
    }
  })();
  return initPromise;
}

async function readQueue(): Promise<QueuedMutation[]> {
  await ensureInit();
  if (idbUnavailable) return lsRead();
  try {
    return await idbReadAll();
  } catch {
    idbUnavailable = true;
    return lsRead();
  }
}

// ── Public API ─────────────────────────────────────────────────

/** Enqueue a failed mutation for later replay. */
export function enqueue(mutation: Omit<QueuedMutation, 'id' | 'queuedAt' | 'attempts'>): void {
  const item: QueuedMutation = {
    ...mutation,
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    queuedAt: new Date().toISOString(),
    attempts: 0,
  };
  // Optimistically bump the visible count right away.
  emitChange(knownCount + 1);
  void ensureInit().then(async () => {
    if (idbUnavailable) {
      const queue = lsRead();
      queue.push(item);
      lsWrite(queue);
      emitChange(lsRead().length);
      return;
    }
    try {
      await idbPut(item);
      emitChange(await idbCount());
    } catch {
      // IndexedDB write failed mid-session — fall back to localStorage.
      idbUnavailable = true;
      const queue = lsRead();
      queue.push(item);
      lsWrite(queue);
      emitChange(lsRead().length);
    }
  });
}

/**
 * Number of items waiting to be replayed (last-known synchronous value;
 * kept fresh via `offlinequeue:change` events).
 */
export function size(): number {
  void ensureInit();
  return knownCount;
}

/** Read the full queue (for diagnostics / settings UI). */
export async function listQueued(): Promise<QueuedMutation[]> {
  return readQueue();
}

/**
 * Replay all queued mutations sequentially.
 * Returns the number of successfully replayed items.
 */
export async function flush(): Promise<number> {
  const queue = await readQueue();
  if (queue.length === 0) return 0;

  let successCount = 0;
  const doneIds: string[] = [];
  const rejected: QueuedMutation[] = [];
  const remaining: QueuedMutation[] = [];

  for (const item of queue) {
    try {
      const res = await fetch(item.url, {
        method: item.method,
        headers: item.headers,
        body: item.body ?? undefined,
      });
      if (res.ok || res.status === 409 /* already exists / idempotent */) {
        successCount += 1;
        doneIds.push(item.id);
      } else if (res.status >= 500) {
        // Server error — keep for retry.
        remaining.push({ ...item, attempts: item.attempts + 1 });
      } else {
        // 4xx — the server will never accept this (bad request, auth
        // expired). Remove it, but tell the user what was discarded.
        doneIds.push(item.id);
        rejected.push(item);
      }
    } catch {
      // Still offline — keep in queue.
      remaining.push({ ...item, attempts: item.attempts + 1 });
    }
  }

  if (idbUnavailable) {
    lsWrite(remaining);
  } else {
    try {
      await idbDelete(doneIds);
      for (const item of remaining) {
        await idbPut(item);
      }
    } catch {
      idbUnavailable = true;
      lsWrite(remaining);
    }
  }

  if (rejected.length > 0) {
    emitDropped({
      reason: 'server-rejected',
      count: rejected.length,
      labels: rejected.map((d) => d.label ?? `${d.method} ${d.url}`),
    });
  }

  emitChange(remaining.length);
  return successCount;
}

// ── Auto-flush on reconnect ────────────────────────────────────

let _autoFlushBound = false;

/** Start listening for the `online` event and auto-flush. Call once at boot. */
export function startAutoFlush(): void {
  if (_autoFlushBound) return;
  _autoFlushBound = true;
  void ensureInit();
  window.addEventListener('online', () => {
    // Small delay to let the network stabilise.
    setTimeout(() => {
      flush().catch(() => {});
    }, 1500);
  });
}
