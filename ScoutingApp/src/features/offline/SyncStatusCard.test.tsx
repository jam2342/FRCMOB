import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { act, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SyncStatusCard } from './SyncStatusCard';
import { openDb, saveSession, type StoredSession } from '../onDevice/offlineStore';
import { signInTestWorkspace } from '../../test/workspace';
import { clearWorkspaceSession, setWorkspaceSession } from '../workspace/workspaceSession';

vi.mock('../../api', () => ({ syncOnDeviceSession: vi.fn() }));
vi.mock('../../utils/offlineQueue', () => ({ flush: vi.fn(async () => 0), listFailedMutations: vi.fn(async () => []), recoveryChanges: vi.fn(async () => []) }));
vi.mock('../../hooks/useOnlineStatus', () => ({ useOnlineStatus: () => ({ online: true, queueSize: 0 }) }));

const saved = (id: string, workspaceId: number): StoredSession => ({
  id, matchKey: `2026test_qm${id}`, eventKey: '2026test', createdAt: 1, synced: false, workspaceId,
  payload: { pointsByTeam: {}, schemaVersion: 'on_device_session_v2', modelVersion: 'test', calibrationVersion: 'test', calibrationRmseM: 0, calibrationVerified: true, captureSource: 'video', poseSource: 'static', poseFallbackRatio: 0, identityConfidence: 1, identitySource: 'manual', timingSource: 'video_offset', captureToMatchOffsetSec: 0, shift1ActiveAlliance: null, shift1Source: null, executionProvider: 'wasm', sampledFrameCount: 3, inferenceMedianMs: 100, inferenceP90Ms: 100, thermalDriftPct: 0 },
});

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  window.localStorage.clear();
  signInTestWorkspace();
});

async function seed(...sessions: StoredSession[]) {
  const db = await openDb();
  try { for (const session of sessions) await saveSession(db, session); }
  finally { db.close(); }
}

function row(id: string) { return within(screen.getByText(`2026test_qm${id} · 2026test`).closest('li')!); }

describe('recording sync status card', () => {
  it('shows per-match failure reasons and export controls', async () => {
    await seed({ ...saved('1', 1), syncFailure: { kind: 'rejected', status: 409, at: 1 } }, { ...saved('2', 1), syncFailure: { kind: 'connection', at: 1 } });
    render(<SyncStatusCard />);
    await screen.findByText('0 scouting changes waiting · 2 recordings waiting');
    expect(row('1').getByText(/server did not accept/)).toBeInTheDocument();
    expect(row('2').getByText(/last upload lost its connection/)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Export recording' })).toHaveLength(2);
  });

  it('updates a recording blocked by another workspace when the scout switches back', async () => {
    await seed(saved('other', 2));
    render(<SyncStatusCard />);
    await screen.findByText(/Saved for another team/);
    act(() => setWorkspaceSession({ token: 'fake-other-token', expiresAt: Date.now() / 1000 + 3600, workspace: { id: 2, name: 'Other Team', frc_team_number: 254 }, me: { id: 2, display_name: 'Scout', role: 'member' } }));
    await screen.findByText(/waiting for the server to confirm/);
    expect(screen.queryByText(/Saved for another team/)).toBeNull();
  });

  it('explains that a signed-out scout needs to join a team', async () => {
    await seed(saved('join', 1));
    clearWorkspaceSession('left');
    render(<SyncStatusCard />);
    await screen.findByText('Join your team to upload this recording.');
  });
});
