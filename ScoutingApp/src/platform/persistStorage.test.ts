import { afterEach, expect, it, vi } from 'vitest';
import { keepStorageForQueuedWork, resetPersistStorageForTests } from './persistStorage';

const stub = (persisted: boolean) => {
  const persist = vi.fn(async () => true);
  vi.stubGlobal('navigator', { storage: { persisted: vi.fn(async () => persisted), persist } });
  return persist;
};
afterEach(() => { vi.unstubAllGlobals(); resetPersistStorageForTests(); });

it('asks once, only if the site is not kept already', async () => {
  const persist = stub(false);
  keepStorageForQueuedWork();
  keepStorageForQueuedWork();
  await vi.waitFor(() => expect(persist).toHaveBeenCalledTimes(1));
  resetPersistStorageForTests();
  const again = stub(true);
  keepStorageForQueuedWork();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(again).not.toHaveBeenCalled();
});

it('does nothing where the browser has no storage manager', () => {
  vi.stubGlobal('navigator', {});
  expect(() => keepStorageForQueuedWork()).not.toThrow();
});
