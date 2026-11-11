// Robot photos taken without signal wait here, on this phone, until they can upload.
// Notes queue through utils/offlineQueue; photos are megabytes, so they get their own
// IndexedDB store with a cap, and a store that can't open degrades to "online only".

export const PIT_PHOTO_QUEUE_MAX_ITEMS = 40;
export const PIT_PHOTO_QUEUE_MAX_BYTES = 60 * 1024 * 1024;
const BACKOFF_BASE_MS = 5_000;
const BACKOFF_MAX_MS = 5 * 60_000;
const IDB_NAME = 'frcmob_pit_photos';
const IDB_STORE = 'photos';

export type QueuedPitPhoto = {
  id: string;
  workspaceId: number;
  eventKey: string;
  teamKey: string;
  scoutProfile: string;
  createdAt: number;
  // ArrayBuffer rather than Blob: older iOS Safari mishandled Blobs inside IndexedDB.
  data: ArrayBuffer;
  type: string;
  size: number;
  attempts: number;
  nextAttemptAt: number;
  lastError?: string;
  // The server answered and refused it (photo limit, storage full, bad image). Retrying on
  // a timer can't fix that, so it waits for the scout: try again, or delete it.
  refused?: boolean;
};

export interface PitPhotoStore {
  getAll(): Promise<QueuedPitPhoto[]>;
  put(item: QueuedPitPhoto): Promise<void>;
  remove(id: string): Promise<void>;
}

export type EnqueueResult =
  | { ok: true; item: QueuedPitPhoto }
  | { ok: false; reason: 'full' | 'unavailable'; message: string };

export const PIT_PHOTO_FULL_MESSAGE =
  'This phone is holding as many photos as it can (40). Get signal so they upload, or delete some, before taking more.';
export const PIT_PHOTO_UNAVAILABLE_MESSAGE =
  "This phone can't keep photos while offline (private window or blocked storage), so this one wasn't saved. Photos need signal; take it again once you're back online.";

/* ---- Stores -------------------------------------------------------- */

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error('IndexedDB request failed'));
  });
}

export function createIdbPitPhotoStore(factory?: IDBFactory): PitPhotoStore {
  let opened: Promise<IDBDatabase> | null = null;
  const open = () => {
    opened ??= new Promise<IDBDatabase>((resolve, reject) => {
      const idb = factory ?? (typeof indexedDB === 'undefined' ? null : indexedDB);
      if (!idb) { reject(new Error('IndexedDB is unavailable')); return; }
      let req: IDBOpenDBRequest;
      try { req = idb.open(IDB_NAME, 1); } catch (err) { reject(err); return; }
      req.onupgradeneeded = () => { req.result.createObjectStore(IDB_STORE, { keyPath: 'id' }); };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error ?? new Error('IndexedDB could not open'));
      req.onblocked = () => reject(new Error('IndexedDB is blocked'));
    });
    // A failed open is retried next time instead of failing for the rest of the session.
    opened.catch(() => { opened = null; });
    return opened;
  };
  const store = async (mode: IDBTransactionMode) => (await open()).transaction(IDB_STORE, mode).objectStore(IDB_STORE);
  return {
    async getAll() { return request((await store('readonly')).getAll() as IDBRequest<QueuedPitPhoto[]>); },
    async put(item) { await request((await store('readwrite')).put(item)); },
    async remove(id) { await request((await store('readwrite')).delete(id)); },
  };
}

export function createMemoryPitPhotoStore(): PitPhotoStore {
  const rows = new Map<string, QueuedPitPhoto>();
  return {
    async getAll() { return [...rows.values()].map((row) => ({ ...row })); },
    async put(item) { rows.set(item.id, { ...item }); },
    async remove(id) { rows.delete(id); },
  };
}

let activeStore: PitPhotoStore = createIdbPitPhotoStore();
export function setPitPhotoStore(next: PitPhotoStore): void { activeStore = next; }

/* ---- Events -------------------------------------------------------- */

export type PitPhotoUploadedDetail = { item: QueuedPitPhoto; entry: unknown };

function emit(name: 'pitphotos:change' | 'pitphotos:uploaded', detail?: unknown) {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(name, { detail }));
}

/* ---- Queue --------------------------------------------------------- */

const byAge = (a: QueuedPitPhoto, b: QueuedPitPhoto) => a.createdAt - b.createdAt || a.id.localeCompare(b.id);

// An unreadable store reads as empty, so a blocked database never breaks a page that only lists.
export async function listQueuedPitPhotos(): Promise<QueuedPitPhoto[]> {
  try { return (await activeStore.getAll()).sort(byAge); } catch { return []; }
}

// Unlike the list above, an unreadable store throws: a caller deciding whether this
// phone still holds photos must not read "couldn't look" as "none".
export async function countQueuedPitPhotos(): Promise<number> {
  return (await activeStore.getAll()).length;
}

export function pitPhotoBackoffMs(attempts: number): number {
  return Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** Math.max(0, attempts - 1));
}

function newId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export async function enqueuePitPhoto(
  photo: { blob: Blob; workspaceId: number; eventKey: string; teamKey: string; scoutProfile?: string },
  now: number = Date.now(),
): Promise<EnqueueResult> {
  let existing: QueuedPitPhoto[];
  let data: ArrayBuffer;
  try {
    existing = await activeStore.getAll();
    data = await photo.blob.arrayBuffer();
  } catch {
    return { ok: false, reason: 'unavailable', message: PIT_PHOTO_UNAVAILABLE_MESSAGE };
  }
  const bytes = existing.reduce((sum, row) => sum + row.size, 0);
  if (existing.length >= PIT_PHOTO_QUEUE_MAX_ITEMS || bytes + data.byteLength > PIT_PHOTO_QUEUE_MAX_BYTES) {
    return { ok: false, reason: 'full', message: PIT_PHOTO_FULL_MESSAGE };
  }
  const item: QueuedPitPhoto = {
    id: newId(),
    workspaceId: photo.workspaceId,
    eventKey: photo.eventKey,
    teamKey: photo.teamKey,
    scoutProfile: photo.scoutProfile ?? '',
    createdAt: now,
    data,
    type: photo.blob.type || 'image/jpeg',
    size: data.byteLength,
    attempts: 0,
    nextAttemptAt: now,
  };
  try {
    await activeStore.put(item);
  } catch {
    // Storage full or blocked at write time: same outcome as not having a queue.
    return { ok: false, reason: 'unavailable', message: PIT_PHOTO_UNAVAILABLE_MESSAGE };
  }
  emit('pitphotos:change');
  return { ok: true, item };
}

export async function deleteQueuedPitPhoto(id: string): Promise<boolean> {
  try {
    await activeStore.remove(id);
  } catch {
    return false;
  }
  emit('pitphotos:change');
  return true;
}

// A refusal names the photo, not the connection: 4xx except timeout and rate limit.
export function isPitPhotoRefusal(err: unknown): boolean {
  const status = (err as { status?: unknown } | null)?.status;
  return typeof status === 'number' && status >= 400 && status < 500 && status !== 408 && status !== 429;
}

// Hands a refused photo back to the queue and uploads it now.
export async function retryRefusedPitPhoto(id: string, now: number = Date.now()): Promise<boolean> {
  try {
    const row = (await activeStore.getAll()).find((item) => item.id === id);
    if (!row) return false;
    await activeStore.put({ ...row, refused: false, nextAttemptAt: now });
  } catch {
    return false;
  }
  emit('pitphotos:change');
  syncNow?.(true);
  return true;
}

export type PitPhotoUploader = (item: QueuedPitPhoto) => Promise<unknown>;

let flushing: Promise<{ uploaded: number; remaining: number }> | null = null;

// Oldest first, one at a time. A dead connection stops the run, so it costs one attempt,
// not forty. A photo the server refused is set aside and the run goes on: one bad photo
// must never hold up every photo behind it.
export function flushPitPhotos(
  upload: PitPhotoUploader,
  options: { workspaceId: number | null; force?: boolean; now?: () => number },
): Promise<{ uploaded: number; remaining: number }> {
  if (flushing) return flushing;
  flushing = run(upload, options).finally(() => { flushing = null; });
  return flushing;
}

async function run(
  upload: PitPhotoUploader,
  options: { workspaceId: number | null; force?: boolean; now?: () => number },
): Promise<{ uploaded: number; remaining: number }> {
  const clock = options.now ?? Date.now;
  let uploaded = 0;
  // A photo taken for one team is never sent under another's login.
  const rows = (await listQueuedPitPhotos()).filter((row) => row.workspaceId === options.workspaceId);
  for (const row of rows) {
    if (row.refused) continue;
    if (!options.force && row.nextAttemptAt > clock()) continue;
    try {
      const entry = await upload(row);
      await deleteQueuedPitPhoto(row.id);
      uploaded += 1;
      emit('pitphotos:uploaded', { item: row, entry } satisfies PitPhotoUploadedDetail);
    } catch (err) {
      const attempts = row.attempts + 1;
      const refused = isPitPhotoRefusal(err);
      try {
        await activeStore.put({
          ...row,
          attempts,
          nextAttemptAt: clock() + pitPhotoBackoffMs(attempts),
          lastError: (err as Error)?.message || 'Upload failed',
          refused,
        });
        emit('pitphotos:change');
      } catch {
        // The photo stays as it was; the next run retries it.
      }
      if (!refused) break;
    }
  }
  return { uploaded, remaining: rows.length - uploaded };
}

let syncBound = false;
let syncNow: ((force?: boolean) => void) | null = null;

// Same triggers as the note queue: the online event, a timer (venue wifi often fails while
// the browser still says online), the app coming back to the front, and app start.
export function startPitPhotoSync(upload: PitPhotoUploader, currentWorkspaceId: () => number | null): void {
  if (syncBound || typeof window === 'undefined') return;
  syncBound = true;
  const flush = (force = false) => {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return;
    flushPitPhotos(upload, { workspaceId: currentWorkspaceId(), force }).catch(() => {});
  };
  syncNow = flush;
  window.addEventListener('online', () => { window.setTimeout(() => flush(true), 1500); });
  window.setInterval(() => flush(), 30_000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') flush();
  });
  flush();
}
