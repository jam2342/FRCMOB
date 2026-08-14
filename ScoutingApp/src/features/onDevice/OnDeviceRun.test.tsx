import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getEventSchedule } from '../../api';
import { OnDeviceRun } from './OnDeviceRun';

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
  }: {
    onFrame: (frame: { timeSec: number; detections: []; homography: number[][] }) => void;
  }) => (
    <button
      type="button"
      onClick={() => onFrame({ timeSec: 1, detections: [], homography: [[1, 0, 0], [0, 1, 0], [0, 0, 1]] })}
    >
      Emit captured frame
    </button>
  ),
}));
vi.mock('./VideoFileProcessor', () => ({ VideoFileProcessor: () => null }));
vi.mock('./opticalFlow', () => ({
  createCvPoseResolver: () => ({ resolve: () => [[1, 0, 0], [0, 1, 0], [0, 0, 1]], dispose: vi.fn() }),
  loadOpenCv: vi.fn(async () => ({})),
}));
vi.mock('./sync', () => ({ flushPendingOnDeviceSessions: vi.fn() }));

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

describe('OnDeviceRun match lifecycle', () => {
  beforeEach(() => {
    vi.mocked(getEventSchedule).mockResolvedValue({ matches } as never);
  });

  it('clears captured data after loading a different match', async () => {
    render(<OnDeviceRun />);
    const matchInput = screen.getByPlaceholderText('e.g. 2026txhou_qm1');

    fireEvent.change(matchInput, { target: { value: '2026test_qm1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load match teams' }));
    expect(await screen.findByRole('button', { name: 'Continue to calibration' })).toBeInTheDocument();
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
    expect(await screen.findByText('3')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Continue to calibration' }));
    fireEvent.click(screen.getByRole('button', { name: 'Complete calibration' }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole('button', { name: 'Identify robots' })).toBeDisabled();
  });
});
