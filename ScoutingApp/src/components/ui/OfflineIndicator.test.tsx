import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { recordSyncIssue } from '../../features/offline/syncReceipt';
import { OfflineIndicator } from './OfflineIndicator';

let status = { online: false, queueSize: 0 };
vi.mock('../../hooks/useOnlineStatus', () => ({ useOnlineStatus: () => status }));
vi.mock('../../utils/offlineQueue', () => ({ flush: () => Promise.resolve(0) }));

describe('OfflineIndicator', () => {
  afterEach(() => window.localStorage.clear());
  it('offline, says how many changes are waiting rather than every local entry', () => {
    status = { online: false, queueSize: 2 };
    window.localStorage.setItem('scouting_manual_entries_v2', JSON.stringify(new Array(300).fill({})));
    render(<OfflineIndicator />);
    expect(screen.getByText("2 changes will sync when you're back online")).toBeInTheDocument();
    expect(screen.queryByText(/300/)).toBeNull();
  });

  it('offline with nothing waiting, reassures that saves stay on the phone', () => {
    status = { online: false, queueSize: 0 };
    render(<OfflineIndicator />);
    expect(screen.getByText('Anything you save stays on this phone')).toBeInTheDocument();
  });

  it('online with a backlog, offers to sync now', () => {
    status = { online: true, queueSize: 1 };
    render(<OfflineIndicator />);
    expect(screen.getByText('1 pending change to sync')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sync now' })).toBeInTheDocument();
  });
  it('restores a recovery warning after reopening without promising to sync rejected work', () => {
    status = { online: false, queueSize: 0 };
    recordSyncIssue({ reason: 'conflict', count: 1, labels: ['Pit notes'] });
    render(<MemoryRouter><OfflineIndicator /></MemoryRouter>);
    expect(screen.getByRole('link', { name: 'Recover saved edits' })).toHaveAttribute('href', '/my-team');
    expect(screen.getByText(/original edit is still saved/)).toBeInTheDocument();
    expect(screen.queryByText(/will sync/)).toBeNull();
  });

});

describe('back online', () => {
  it('shows each waiting change go out, then one "all synced" that folds away', async () => {
    const { act } = await import('@testing-library/react');
    vi.useFakeTimers();
    status = { online: false, queueSize: 3 };
    const view = render(<MemoryRouter><OfflineIndicator /></MemoryRouter>);
    status = { online: true, queueSize: 3 };
    view.rerender(<MemoryRouter><OfflineIndicator /></MemoryRouter>);
    expect(screen.getByText('Back online · sharing 1 of 3 changes')).toBeInTheDocument();
    status = { online: true, queueSize: 1 };
    view.rerender(<MemoryRouter><OfflineIndicator /></MemoryRouter>);
    expect(screen.getByText('Back online · sharing 3 of 3 changes')).toBeInTheDocument();
    status = { online: true, queueSize: 0 };
    view.rerender(<MemoryRouter><OfflineIndicator /></MemoryRouter>);
    expect(screen.getByText('All 3 changes synced')).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(3000); });
    act(() => { vi.advanceTimersByTime(300); });
    expect(screen.queryByRole('status')).toBeNull();
    vi.useRealTimers();
  });

  it('with nothing waiting, just says it is back online for a moment', async () => {
    const { act } = await import('@testing-library/react');
    vi.useFakeTimers();
    status = { online: false, queueSize: 0 };
    const view = render(<MemoryRouter><OfflineIndicator /></MemoryRouter>);
    status = { online: true, queueSize: 0 };
    view.rerender(<MemoryRouter><OfflineIndicator /></MemoryRouter>);
    expect(screen.getByText('Back online')).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(1900); });
    act(() => { vi.advanceTimersByTime(300); });
    expect(screen.queryByText('Back online')).toBeNull();
    vi.useRealTimers();
  });
});
