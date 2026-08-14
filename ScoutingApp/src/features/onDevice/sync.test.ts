import 'fake-indexeddb/auto';

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

// The bridge calls the real API client; mock just the one function it uses.
vi.mock('../../api', () => ({ syncOnDeviceSession: vi.fn() }));

import { syncOnDeviceSession } from '../../api';
import { flushPendingOnDeviceSessions } from './sync';
import { listPendingSessions, openDb, saveSession, type StoredSession } from './offlineStore';

const mockSync = vi.mocked(syncOnDeviceSession);

const session = (id: string): StoredSession => ({
  id,
  eventKey: '2026test',
  matchKey: `2026test_qm${id}`,
  createdAt: 123,
  synced: false,
  payload: { points_by_team: { frc254: [] } },
});

beforeEach(async () => {
  globalThis.indexedDB = new IDBFactory(); // fresh in-memory DB per test
  mockSync.mockReset();
});

describe('on-device sync bridge', () => {
  it('posts each pending session mapped to the request shape and marks synced; failures stay pending', async () => {
    const seed = await openDb();
    await saveSession(seed, session('ok'));
    await saveSession(seed, session('boom'));
    seed.close();

    mockSync.mockImplementation(async (s) => {
      if (s.id === 'boom') throw new Error('network down');
      return {} as Awaited<ReturnType<typeof syncOnDeviceSession>>;
    });

    const result = await flushPendingOnDeviceSessions();
    expect(result).toEqual({ synced: 1, failed: 1 });
    expect(mockSync).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'ok',
        eventKey: '2026test',
        matchKey: '2026test_qmok',
        payload: { points_by_team: { frc254: [] } },
      }),
    );

    const db = await openDb();
    expect((await listPendingSessions(db)).map((s) => s.id)).toEqual(['boom']); // retried later
    db.close();
  });

  it('is a no-op with zero counts when nothing is pending', async () => {
    const result = await flushPendingOnDeviceSessions();
    expect(result).toEqual({ synced: 0, failed: 0 });
    expect(mockSync).not.toHaveBeenCalled();
  });
});
