import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SyncStatusCard } from './SyncStatusCard';

vi.mock('../../api', () => ({ syncOnDeviceSession: vi.fn() }));
vi.mock('../../utils/offlineQueue', () => ({ flush: vi.fn(async () => 0), listFailedMutations: vi.fn(async () => []), recoveryChanges: vi.fn(async () => []) }));
vi.mock('../../hooks/useOnlineStatus', () => ({ useOnlineStatus: () => ({ online: true, queueSize: 0 }) }));


it('offers export but no ineffective Sync now for rejected changes alone', async () => {
  globalThis.indexedDB = new IDBFactory();
  const { listFailedMutations } = await import('../../utils/offlineQueue');
  vi.mocked(listFailedMutations).mockResolvedValue([{label:'Picklist changes',failure:{reason:'conflict'}}] as never);
  render(<SyncStatusCard />);
  await screen.findByRole('button', { name: 'Export unsynced changes' });
  expect(screen.queryByRole('button', { name: 'Sync now' })).not.toBeInTheDocument();
});
