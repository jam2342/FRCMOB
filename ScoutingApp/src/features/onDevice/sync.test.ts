import 'fake-indexeddb/auto';

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

// The bridge calls the real API client; mock just the one function it uses.
vi.mock('../../api', () => ({ syncOnDeviceSession: vi.fn() }));

import { syncOnDeviceSession } from '../../api';
import { flushPendingOnDeviceSessions } from './sync';
import { listPendingSessions, listSessions, openDb, saveSession, type StoredSession } from './offlineStore';
import { signInTestWorkspace } from '../../test/workspace';
import { setWorkspaceSession } from '../workspace/workspaceSession';

const mockSync = vi.mocked(syncOnDeviceSession);

const session = (id: string): StoredSession => ({
  id,
  eventKey: '2026test',
  matchKey: `2026test_qm${id}`,
  createdAt: 123,
  synced: false,
  payload: {
    pointsByTeam: { frc254: [] },
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

beforeEach(async () => {
  globalThis.indexedDB = new IDBFactory(); // fresh in-memory DB per test
  mockSync.mockReset();
  signInTestWorkspace();
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
        payload: expect.objectContaining({
          pointsByTeam: { frc254: [] },
          schemaVersion: 'on_device_session_v2',
          calibrationVerified: true,
          shift1ActiveAlliance: 'red',
        }),
      }),
      'test-workspace-token',
    );

    const db = await openDb();
    expect((await listPendingSessions(db)).map((s) => s.id)).toEqual(['boom']); // retried later
    expect((await listSessions(db)).find((s) => s.id === 'ok')?.syncResult).toEqual({});
    db.close();
  });

  it("never uploads one team's run as another team's", async () => {
    // signInTestWorkspace puts this device in workspace 1.
    mockSync.mockResolvedValue({} as never);
    const db = await openDb();
    await saveSession(db, { ...session('1'), workspaceId: 1 });
    await saveSession(db, { ...session('2'), workspaceId: 2 });
    await saveSession(db, { ...session('3'), workspaceId: null });
    db.close();

    const result = await flushPendingOnDeviceSessions();

    expect(result).toEqual({ synced: 2, failed: 0 });
    expect(mockSync.mock.calls.map(([body]) => body.id).sort()).toEqual(['1', '3']);
    const reopened = await openDb();
    expect((await listPendingSessions(reopened)).map((s) => s.id)).toEqual(['2']);
    reopened.close();
  });

  it('stops a two-session flush when the active workspace changes', async () => {
    const db = await openDb();
    await saveSession(db, { ...session('first'), workspaceId: 1 });
    await saveSession(db, { ...session('second'), workspaceId: 1 });
    db.close();
    mockSync.mockImplementation(async () => {
      setWorkspaceSession({
        token: 'workspace-two-token',
        expiresAt: Math.floor(Date.now() / 1000) + 3600,
        workspace: { id: 2, name: 'Other Team', frc_team_number: 254 },
        me: { id: 2, display_name: 'Other Scout', role: 'member' },
      });
      return {} as never;
    });

    const result = await flushPendingOnDeviceSessions();

    expect(result).toEqual({ synced: 1, failed: 0 });
    expect(mockSync).toHaveBeenCalledTimes(1);
    expect(mockSync).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'first', workspaceId: 1 }),
      'test-workspace-token',
    );
    const reopened = await openDb();
    expect((await listPendingSessions(reopened)).map((s) => s.id)).toEqual(['second']);
    reopened.close();
  });

  it('is a no-op with zero counts when nothing is pending', async () => {
    const result = await flushPendingOnDeviceSessions();
    expect(result).toEqual({ synced: 0, failed: 0 });
    expect(mockSync).not.toHaveBeenCalled();
  });
  it('shares concurrent flushes so each recording is posted once', async () => {
    const db = await openDb();
    await saveSession(db, session('one'));
    db.close();
    let resolve!: () => void;
    mockSync.mockImplementation(() => new Promise((done) => { resolve = () => done({} as never); }));
    const first = flushPendingOnDeviceSessions();
    const second = flushPendingOnDeviceSessions();
    expect(second).toBe(first);
    await vi.waitFor(() => expect(resolve).toBeDefined());
    resolve();
    expect(await first).toEqual({ synced: 1, failed: 0 });
    expect(mockSync).toHaveBeenCalledTimes(1);
  });

  it('persists a rejection after reopening and retries it only on request', async () => {
    const db = await openDb();
    await saveSession(db, { ...session('rejected'), workspaceId: 1 });
    db.close();
    mockSync.mockRejectedValue(Object.assign(new Error('Unusable session'), { status: 409 }));
    expect(await flushPendingOnDeviceSessions()).toEqual({ synced: 0, failed: 1 });
    const reopened = await openDb();
    expect((await listPendingSessions(reopened))[0].syncFailure).toMatchObject({ kind: 'rejected', status: 409 });
    reopened.close();
    expect(await flushPendingOnDeviceSessions()).toEqual({ synced: 0, failed: 0 });
    expect(mockSync).toHaveBeenCalledTimes(1);
    mockSync.mockResolvedValue({} as never);
    expect(await flushPendingOnDeviceSessions({ retryRejected: true })).toEqual({ synced: 1, failed: 0 });
    const confirmed = await openDb();
    expect(await listPendingSessions(confirmed)).toEqual([]);
    confirmed.close();
  });

  it('pins a recording made before joining a team even when the upload loses its acknowledgement', async () => {
    const db = await openDb();
    await saveSession(db, session('unassigned'));
    db.close();
    mockSync.mockRejectedValue(new Error('Connection dropped'));
    expect(await flushPendingOnDeviceSessions()).toEqual({ synced: 0, failed: 1 });
    const reopened = await openDb();
    const pending = (await listPendingSessions(reopened))[0];
    expect(pending.workspaceId).toBe(1);
    expect(pending.syncFailure?.kind).toBe('connection');
    reopened.close();
    setWorkspaceSession({ token: 'other-token', expiresAt: Date.now() / 1000 + 3600, workspace: { id: 2, name: 'Other Team', frc_team_number: 254 }, me: { id: 2, display_name: 'Scout', role: 'member' } });
    expect(await flushPendingOnDeviceSessions()).toEqual({ synced: 0, failed: 0 });
    expect(mockSync).toHaveBeenCalledTimes(1);
    signInTestWorkspace();
    mockSync.mockResolvedValue({} as never);
    expect(await flushPendingOnDeviceSessions()).toEqual({ synced: 1, failed: 0 });
  });

});
