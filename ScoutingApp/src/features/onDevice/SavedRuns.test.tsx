import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { OnDeviceSessionSyncResponse, TeamHeatmapResponse } from '../../api';
import { signInTestWorkspace, signOutTestWorkspace } from '../../test/workspace';
import { SavedRuns } from './SavedRuns';
import { positionHeatmap } from './resultHeatmaps';
import { listSessions, markSessionSynced, openDb, saveSession, type StoredSession } from './offlineStore';
import { FIELD_LENGTH_M, FIELD_WIDTH_M } from './fieldZones';

vi.mock('../../components/cv/FieldHeatmap', () => ({
  FieldHeatmap: ({ data }: { data: TeamHeatmapResponse }) => <div data-testid="heatmap">{data.team_key}: {data.total_points} observations</div>,
}));

const run: StoredSession = {
  id: 'saved-test-run', matchKey: '2026test_qm1', eventKey: '2026test', createdAt: 1000,
  synced: false, workspaceId: 1, workspaceName: 'Test Team',
  payload: {
    pointsByTeam: { frc254: [
      { timeSec: 1, fieldX: 1, fieldY: 2, zoneKey: null, speedMps: null },
      { timeSec: 42, fieldX: 2, fieldY: 2, zoneKey: null, speedMps: null },
    ] },
    schemaVersion: 'on_device_session_v2', modelVersion: 'test', calibrationVersion: 'test',
    calibrationRmseM: 0, calibrationVerified: true, captureSource: 'video', poseSource: 'static',
    poseFallbackRatio: 0, identityConfidence: 1, identitySource: 'manual', timingSource: 'video_offset',
    captureToMatchOffsetSec: -8, shift1ActiveAlliance: null, shift1Source: null, executionProvider: 'webgpu',
    sampledFrameCount: 100, inferenceMedianMs: 15, inferenceP90Ms: 20, thermalDriftPct: 0,
  },
};
const analysis: OnDeviceSessionSyncResponse = {
  ok: true, match_key: '2026test_qm1', event_key: '2026test', run_id: 1, on_device_session_id: 1,
  session_key: 'test', reused_run: true, status: 'provisional', quality_score: 0.84,
  quality: { eligible_for_review: true }, shift1_active_alliance: 'red', shift1_source: 'official',
  team_count: 1, points_persisted: 2, points_by_team: { frc254: 2 }, skipped_unknown_teams: [],
  shift_play: { frc254: {
    team_key: 'frc254', alliance: 'red', offense: { level_1_5: 4, confidence_0_1: 0.8 },
    defense: { level_1_5: 1, confidence_0_1: 0, assessable: false },
    heatmaps: { attack: [[0, 2]], defense: [[0, 0]] },
  } }, shift_play_missing_reason: null,
};

async function seed(session = run) {
  const db = await openDb();
  try { await saveSession(db, session); } finally { db.close(); }
}
async function openResults() {
  fireEvent.click(await screen.findByRole('button', { name: /View results for 2026test_qm1/ }));
  return screen.findByRole('region', { name: 'Run results' });
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  signInTestWorkspace();
  window.location.hash = '#/scouting/record';
});

describe('saved run results', () => {
  it('reopens a locally saved run offline with observations and no invented estimates', async () => {
    await seed();
    render(<SavedRuns />);
    const results = await openResults();
    expect(results).toHaveTextContent('1 robot · 2 saved positions · 100 analyzed frames');
    expect(results).toHaveTextContent('observed 0:01–0:42 on the match clock; gaps may exist');
    expect(results).toHaveTextContent('Waiting to sync');
    expect(results).toHaveTextContent('does not measure fuel scored or cycle times');
    expect(screen.getAllByTestId('heatmap')).toHaveLength(1);
    expect(screen.queryByText('4/5')).toBeNull();
  });

  it('persists the server analysis and reopens it after remounting, including unassessable defense', async () => {
    await seed();
    const db = await openDb();
    await markSessionSynced(db, run.id, analysis);
    // A later acknowledgement without analysis must not erase the cached result.
    await markSessionSynced(db, run.id);
    expect((await listSessions(db))[0].syncResult).toEqual(analysis);
    db.close();
    const view = render(<SavedRuns />);
    await openResults();
    expect(screen.getByText('4/5 · 80%')).toBeInTheDocument();
    expect(screen.getByText('n/a')).toBeInTheDocument();
    expect(screen.getByText(/Awaiting operator review/)).toBeInTheDocument();
    expect(screen.getByText(/no duplicate was created/)).toBeInTheDocument();
    view.unmount();
    render(<SavedRuns />);
    await openResults();
    expect(screen.getAllByTestId('heatmap')).toHaveLength(2);
    expect(screen.getByText('No observed positions during opponent shifts.')).toBeInTheDocument();
    expect(screen.getByText('4/5 · 80%')).toBeInTheDocument();
  });

  it('refreshes an open offline result after automatic sync persists its analysis', async () => {
    await seed();
    render(<SavedRuns />);
    await openResults();
    const db = await openDb();
    await act(async () => { await markSessionSynced(db, run.id, analysis); });
    db.close();
    expect(await screen.findByText('4/5 · 80%')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Synced to your team');
  });

  it('hides bound runs immediately when the scout leaves the workspace', async () => {
    await seed({ ...run, synced: true, syncResult: analysis });
    await seed({ ...run, id: 'other', matchKey: '2026other_qm2', workspaceId: 2 });
    render(<SavedRuns />);
    await openResults();
    expect(screen.queryByText('2026other_qm2')).toBeNull();
    act(() => signOutTestWorkspace());
    expect(screen.queryByText('4/5 · 80%')).toBeNull();
    expect(screen.queryByRole('button', { name: /View results/ })).toBeNull();
  });

  it('opens a run requested by the My Team link without making a new upload', async () => {
    await seed();
    window.location.hash = '#/scouting/record?run=saved-test-run';
    render(<SavedRuns />);
    expect(await screen.findByRole('region', { name: 'Run results' })).toHaveTextContent('2 saved positions');
  });

  it('keeps heatmaps available for older synced runs whose analysis was not saved', async () => {
    await seed({ ...run, synced: true });
    render(<SavedRuns />);
    await openResults();
    expect(screen.getByText(/No saved offense or defense analysis/)).toBeInTheDocument();
    expect(screen.getByTestId('heatmap')).toHaveTextContent('frc254: 2 observations');
  });

  it('explains missing shift analysis without displaying a rating', async () => {
    await seed({ ...run, synced: true, syncResult: { ...analysis, shift_play: null, shift_play_missing_reason: 'missing_shift1_active_alliance' } });
    render(<SavedRuns />);
    await openResults();
    expect(screen.getByText(/Offense and defense unavailable: missing shift1 active alliance/)).toBeInTheDocument();
    expect(screen.queryByText('4/5 · 80%')).toBeNull();
  });

  it('bins field coordinates correctly and excludes invalid or off-field observations', () => {
    const point = run.payload.pointsByTeam.frc254[0];
    const result = positionHeatmap([
      { ...point, fieldX: 0, fieldY: 0 },
      { ...point, fieldX: FIELD_LENGTH_M, fieldY: FIELD_WIDTH_M },
      { ...point, fieldX: -1 }, { ...point, fieldY: NaN }, { ...point, fieldX: Infinity },
    ], 'frc254');
    expect(result.total_points).toBe(2);
    expect(result.grid[0][0]).toBe(1);
    expect(result.grid[15][31]).toBe(1);
  });
});
