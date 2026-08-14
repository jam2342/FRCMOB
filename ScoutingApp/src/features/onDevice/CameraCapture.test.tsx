import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CameraCapture } from './CameraCapture';

const flushPromises = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

function setMediaDevices(getUserMedia: ReturnType<typeof vi.fn>) {
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia },
    configurable: true,
  });
}

function streamWithStop(stop: ReturnType<typeof vi.fn>): MediaStream {
  return { getTracks: () => [{ stop }] } as unknown as MediaStream;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('CameraCapture lifecycle', () => {
  it('stops a stream that resolves after the component unmounts', async () => {
    const stop = vi.fn();
    let resolveStream!: (stream: MediaStream) => void;
    setMediaDevices(vi.fn(() => new Promise<MediaStream>((resolve) => {
      resolveStream = resolve;
    })));

    const { unmount } = render(<CameraCapture onCapture={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /use camera/i }));
    unmount();

    resolveStream(streamWithStop(stop));
    await flushPromises();

    expect(stop).toHaveBeenCalledTimes(1);
  });

  it('stops the stream when video playback fails during start', async () => {
    const stop = vi.fn();
    setMediaDevices(vi.fn(async () => streamWithStop(stop)));
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new Error('play failed'));

    render(<CameraCapture onCapture={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /use camera/i }));

    expect(await screen.findByText('play failed')).toBeInTheDocument();
    expect(stop).toHaveBeenCalledTimes(1);
  });
});
