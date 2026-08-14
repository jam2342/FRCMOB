import 'fake-indexeddb/auto';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

import {
  type StoredSession,
  getCalibration,
  listPendingSessions,
  openDb,
  saveCalibration,
  saveSession,
  syncPendingSessions,
} from './offlineStore';

let db: Awaited<ReturnType<typeof openDb>>;

beforeEach(async () => {
  // fresh in-memory IndexedDB per test
  globalThis.indexedDB = new IDBFactory();
  db = await openDb();
});

afterEach(() => db.close());

const session = (id: string, synced = false): StoredSession => ({
  id,
  eventKey: '2026test',
  matchKey: `2026test_qm${id}`,
  createdAt: Date.now(),
  synced,
  payload: { points: 1 },
});

describe('offline store', () => {
  it('round-trips a calibration', async () => {
    await saveCalibration(db, { id: 'current', homography: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], createdAt: 1 });
    const got = await getCalibration(db, 'current');
    expect(got?.homography[0][0]).toBe(1);
    expect(await getCalibration(db, 'missing')).toBeUndefined();
  });

  it('lists only unsynced sessions as pending', async () => {
    await saveSession(db, session('1'));
    await saveSession(db, session('2', true));
    const pending = await listPendingSessions(db);
    expect(pending.map((s) => s.id)).toEqual(['1']);
  });

  it('syncPendingSessions posts pending, marks them synced, counts failures', async () => {
    await saveSession(db, session('ok'));
    await saveSession(db, session('boom'));
    const result = await syncPendingSessions(db, async (s) => {
      if (s.id === 'boom') throw new Error('offline');
    });
    expect(result).toEqual({ synced: 1, failed: 1 });
    // the failed one stays pending for a later retry
    expect((await listPendingSessions(db)).map((s) => s.id)).toEqual(['boom']);
  });
});
