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
  try {
    const rows = await transaction<Row[]>('readonly', store => store.getAll());
    const live = rows.filter(row => row.retainUntil > Date.now()).sort((a,b) => a.storedAt-b.storedAt);
    const remove = [...rows.filter(row => row.retainUntil <= Date.now()), ...live.slice(0, Math.max(0,live.length-maxEntries))];
    for (const row of remove) await transaction('readwrite', store => store.delete(row.key));
  } catch { /* Offline readiness verifies persisted keys rather than assuming success. */ }
}
