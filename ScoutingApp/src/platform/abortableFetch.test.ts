import { describe, expect, it, vi } from 'vitest';
import { abortableFetch } from './abortableFetch';
describe('native HTTP abort handling', () => {
  it('releases a caller when the native bridge ignores AbortSignal', async () => {
    const bridge = vi.fn(() => new Promise<Response>(() => undefined));
    const controller = new AbortController();
    const pending = abortableFetch(bridge)('https://example.com/save', { method: 'POST', signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });
  it('never starts an already cancelled write', async () => {
    const bridge = vi.fn();
    const controller = new AbortController();
    controller.abort();
    await expect(abortableFetch(bridge)('https://example.com/save', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' });
    expect(bridge).not.toHaveBeenCalled();
  });
  it('removes listeners and preserves the response after a successful upload', async () => {
    const response = new Response('{}');
    const controller = new AbortController();
    const remove = vi.spyOn(controller.signal, 'removeEventListener');
    expect(await abortableFetch(vi.fn(async () => response))('https://example.com/save', { signal: controller.signal })).toBe(response);
    await Promise.resolve();
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
  });
});
