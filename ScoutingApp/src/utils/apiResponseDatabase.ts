// WKWebView's capacitor: origin has IndexedDB but may have no Cache Storage.
// Keep response bodies out of the small localStorage quota.
type Row = { key: string; body: string; status: number; statusText: string; headers: [string, string][]; storedAt: number; retainUntil: number };
const DB = 'frcmob-api-responses';
async function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('responses', { keyPath: 'key' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error('API cache upgrade blocked'));
  });
}
async function transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction('responses', mode); const request = action(tx.objectStore('responses'));
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = tx.onabort = () => reject(tx.error || request.error || new Error('API cache transaction failed'));
    });
  } finally { db.close(); }
}
export async function putDatabaseResponse(row: Row): Promise<boolean> {
  try { await transaction('readwrite', store => store.put(row)); return true; } catch { return false; }
}
export async function getDatabaseResponse(key: string): Promise<Row | null> {
  try {
    const row = await transaction<Row | undefined>('readonly', store => store.get(key));
    if (!row) return null;
    if (row.retainUntil <= Date.now()) { await transaction('readwrite', store => store.delete(key)); return null; }
    return row;
  } catch { return null; }
}
export async function pruneDatabaseResponses(maxEntries: number): Promise<void> {
  let db: IDBDatabase | undefined;
  try {
    db = await open();
    await new Promise<void>((resolve, reject) => {
      const tx = db!.transaction('responses', 'readwrite');
      const store = tx.objectStore('responses');
      const request = store.openCursor();
      const live: { key: string; storedAt: number }[] = [];
      const now = Date.now();
      request.onsuccess = () => {
        const cursor = request.result;
        if (cursor) {
          const row = cursor.value as Row;
          if (row.retainUntil <= now) cursor.delete();
          else live.push({ key: row.key, storedAt: row.storedAt });
          cursor.continue();
        } else {
          live.sort((a, b) => a.storedAt - b.storedAt);
          for (const row of live.slice(0, Math.max(0, live.length - maxEntries))) store.delete(row.key);
        }
      };
      tx.oncomplete = () => resolve();
      tx.onerror = tx.onabort = () => reject(tx.error || request.error || new Error('API cache pruning failed'));
    });
  } catch { /* Offline readiness verifies persisted keys rather than assuming success. */ }
  finally { db?.close(); }
}
