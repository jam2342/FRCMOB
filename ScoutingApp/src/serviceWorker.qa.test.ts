// Regression from the 2026-10-04 QA pass.
// @vitest-environment node
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => { vi.unstubAllEnvs(); vi.resetModules(); });

it('keeps signed pit photo requests eligible for offline image caching', async () => {
  vi.stubEnv('VITE_API_URL', '/api');
  const { pitPhotoDisplayUrl } = await import('./api');
  const path = '/media/pit_photos/2026arc/frc254/robot.jpg';
  const displayed = pitPhotoDisplayUrl({ photos: [path], photo_urls: [`${path}?expires=1900000000&signature=qa-fixture`] }, 0);
  expect(displayed).toContain('signature=qa-fixture');
  let onFetch!: (event: unknown) => void;
  const self = { location: { origin: 'https://app.test' }, addEventListener: (name: string, handler: (event: unknown) => void) => {
    if (name === 'fetch') onFetch = handler;
  } };
  const caches = { match: vi.fn(async () => new Response('saved photo')) };
  runInNewContext(readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8'), { self, URL, caches });
  const respondWith = vi.fn();
  onFetch({ request: { method: 'GET', mode: 'no-cors', url: new URL(displayed, self.location.origin).href }, respondWith });
  expect(respondWith).toHaveBeenCalledTimes(1);
});
