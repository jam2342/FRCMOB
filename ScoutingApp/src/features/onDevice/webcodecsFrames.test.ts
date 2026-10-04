import { describe, expect, it } from 'vitest';

import { sampleFramesWithWebCodecs, webCodecsAvailable, WebCodecsUnsupported } from './webcodecsFrames';

describe('sampleFramesWithWebCodecs', () => {
  it('reports unsupported, so the caller can play the video instead', async () => {
    // jsdom has no VideoDecoder, like an older phone browser
    expect(webCodecsAvailable()).toBe(false);
    await expect(
      sampleFramesWithWebCodecs(new Blob([new Uint8Array(16)]), {
        startSec: 0,
        endSec: 10,
        intervalSec: 1 / 3,
        onFrame: async () => {},
      }),
    ).rejects.toBeInstanceOf(WebCodecsUnsupported);
  });
});
