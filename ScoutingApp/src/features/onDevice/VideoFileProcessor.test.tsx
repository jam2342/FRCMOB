import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { VideoFileProcessor } from './VideoFileProcessor';

vi.mock('./detector', () => ({
  createDetector: vi.fn(async () => ({ session: {}, inputName: 'input', outputName: 'output' })),
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
  vi.restoreAllMocks();
});

describe('VideoFileProcessor lifecycle', () => {
  it('aborts a pending video load and revokes the object URL on unmount', async () => {
    const { createObjectURL, revokeObjectURL } = stubObjectUrls();
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
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
