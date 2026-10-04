import 'fake-indexeddb/auto';

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

import { getEventSchedule } from '../../api';
import { OnDeviceRun } from './OnDeviceRun';
import { loadOpenCv } from './opticalFlow';

// Companion to OnDeviceRun.test.tsx, which pins the OpenCV path. This file pins the other
// side of the flag: with the local core enabled the 7.3 MB OpenCV build must never be
// fetched at all, and stabilisation must come up without waiting for anything.

vi.mock('../../api', () => ({
  getEventSchedule: vi.fn(),
  syncOnDeviceSession: vi.fn(),
}));
vi.mock('../../components/cv/FieldHeatmap', () => ({ FieldHeatmap: () => null }));
vi.mock('./FieldCalibration', () => ({
  FieldCalibration: ({ onCalibrated }: { onCalibrated?: (v: { homography: number[][] }) => void }) => (
    <button type="button" onClick={() => onCalibrated?.({ homography: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] })}>
      Complete calibration
    </button>
  ),
}));
vi.mock('./MatchRecorder', () => ({ MatchRecorder: () => null }));
vi.mock('./VideoFileProcessor', () => ({ VideoFileProcessor: () => null }));
vi.mock('./sync', () => ({ flushPendingOnDeviceSessions: vi.fn() }));
vi.mock('./opticalFlow', () => ({
  createCvPoseResolver: () => ({ resolve: () => null, dispose: vi.fn() }),
  createLocalPoseResolver: vi.fn(() => ({ resolve: () => null, dispose: vi.fn() })),
  loadOpenCv: vi.fn(async () => ({})),
  USE_LOCAL_OPTICAL_FLOW: true,
}));

const matches = [{ match_key: '2026test_qm1', red: [{ team_key: 'frc1' }], blue: [{ team_key: 'frc2' }] }];

describe('OnDeviceRun with the local optical-flow core', () => {
  beforeEach(() => {
    globalThis.indexedDB = new IDBFactory();
    vi.mocked(loadOpenCv).mockClear();
    vi.mocked(getEventSchedule).mockResolvedValue({ matches } as never);
  });

  it('reaches capture without ever fetching OpenCV', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
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
        // Well past the grace period the OpenCV path waits out.
        vi.advanceTimersByTime(30_000);
        await Promise.resolve();
      });
      expect(loadOpenCv).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
