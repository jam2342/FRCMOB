import { describe, expect, it } from 'vitest';
import { classifyRecordingSyncFailure, recordingRecovery, recordingSyncState } from './recordingSyncStatus';
import type { StoredSession } from './offlineStore';

const recording: StoredSession = {
  id: 'saved', eventKey: '2026test', matchKey: '2026test_qm1', createdAt: 1, workspaceId: 1, synced: false,
  payload: { pointsByTeam: { frc254: [] }, schemaVersion: 'on_device_session_v2', modelVersion: 'test', calibrationVersion: 'test', calibrationRmseM: 0, calibrationVerified: true, captureSource: 'video', poseSource: 'static', poseFallbackRatio: 0, identityConfidence: 1, identitySource: 'manual', timingSource: 'video_offset', captureToMatchOffsetSec: 0, shift1ActiveAlliance: null, shift1Source: null, executionProvider: 'wasm', sampledFrameCount: 3, inferenceMedianMs: 100, inferenceP90Ms: 100, thermalDriftPct: 0 },
};

describe('recording sync status', () => {
  it.each([[401, 'access'], [403, 'access'], [409, 'rejected'], [422, 'rejected'], [408, 'server'], [429, 'server'], [503, 'server']])('classifies HTTP %s without retaining server messages', (status, kind) => {
    const result = classifyRecordingSyncFailure(Object.assign(new Error('Private server detail'), { status }));
    expect(result).toMatchObject({ kind, status });
    expect(JSON.stringify(result)).not.toContain('Private server detail');
  });

  it('distinguishes a connection loss from workspace and offline waits', () => {
    const saved = { ...recording, syncFailure: classifyRecordingSyncFailure(new TypeError('Failed to fetch')) };
    expect(recordingSyncState(saved, 1, true)).toBe('connection');
    expect(recordingSyncState(saved, 1, false)).toBe('offline');
    expect(recordingSyncState(saved, 2, true)).toBe('other-team');
    expect(recordingSyncState(saved, null, true)).toBe('join-team');
  });

  it('keeps rejection and expired access actionable while disconnected', () => {
    expect(recordingSyncState({ ...recording, syncFailure: classifyRecordingSyncFailure({ status: 409 }) }, 1, false)).toBe('rejected');
    expect(recordingSyncState({ ...recording, syncFailure: classifyRecordingSyncFailure({ status: 401 }) }, null, true)).toBe('access');
  });

  it('exports only recording data even if a legacy record contains authorization fields', () => {
    const exported = JSON.stringify(recordingRecovery({ ...recording, token: 'fake-private-token', headers: { authorization: 'fake-private-header' } } as StoredSession));
    expect(exported).toContain('pointsByTeam');
    expect(exported).not.toContain('fake-private');
  });
});
