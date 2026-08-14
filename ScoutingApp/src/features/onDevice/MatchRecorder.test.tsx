import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MatchRecorder } from './MatchRecorder';
import { type Mat3 } from './homography';

vi.mock('./detector', () => ({
  createDetector: vi.fn(async () => ({ session: {}, inputName: 'input', outputName: 'output' })),
  detectRobots: vi.fn(async () => []),
}));

const IDENTITY: Mat3 = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];

const flushPromises = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

function setMediaDevices(getUserMedia: ReturnType<typeof vi.fn>) {
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia },
    configurable: true,
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('MatchRecorder lifecycle', () => {
  it('clears the scheduled sampling loop and stops tracks on unmount', async () => {
    vi.useFakeTimers();
    const stop = vi.fn();
    setMediaDevices(vi.fn(async () => ({ getTracks: () => [{ stop }] }) as unknown as MediaStream));
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    const clearTimeoutSpy = vi.spyOn(globalThis, 'clearTimeout');

    const { unmount } = render(
      <MatchRecorder resolvePose={() => IDENTITY} onFrame={vi.fn()} targetFps={3} />,
    );

    fireEvent.click(screen.getByRole('button', { name: /start recording/i }));
    await act(async () => {
      await flushPromises();
    });

    expect(screen.getByRole('button', { name: /stop recording/i })).toBeInTheDocument();
    const loopTimerIndex = setTimeoutSpy.mock.calls.findIndex(([, delay]) => delay === 100);
    expect(loopTimerIndex).toBeGreaterThanOrEqual(0);
    const loopTimer = setTimeoutSpy.mock.results[loopTimerIndex].value;

    act(() => {
      unmount();
    });

    expect(clearTimeoutSpy).toHaveBeenCalledWith(loopTimer);
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it('keeps captured timestamps monotonic after stopping and restarting', async () => {
    vi.useFakeTimers();
    setMediaDevices(vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] }) as unknown as MediaStream));
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    vi.spyOn(HTMLVideoElement.prototype, 'videoWidth', 'get').mockReturnValue(100);
    vi.spyOn(HTMLVideoElement.prototype, 'videoHeight', 'get').mockReturnValue(50);
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    const timestamps: number[] = [];

    render(
      <MatchRecorder
        resolvePose={() => IDENTITY}
        onFrame={(frame) => timestamps.push(frame.timeSec)}
        targetFps={10}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /start recording/i }));
    await act(async () => {
      await flushPromises();
      await vi.advanceTimersByTimeAsync(450);
    });
    fireEvent.click(screen.getByRole('button', { name: /stop recording/i }));
    const capturedBeforeRestart = timestamps.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    fireEvent.click(screen.getByRole('button', { name: /start recording/i }));
    await act(async () => {
      await flushPromises();
      await vi.advanceTimersByTimeAsync(150);
    });

    expect(timestamps.length).toBeGreaterThan(capturedBeforeRestart);
    expect(timestamps.every((timeSec, index) => index === 0 || timeSec > timestamps[index - 1])).toBe(true);
  });
});
