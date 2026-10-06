import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory, IDBDatabase } from 'fake-indexeddb';
import { getDatabaseResponse, putDatabaseResponse, pruneDatabaseResponses } from './apiResponseDatabase';
const row = (key: string, body = 'x'.repeat(600_000)) => ({key, body, status:200, statusText:'OK', headers:[['Content-Type','application/json']] as [string,string][], storedAt:Date.now(), retainUntil:Date.now()+60_000});
describe('native API response database', () => {
 beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()));
 afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
 it('persists large responses and keeps workspace keys separate', async () => {
  expect(await putDatabaseResponse(row('GET:w1:/teams'))).toBe(true);
  expect((await getDatabaseResponse('GET:w1:/teams'))?.body).toHaveLength(600_000);
  expect(await getDatabaseResponse('GET:w2:/teams')).toBeNull();
 });
 it('expires saved responses and bounds the retained entries', async () => {
  await putDatabaseResponse({...row('expired','small'),retainUntil:Date.now()-1});
  expect(await getDatabaseResponse('expired')).toBeNull();
  await putDatabaseResponse({...row('older','small'),storedAt:1});await putDatabaseResponse(row('newer','small'));
  await pruneDatabaseResponses(1);expect(await getDatabaseResponse('older')).toBeNull();expect(await getDatabaseResponse('newer')).not.toBeNull();
 });
 it('reports failed persistence when the database is unavailable', async () => {
  vi.stubGlobal('indexedDB',undefined);expect(await putDatabaseResponse(row('blocked'))).toBe(false);expect(await getDatabaseResponse('blocked')).toBeNull();
 });
  it('removes expired and oldest responses with one database open and transaction', async () => {
    const now = Date.now();
    for (const [key, storedAt, retainUntil] of [['expired', 1, now - 1], ['oldest', 2, now + 60_000], ['latest', 3, now + 60_000]] as const) {
      expect(await putDatabaseResponse({ key, storedAt, retainUntil, body: key, status: 200, statusText: 'OK', headers: [] })).toBe(true);
    }
    const open = vi.spyOn(indexedDB, 'open');
    const transaction = vi.spyOn(IDBDatabase.prototype, 'transaction');
    await pruneDatabaseResponses(1);
    expect(open).toHaveBeenCalledTimes(1);
    expect(transaction).toHaveBeenCalledTimes(1);
    expect(await getDatabaseResponse('expired')).toBeNull();
    expect(await getDatabaseResponse('oldest')).toBeNull();
    expect((await getDatabaseResponse('latest'))?.body).toBe('latest');
  });
});
