import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { prepareRecorderOffline, recorderReadyOffline } from './offlineReady';
import { RecorderOfflineStatus } from './RecorderOfflineStatus';
vi.mock('./offlineReady', () => ({ prepareRecorderOffline: vi.fn(), recorderReadyOffline: vi.fn() }));
vi.mock('../../hooks/useOnlineStatus', () => ({ useOnlineStatus: () => ({ online: true }) }));
vi.mock('../../routePrefetch', () => ({ shouldPrefetchRoutes: () => true }));
beforeEach(() => {
  vi.mocked(recorderReadyOffline).mockReset().mockResolvedValue(false);
  vi.mocked(prepareRecorderOffline).mockReset();
});
it('explains quota failures and lets the scout retry after freeing space', async () => {
  vi.mocked(prepareRecorderOffline).mockRejectedValueOnce(new DOMException('Full', 'QuotaExceededError'));
  render(<RecorderOfflineStatus />);
  expect(await screen.findByText(/Device storage is full/)).toBeInTheDocument();
  vi.mocked(recorderReadyOffline).mockResolvedValue(true);
  fireEvent.click(screen.getByRole('button', { name: 'Retry saving detector' }));
  expect(await screen.findByText('Detector saved on this phone: works with no signal.')).toBeInTheDocument();
});
it('recovers when the initial readiness check fails before the download starts', async () => {
  vi.mocked(recorderReadyOffline).mockRejectedValueOnce(new Error('Fixture readiness failed'));
  render(<RecorderOfflineStatus />);
  expect(await screen.findByRole('button', { name: 'Retry saving detector' })).toBeInTheDocument();
  expect(prepareRecorderOffline).not.toHaveBeenCalled();
});
