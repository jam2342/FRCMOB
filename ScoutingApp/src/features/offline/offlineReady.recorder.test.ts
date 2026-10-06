import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../onDevice/detector', () => ({ pickDeviceModel: async () => ({ url: '/model.onnx' }) }));
vi.mock('../../routePrefetch', () => ({
  appShellReadyOffline: async () => true,
  prepareAppShellOffline: async () => {},
}));

const flow = vi.hoisted(() => ({ local: true }));
vi.mock('../onDevice/opticalFlow', () => ({
  get USE_LOCAL_OPTICAL_FLOW() {
    return flow.local;
  },
}));

afterEach(() => vi.unstubAllGlobals());

describe('offline recorder tracker assets', () => {
  it.each([true, false])('requires OpenCV only for the fallback (local=%s)', async (local) => {
    flow.local = local;
    const urls: string[] = [];
    vi.stubGlobal('caches', {
      match: vi.fn(async (url: string) => {
        urls.push(url);
        return url.includes('opencv') ? undefined : new Response('saved');
      }),
    });
    const { recorderReadyOffline } = await import('./offlineReady');
    expect(await recorderReadyOffline()).toBe(local);
    expect(urls.some((url) => url.includes('opencv'))).toBe(!local);
  });
});
