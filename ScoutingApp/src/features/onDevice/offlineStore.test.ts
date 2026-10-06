import 'fake-indexeddb/auto';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
  payload: {
    pointsByTeam: {},
    schemaVersion: 'on_device_session_v2',
    modelVersion: 'test-model',
    calibrationVersion: 'manual_corners_v1',
    calibrationRmseM: 0,
    calibrationVerified: true,
    captureSource: 'video',
    poseSource: 'static',
    poseFallbackRatio: 0,
    identityConfidence: 1,
    identitySource: 'manual',
    timingSource: 'video_offset',
    captureToMatchOffsetSec: -8,
    shift1ActiveAlliance: 'red',
    shift1Source: 'manual_scout_selection',
    executionProvider: 'wasm',
    sampledFrameCount: 3,
    inferenceMedianMs: 100,
    inferenceP90Ms: 120,
    thermalDriftPct: 2,
  },
});

describe('offline store', () => {
  it('round-trips calibration frame dimensions and pixels', async () => {
    await saveCalibration(db, {
      id: 'current',
      homography: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
      createdAt: 1,
      calibrationVersion: 'manual_corners_v1',
      rmseM: 0,
      verified: true,
      imageWidth: 2,
      imageHeight: 1,
      referenceGray: new Float32Array([10, 20]),
    });
    const got = await getCalibration(db, 'current');
    expect(got?.homography[0][0]).toBe(1);
    expect(got?.verified).toBe(true);
    expect(got?.imageWidth).toBe(2);
    expect(Array.from(got?.referenceGray ?? [])).toEqual([10, 20]);
    expect(await getCalibration(db, 'missing')).toBeUndefined();
  });

  it('lists only unsynced sessions as pending', async () => {
    await saveSession(db, session('1'));
    await saveSession(db, session('2', true));
    const pending = await listPendingSessions(db);
    expect(pending.map((s) => s.id)).toEqual(['1']);
  });

  it('notifies listeners once for a sync batch, including failed recordings', async () => {
    await saveSession(db, session('ok'));
    await saveSession(db, session('boom'));
    const changed = vi.fn();
    window.addEventListener('frcmob:session-change', changed);
    try {
      await syncPendingSessions(db, async (saved) => {
        expect(changed).not.toHaveBeenCalled();
        if (saved.id === 'boom') throw new Error('offline');
      });
      expect(changed).toHaveBeenCalledTimes(1);
    } finally { window.removeEventListener('frcmob:session-change', changed); }
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
