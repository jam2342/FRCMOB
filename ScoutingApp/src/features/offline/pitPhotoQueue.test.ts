import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { beforeEach, expect, it, vi } from 'vitest';
import {
  PIT_PHOTO_QUEUE_MAX_ITEMS,
  createIdbPitPhotoStore,
  createMemoryPitPhotoStore,
  deleteQueuedPitPhoto,
  enqueuePitPhoto,
  flushPitPhotos,
  listQueuedPitPhotos,
  isPitPhotoRefusal,
  pitPhotoBackoffMs,
  retryRefusedPitPhoto,
  setPitPhotoStore,
} from './pitPhotoQueue';
import type { PitPhotoStore } from './pitPhotoQueue';
import { HttpStatusError } from '../../api';

const blob = (text = 'x', type = 'image/jpeg') => new Blob([text], { type });
const photo = (over: Record<string, unknown> = {}) => ({ blob: blob(), workspaceId: 1, eventKey: '2026arc', teamKey: 'frc100', scoutProfile: 'Priya', ...over });

beforeEach(() => setPitPhotoStore(createMemoryPitPhotoStore()));

it('keeps the photo with who, where and when, and lists oldest first', async () => {
  await enqueuePitPhoto(photo({ teamKey: 'frc200' }), 2000);
  const first = await enqueuePitPhoto(photo(), 1000);
  expect(first.ok).toBe(true);
  const rows = await listQueuedPitPhotos();
  expect(rows.map((row) => row.teamKey)).toEqual(['frc100', 'frc200']);
  expect(rows[0]).toMatchObject({ eventKey: '2026arc', scoutProfile: 'Priya', createdAt: 1000, workspaceId: 1, attempts: 0 });
});

it('uploads in order and removes each photo once it is sent', async () => {
  await enqueuePitPhoto(photo({ teamKey: 'frc200' }), 2000);
  await enqueuePitPhoto(photo({ teamKey: 'frc100' }), 1000);
  const sent: string[] = [];
  const uploaded = vi.fn();
  window.addEventListener('pitphotos:uploaded', uploaded);
  const result = await flushPitPhotos(async (row) => { sent.push(row.teamKey); return { ok: true }; }, { workspaceId: 1, now: () => 5000 });
  window.removeEventListener('pitphotos:uploaded', uploaded);
  expect(sent).toEqual(['frc100', 'frc200']);
  expect(result).toEqual({ uploaded: 2, remaining: 0 });
  expect(await listQueuedPitPhotos()).toEqual([]);
  expect(uploaded).toHaveBeenCalledTimes(2);
});

it('stops the run on a dead connection and backs the failed photo off before retrying it', async () => {
  await enqueuePitPhoto(photo({ teamKey: 'frc100' }), 1000);
  await enqueuePitPhoto(photo({ teamKey: 'frc200' }), 2000);
  const upload = vi.fn().mockRejectedValueOnce(new Error('Failed to fetch')).mockResolvedValue({});
  await flushPitPhotos(upload, { workspaceId: 1, now: () => 10_000 });
  // One attempt, not one per photo.
  expect(upload).toHaveBeenCalledTimes(1);
  let rows = await listQueuedPitPhotos();
  expect(rows).toHaveLength(2);
  expect(rows[0]).toMatchObject({ attempts: 1, nextAttemptAt: 10_000 + pitPhotoBackoffMs(1), lastError: 'Failed to fetch', refused: false });
  // Too soon for the first; the one behind it doesn't wait on it.
  await flushPitPhotos(upload, { workspaceId: 1, now: () => 11_000 });
  expect(upload.mock.calls.map(([row]) => row.teamKey)).toEqual(['frc100', 'frc200']);
  expect((await listQueuedPitPhotos()).map((row) => row.teamKey)).toEqual(['frc100']);
  // After the wait the first goes too.
  await flushPitPhotos(upload, { workspaceId: 1, now: () => 10_000 + pitPhotoBackoffMs(1) });
  rows = await listQueuedPitPhotos();
  expect(rows).toEqual([]);
  expect(pitPhotoBackoffMs(2)).toBe(pitPhotoBackoffMs(1) * 2);
  expect(pitPhotoBackoffMs(50)).toBeLessThanOrEqual(5 * 60_000);
});

it('sets aside a photo the server refuses and keeps uploading the ones behind it', async () => {
  await enqueuePitPhoto(photo({ teamKey: 'frc100' }), 1000);
  await enqueuePitPhoto(photo({ teamKey: 'frc200' }), 2000);
  const refusal = new HttpStatusError(400, 'Photo limit reached (12 per team). Delete one first.');
  const upload = vi.fn(async (row: { teamKey: string }) => {
    if (row.teamKey === 'frc100') throw refusal;
    return {};
  });
  const result = await flushPitPhotos(upload, { workspaceId: 1, now: () => 10_000 });
  expect(result).toEqual({ uploaded: 1, remaining: 1 });
  const [kept] = await listQueuedPitPhotos();
  expect(kept).toMatchObject({ teamKey: 'frc100', refused: true, lastError: refusal.message });
  // Timers and reconnects leave it alone: retrying can't change the answer.
  for (const now of [20_000, 10 * 60_000]) await flushPitPhotos(upload, { workspaceId: 1, now: () => now, force: true });
  expect(upload).toHaveBeenCalledTimes(2);
  // The scout asks for another try (after deleting a photo on the server, say).
  expect(await retryRefusedPitPhoto(kept.id, 30_000)).toBe(true);
  await flushPitPhotos(async () => ({}), { workspaceId: 1, now: () => 30_000 });
  expect(await listQueuedPitPhotos()).toEqual([]);
});

it('treats timeouts, rate limits and server errors as the connection, not the photo', () => {
  expect(isPitPhotoRefusal(new HttpStatusError(413, 'full'))).toBe(true);
  expect(isPitPhotoRefusal(new HttpStatusError(403, 'removed'))).toBe(true);
  for (const status of [408, 429, 500, 502, 503]) expect(isPitPhotoRefusal(new HttpStatusError(status, 'x'))).toBe(false);
  expect(isPitPhotoRefusal(new TypeError('Failed to fetch'))).toBe(false);
  expect(isPitPhotoRefusal(null)).toBe(false);
});

it('never sends one team workspace photo under another login', async () => {
  await enqueuePitPhoto(photo({ workspaceId: 2 }), 1000);
  const upload = vi.fn();
  await flushPitPhotos(upload, { workspaceId: 1, now: () => 5000 });
  expect(upload).not.toHaveBeenCalled();
  expect(await listQueuedPitPhotos()).toHaveLength(1);
});

it('lets a waiting photo be deleted', async () => {
  const queued = await enqueuePitPhoto(photo());
  if (!queued.ok) throw new Error('not queued');
  expect(await deleteQueuedPitPhoto(queued.item.id)).toBe(true);
  expect(await listQueuedPitPhotos()).toEqual([]);
});

it('refuses past the cap with a clear message', async () => {
  for (let i = 0; i < PIT_PHOTO_QUEUE_MAX_ITEMS; i += 1) await enqueuePitPhoto(photo(), i);
  const over = await enqueuePitPhoto(photo());
  expect(over).toMatchObject({ ok: false, reason: 'full' });
  if (!over.ok) expect(over.message).toMatch(/40/);
  expect(await listQueuedPitPhotos()).toHaveLength(PIT_PHOTO_QUEUE_MAX_ITEMS);
});

it('refuses a photo that would pass the size cap', async () => {
  const big = new Blob([new Uint8Array(61 * 1024 * 1024)], { type: 'image/jpeg' });
  expect(await enqueuePitPhoto(photo({ blob: big }))).toMatchObject({ ok: false, reason: 'full' });
});

it('falls back to online-only when storage is blocked, without throwing', async () => {
  const blocked: PitPhotoStore = {
    getAll: () => Promise.reject(new Error('SecurityError')),
    put: () => Promise.reject(new Error('SecurityError')),
    remove: () => Promise.reject(new Error('SecurityError')),
  };
  setPitPhotoStore(blocked);
  const result = await enqueuePitPhoto(photo());
  expect(result).toMatchObject({ ok: false, reason: 'unavailable' });
  expect(await listQueuedPitPhotos()).toEqual([]);
  expect(await deleteQueuedPitPhoto('nope')).toBe(false);
});

it('stores photos in IndexedDB and reads them back', async () => {
  setPitPhotoStore(createIdbPitPhotoStore(new IDBFactory()));
  const queued = await enqueuePitPhoto(photo({ blob: blob('hello') }));
  expect(queued.ok).toBe(true);
  const [row] = await listQueuedPitPhotos();
  expect(new TextDecoder().decode(row.data)).toBe('hello');
  expect(row.type).toBe('image/jpeg');
});

it('reports an unavailable IndexedDB as unavailable', async () => {
  setPitPhotoStore(createIdbPitPhotoStore({ open: () => { throw new Error('blocked'); } } as unknown as IDBFactory));
  expect(await enqueuePitPhoto(photo())).toMatchObject({ ok: false, reason: 'unavailable' });
});
