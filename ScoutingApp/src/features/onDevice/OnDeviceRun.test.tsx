import 'fake-indexeddb/auto';

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory, IDBObjectStore } from 'fake-indexeddb';

import { getEventSchedule, syncOnDeviceSession } from '../../api';
import { OnDeviceRun } from './OnDeviceRun';
import { loadOpenCv } from './opticalFlow';
import { signInTestWorkspace } from '../../test/workspace';
import { listSessions, openDb } from './offlineStore';

vi.mock('../../api', () => ({
  getEventSchedule: vi.fn(),
  syncOnDeviceSession: vi.fn(),
}));

vi.mock('../../components/cv/FieldHeatmap', () => ({ FieldHeatmap: () => null }));
vi.mock('./FieldCalibration', () => ({
  FieldCalibration: ({ onCalibrated }: { onCalibrated?: (value: { homography: number[][] }) => void }) => (
    <button
      type="button"
      onClick={() => onCalibrated?.({ homography: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] })}
    >
      Complete calibration
    </button>
  ),
}));
vi.mock('./MatchRecorder', () => ({
  MatchRecorder: ({
    onFrame,
    onTelemetry,
  }: {
    onFrame: (frame: {
      timeSec: number;
      detections: Array<{ bbox: [number, number, number, number]; confidence: number }>;
      homography: number[][];
    }) => void;
    onTelemetry?: (telemetry: Record<string, unknown>) => void;
  }) => (
    <button
      type="button"
      onClick={() => {
        emittedFrame += 1;
        onFrame({
          timeSec: emittedFrame,
          detections: [{ bbox: [1, 1, 2, 2], confidence: 0.9 }],
          homography: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        });
        onTelemetry?.({
          iterations: emittedFrame,
          msMedian: 15,
          msP90: 18,
          msMax: 18,
          fps: 66.7,
          thermalDriftPct: 2,
          executionProvider: 'webgpu',
          modelVersion: 'test-yolo-v2',
        });
      }}
    >
      Emit captured frame
    </button>
  ),
}));
vi.mock('./VideoFileProcessor', () => ({ VideoFileProcessor: () => null }));
vi.mock('./opticalFlow', () => ({
  createCvPoseResolver: () => ({ resolve: () => [[1, 0, 0], [0, 1, 0], [0, 0, 1]], dispose: vi.fn() }),
  createLocalPoseResolver: () => ({ resolve: () => [[1, 0, 0], [0, 1, 0], [0, 0, 1]], dispose: vi.fn() }),
  loadOpenCv: vi.fn(async () => ({})),
  // These cases assert the OpenCV load behaviour, so they must exercise that path.
  USE_LOCAL_OPTICAL_FLOW: false,
}));
vi.mock('./sync', () => ({ flushPendingOnDeviceSessions: vi.fn(async () => ({ synced: 0, failed: 0 })) }));

const matches = [
  {
    match_key: '2026test_qm1',
    red: [{ team_key: 'frc1' }],
    blue: [{ team_key: 'frc2' }],
  },
  {
    match_key: '2026test_qm2',
    red: [{ team_key: 'frc3' }],
    blue: [{ team_key: 'frc4' }],
  },
];

let emittedFrame = 0;

const syncResponse = {
  ok: true,
  match_key: '2026test_qm1',
  event_key: '2026test',
  run_id: 1,
  on_device_session_id: 1,
  session_key: 'od2:test',
  reused_run: false,
  status: 'provisional' as const,
  quality_score: 0.84,
  quality: { eligible_for_review: true },
  shift1_active_alliance: 'red' as const,
  shift1_source: 'manual_scout_selection',
  team_count: 1,
  points_persisted: 3,
  points_by_team: { frc1: 3 },
  skipped_unknown_teams: [],
  shift_play: null,
  shift_play_missing_reason: null,
};

describe('OnDeviceRun match lifecycle', () => {
  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    emittedFrame = 0;
    signInTestWorkspace();
    globalThis.indexedDB = new IDBFactory();
    vi.mocked(loadOpenCv).mockClear();
    vi.mocked(getEventSchedule).mockReset().mockResolvedValue({ matches } as never);
    vi.mocked(syncOnDeviceSession).mockResolvedValue(syncResponse);
  });

  it('invalidates loaded teams immediately when the match or event input changes', async () => {
    render(<OnDeviceRun />);
    const input = screen.getByPlaceholderText('e.g. 2026txhou_qm1');
    fireEvent.change(input, { target: { value: '2026test_qm1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    await screen.findByRole('button', { name: 'Continue to calibration' });
    fireEvent.change(input, { target: { value: '2026test_qm2' } });
    expect(screen.queryByRole('button', { name: 'Continue to calibration' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    await screen.findByRole('button', { name: 'Continue to calibration' });
    fireEvent.change(screen.getByPlaceholderText('inferred from match key'), { target: { value: '2026other' } });
    expect(screen.queryByRole('button', { name: 'Continue to calibration' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('event key must match');
    expect(getEventSchedule).toHaveBeenCalledTimes(2);
  });

  it('ignores a slow response for a match edited while loading', async () => {
    let resolve!: (value: never) => void;
    vi.mocked(getEventSchedule).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    render(<OnDeviceRun />);
    const input = screen.getByPlaceholderText('e.g. 2026txhou_qm1');
    fireEvent.change(input, { target: { value: '2026test_qm1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    fireEvent.change(input, { target: { value: '2026test_qm2' } });
    await act(async () => resolve({ matches } as never));
    expect(screen.queryByRole('button', { name: 'Continue to calibration' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    await screen.findByRole('button', { name: 'Continue to calibration' });
    expect(document.querySelector('.odr-teams')).toHaveTextContent('3');
  });

  it('rejects malformed pasted match keys without requesting a schedule', async () => {
    render(<OnDeviceRun />);
    fireEvent.change(screen.getByPlaceholderText('e.g. 2026txhou_qm1'), { target: { value: 'https://example.test/invalid' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter a match key');
    expect(getEventSchedule).not.toHaveBeenCalled();
  });

  it('clears captured data after loading a different match', async () => {
    render(<OnDeviceRun />);
    const matchInput = screen.getByPlaceholderText('e.g. 2026txhou_qm1');

    fireEvent.change(matchInput, { target: { value: '2026test_qm1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    expect(await screen.findByRole('button', { name: 'Continue to calibration' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Alliance with the active hub in Shift 1'), {
      target: { value: 'red' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Continue to calibration' }));
    fireEvent.click(screen.getByRole('button', { name: 'Complete calibration' }));
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Emit captured frame' }));
    expect(screen.getByRole('button', { name: 'Identify robots (1 frames)' })).toBeEnabled();

    const setupStep = screen.getByText('setup').closest('button');
    expect(setupStep).not.toBeNull();
    fireEvent.click(setupStep!);
    fireEvent.change(screen.getByPlaceholderText('e.g. 2026txhou_qm1'), { target: { value: '2026test_qm2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    await screen.findByRole('button', { name: 'Continue to calibration' });
    expect(document.querySelector('.odr-teams')).toHaveTextContent('3');
    fireEvent.change(screen.getByLabelText('Alliance with the active hub in Shift 1'), {
      target: { value: 'blue' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Continue to calibration' }));
    fireEvent.click(screen.getByRole('button', { name: 'Complete calibration' }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole('button', { name: 'Identify robots' })).toBeDisabled();
  });

  // Regression: OpenCV.js is an ~8 MB emscripten bundle whose compile blocks the main
  // thread, which starves onnxruntime's WebGPU session creation. Capture opens in camera
  // mode, so eagerly loading it there used to hang the upload path -- the detector never
  // finished initialising and a full-match run produced no tracks at all.
  const reachCaptureStage = async () => {
    render(<OnDeviceRun />);
    fireEvent.change(screen.getByPlaceholderText('e.g. 2026txhou_qm1'), {
      target: { value: '2026test_qm1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    await screen.findByRole('button', { name: 'Continue to calibration' });
    fireEvent.change(screen.getByLabelText('Alliance with the active hub in Shift 1'), {
      target: { value: 'red' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Continue to calibration' }));
    fireEvent.click(screen.getByRole('button', { name: 'Complete calibration' }));
    await act(async () => {
      await Promise.resolve();
    });
  };

  it('does not start the blocking OpenCV load the moment capture opens', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await reachCaptureStage();
      expect(loadOpenCv).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('never loads OpenCV when the scout switches straight to the upload path', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await reachCaptureStage();
      fireEvent.click(screen.getByRole('tab', { name: 'Upload video' }));
      await act(async () => {
        vi.advanceTimersByTime(10_000);
        await Promise.resolve();
      });
      expect(loadOpenCv).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('still loads OpenCV for a scout who stays on the camera path', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await reachCaptureStage();
      await act(async () => {
        vi.advanceTimersByTime(5_000);
        await Promise.resolve();
      });
      expect(loadOpenCv).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('syncs the complete v2 quality and provenance payload', async () => {
    render(<OnDeviceRun />);
    fireEvent.change(screen.getByPlaceholderText('e.g. 2026txhou_qm1'), {
      target: { value: '2026test_qm1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    await screen.findByRole('button', { name: 'Continue to calibration' });
    fireEvent.change(screen.getByLabelText('Alliance with the active hub in Shift 1'), {
      target: { value: 'red' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Continue to calibration' }));
    fireEvent.click(screen.getByRole('button', { name: 'Complete calibration' }));
    fireEvent.change(screen.getByLabelText('Match clock at recording start (seconds)'), {
      target: { value: '5' },
    });
    for (let index = 0; index < 3; index += 1) {
      fireEvent.click(await screen.findByRole('button', { name: 'Emit captured frame' }));
    }
    fireEvent.click(screen.getByRole('button', { name: 'Identify robots (3 frames)' }));
    fireEvent.change(await screen.findByLabelText('Assign track 0 to a team'), {
      target: { value: 'frc1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save & view results (1)' }));

    await screen.findByText(/Session provisional · quality 84%/);
    expect(syncOnDeviceSession).toHaveBeenCalledWith(
      expect.objectContaining({
        matchKey: '2026test_qm1',
        workspaceId: 1,
        payload: expect.objectContaining({
          schemaVersion: 'on_device_session_v2',
          modelVersion: 'test-yolo-v2',
          calibrationVersion: 'manual_corners_v1',
          calibrationVerified: true,
          captureSource: 'camera',
          identityConfidence: 1,
          identitySource: 'manual',
          timingSource: 'manual',
          captureToMatchOffsetSec: 5,
          shift1ActiveAlliance: 'red',
          shift1Source: 'manual_scout_selection',
          executionProvider: 'webgpu',
          sampledFrameCount: 3,
          inferenceMedianMs: 15,
          inferenceP90Ms: 18,
          thermalDriftPct: 2,
        }),
      }),
      'test-workspace-token',
    );
    const synced = vi.mocked(syncOnDeviceSession).mock.calls[0][0];
    expect((synced.payload as { pointsByTeam: Record<string, Array<{ timeSec: number }>> })
      .pointsByTeam.frc1[0].timeSec).toBe(6);
    expect(screen.getByRole('region', { name: 'Run results' })).toHaveTextContent('3 saved positions');
    await act(async () => { await vi.waitFor(async () => {
      const db = await openDb();
      try { expect((await listSessions(db))[0].syncResult).toEqual(syncResponse); }
      finally { db.close(); }
    }); });
  });

  it('leaves shift 1 to the server when the scout does not pick it', async () => {
    render(<OnDeviceRun />);
    fireEvent.change(screen.getByPlaceholderText('e.g. 2026txhou_qm1'), {
      target: { value: '2026test_qm1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    const next = await screen.findByRole('button', { name: 'Continue to calibration' });
    expect(next).toBeEnabled();
    fireEvent.click(next);
    fireEvent.click(screen.getByRole('button', { name: 'Complete calibration' }));
    fireEvent.change(screen.getByLabelText('Match clock at recording start (seconds)'), {
      target: { value: '5' },
    });
    for (let index = 0; index < 3; index += 1) {
      fireEvent.click(await screen.findByRole('button', { name: 'Emit captured frame' }));
    }
    fireEvent.click(screen.getByRole('button', { name: 'Identify robots (3 frames)' }));
    fireEvent.change(await screen.findByLabelText('Assign track 0 to a team'), {
      target: { value: 'frc1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save & view results (1)' }));

    await screen.findByText(/Session provisional/);
    const synced = vi.mocked(syncOnDeviceSession).mock.calls[0][0];
    expect(synced.payload).toMatchObject({ shift1ActiveAlliance: null, shift1Source: null });
  });
  it('keeps the identified recording open when local storage fails', async () => {
    await reachCaptureStage();
    for (let index = 0; index < 3; index++) fireEvent.click(await screen.findByRole('button', { name: 'Emit captured frame' }));
    fireEvent.click(screen.getByRole('button', { name: 'Identify robots (3 frames)' }));
    fireEvent.change(await screen.findByLabelText('Assign track 0 to a team'), { target: { value: 'frc1' } });
    const put = IDBObjectStore.prototype.put;
    vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (this: IDBObjectStore, ...args) {
      if (this.name === 'sessions') throw new DOMException('Full', 'QuotaExceededError');
      return put.apply(this, args);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save & view results (1)' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be saved on this phone');
    expect(screen.getByLabelText('Assign track 0 to a team')).toHaveValue('frc1');
    expect(screen.getByRole('button', { name: 'Save & view results (1)' })).toBeEnabled();
    expect(syncOnDeviceSession).not.toHaveBeenCalled();
    expect(screen.queryByText(/Saved offline/)).toBeNull();
  });

});
