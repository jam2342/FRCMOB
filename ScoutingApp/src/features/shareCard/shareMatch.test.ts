import { afterEach, describe, expect, it, vi } from 'vitest';
import { sharePicture } from './shareMatch';
import { winnerLine } from './renderShareCard';

describe('sharing a result picture', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('hands the picture and link to the share sheet where files can be shared', async () => {
    const share = vi.fn(async () => undefined);
    vi.stubGlobal('navigator', { ...navigator, share, canShare: () => true });
    const outcome = await sharePicture(new Blob(['x'], { type: 'image/png' }), 'qm1.png', 'Qual 1', 'https://example/#/m');
    expect(outcome).toBe('shared');
    expect(share).toHaveBeenCalledWith(expect.objectContaining({ text: 'Qual 1', url: 'https://example/#/m' }));
  });

  it('saves the picture where the share sheet cannot take a file, and treats cancel as cancel', async () => {
    vi.stubGlobal('navigator', { ...navigator, share: undefined });
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    expect(await sharePicture(new Blob(['x']), 'qm1.png', 'Qual 1', 'u')).toBe('saved');
    const cancel = vi.fn(async () => { throw new DOMException('cancelled', 'AbortError'); });
    vi.stubGlobal('navigator', { ...navigator, share: cancel, canShare: () => true });
    expect(await sharePicture(new Blob(['x']), 'qm1.png', 'Qual 1', 'u')).toBe('cancelled');
  });

  it('says who won', () => {
    expect(winnerLine({ winner: 'blue' })).toBe('Blue alliance wins');
    expect(winnerLine({ winner: 'tie' })).toBe('Tied');
  });
});
