import { afterEach, expect, it, vi } from 'vitest';
import { prepareRecorderOffline } from './offlineReady';
vi.mock('../../routePrefetch', () => ({ appShellReadyOffline: async () => true, prepareAppShellOffline: async () => {} }));
vi.mock('../onDevice/detector', () => ({ pickDeviceModel: async () => ({ url: '/fixture-model.onnx' }) }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('handles a failed cache write immediately while the model response is still streaming', async () => {
  let finishRead!: (value: { done: boolean; value?: Uint8Array }) => void;
  const read = vi.fn(() => new Promise(resolve => { finishRead = resolve; }));
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, headers: new Headers(), clone: () => new Response('fixture'), body: { getReader: () => ({ read, cancel: async () => { finishRead?.({ done: true }); } }) } })));
  vi.stubGlobal('caches', { match: async () => undefined, open: async () => ({ put: async () => { throw new Error('Fixture cache storage full'); } }) });
  const outcome = prepareRecorderOffline(() => {}).then(() => 'ready', (error: Error) => error.message);
  const observed = await Promise.race([outcome, new Promise<string>(resolve => setTimeout(() => resolve('still waiting for the stream'), 30))]);
  finishRead?.({ done: true });
  await outcome;
  expect(observed).toBe('Fixture cache storage full');
});
