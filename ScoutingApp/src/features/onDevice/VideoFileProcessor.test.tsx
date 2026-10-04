import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { matchWindow, stoppedEarly } from './samplingRate';
import { VideoFileProcessor } from './VideoFileProcessor';
import { advanceSampleTarget, safePlaybackRate, SPEEDUP_MAX_RATE } from './samplingRate';

vi.mock('./detector', () => ({
  ON_DEVICE_MODEL_URL: '/models/test.onnx',
  ON_DEVICE_MODEL_VERSION: 'test-model',
  createDeviceDetector: vi.fn(async () => ({
    modelVersion: 'test-model',
    confThreshold: 0.25,
    session: {},
    inputName: 'input',
    outputName: 'output',
    executionProvider: 'wasm',
  })),
  detectRobots: vi.fn(async () => []),
}));

function stubObjectUrls(url = 'blob:match-video') {
  const createObjectURL = vi.fn(() => url);
  const revokeObjectURL = vi.fn();
  Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true });
  Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true });
  return { createObjectURL, revokeObjectURL };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('safePlaybackRate', () => {
  const interval = 1 / 3; // the 3 fps default -> a 333 ms budget between samples

  it('stays at real time when a frame costs more than the budget allows', () => {
    // A mid-range phone at ~200 ms/frame cannot outrun playback; speeding up would
    // only drop samples the sampler promised to take.
    expect(safePlaybackRate(interval, [210, 195, 205, 200, 198, 202])).toBe(1);
  });

  it('speeds up when the measured cost leaves room', () => {
    // ~57 ms/frame measured on a real Metal GPU -> 166 ms budget / 57 = 2x.
    expect(safePlaybackRate(interval, [58, 55, 57, 60, 56, 57])).toBe(2);
  });

  it('is capped so playback never outruns distinct presented frames', () => {
    expect(safePlaybackRate(interval, [1, 1, 1, 1, 1, 1])).toBe(SPEEDUP_MAX_RATE);
  });

  it('ignores a slow first sample rather than being dragged down by it', () => {
    // The first call carries one-off shader compilation; a mean would see ~150 ms
    // and settle for 1x, the median sees the real ~57 ms steady state.
    expect(safePlaybackRate(interval, [620, 57, 56, 58, 57, 55])).toBe(2);
  });

  it('falls back to real time on unusable measurements', () => {
    expect(safePlaybackRate(interval, [])).toBe(1);
    expect(safePlaybackRate(interval, [0, 0, 0])).toBe(1);
  });

  it('scales the budget with the sampling interval, not just the frame cost', () => {
    // Same 57 ms cost, but sampling at 1 fps leaves three times the budget.
    expect(safePlaybackRate(1, [58, 55, 57, 60, 56, 57])).toBe(SPEEDUP_MAX_RATE);
  });
});

describe('advanceSampleTarget', () => {
  const interval = 1 / 3;

  it('does not let a late frame drag the schedule late for good', () => {
    // Frames land a little past each boundary. Re-anchoring to the frame itself would
    // fold that overshoot in every time; the grid must not move.
    let target = advanceSampleTarget(-Infinity, 0, interval);
    let frame = target + 0.02; // 20 ms late, every time
    for (let i = 0; i < 100; i += 1) {
      target = advanceSampleTarget(target, frame, interval);
      frame = target + 0.02;
    }
    // 101 samples on a 1/3 s grid from 0 -> the target is still on the grid, not ~2 s adrift.
    expect(target).toBeCloseTo(101 / 3, 6);
  });

  it('keeps the sample count the naive form would lose over a full match', () => {
    // 165 s of match at 3 fps is 495 samples. Late-by-20ms frames cost 11 of them when
    // the schedule re-anchors to the frame; on the grid it costs none.
    const countSamples = (anchorToFrame: boolean) => {
      let target = 0;
      let taken = 0;
      for (let t = 0; t < 165; t += 1 / 60) {
        if (t >= target) {
          taken += 1;
          target = anchorToFrame ? t + interval : advanceSampleTarget(target, t, interval);
        }
      }
      return taken;
    };
    expect(countSamples(false)).toBe(495);
    expect(countSamples(true)).toBeLessThan(495);
  });

  it('resyncs instead of bursting when a slow frame falls far behind', () => {
    // A frame that took a full second must not queue up three back-to-back samples.
    const target = advanceSampleTarget(1.0, 2.5, interval);
    expect(target).toBeCloseTo(2.5 + interval, 6);
  });

  it('starts the grid at the first frame it is given', () => {
    expect(advanceSampleTarget(-Infinity, 8, interval)).toBeCloseTo(8 + interval, 6);
  });
});

describe('VideoFileProcessor lifecycle', () => {
  it('starts native video loading after installing error listeners and allows retry', async () => {
    const { revokeObjectURL } = stubObjectUrls();
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(function (this: HTMLMediaElement) {
      this.dispatchEvent(new Event('error'));
    });
    const { container, unmount } = render(
      <VideoFileProcessor resolvePose={() => null} onFrame={vi.fn()} onComplete={vi.fn()} />,
    );
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['video'], 'match.mp4')] } });
    await waitFor(() => expect(container.textContent).toContain('could not read that video file'));
    expect(load).toHaveBeenCalledTimes(1);
    expect(input.disabled).toBe(false);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:match-video');
    expect(document.body.querySelector('video')).toBeNull();
    unmount();
  });

  it('reports a video startup timeout and restores the file input', async () => {
    vi.useFakeTimers();
    const { revokeObjectURL } = stubObjectUrls();
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
    const { container, unmount } = render(
      <VideoFileProcessor resolvePose={() => null} onFrame={vi.fn()} onComplete={vi.fn()} />,
    );
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File(['video'], 'match.mp4')] } });
      await Promise.resolve();
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(container.textContent).toContain('Video did not load. Choose the file again.');
    expect(input.disabled).toBe(false);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:match-video');
    expect(document.body.querySelector('video')).toBeNull();
    unmount();
  });

  it('aborts a pending video load and revokes the object URL on unmount', async () => {
    const { createObjectURL, revokeObjectURL } = stubObjectUrls();
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined);
    const { container, unmount } = render(
      <VideoFileProcessor resolvePose={() => null} onFrame={vi.fn()} onComplete={vi.fn()} />,
    );

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['video'], 'match.mp4', { type: 'video/mp4' })] },
    });

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));

    act(() => {
      unmount();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(revokeObjectURL).toHaveBeenCalledWith('blob:match-video');
    expect(document.body.querySelector('video')).toBeNull();
  });
});

describe('stoppedEarly', () => {
  it('flags a run that stopped mid-match and accepts one that reached the end', () => {
    expect(stoppedEarly(97, 170, 1 / 3)).toBe(true);
    expect(stoppedEarly(169.8, 170, 1 / 3)).toBe(false);
    expect(stoppedEarly(-Infinity, 170, 1 / 3)).toBe(true);
  });
});

describe('matchWindow', () => {
  it('covers only the match, plus a short grace after the buzzer', () => {
    expect(matchWindow(8, 182.23)).toEqual({ startSec: 8, endSec: 170 });
  });

  it('stops at the end of a clip that ends early', () => {
    expect(matchWindow(30, 120)).toEqual({ startSec: 30, endSec: 120 });
  });
});
