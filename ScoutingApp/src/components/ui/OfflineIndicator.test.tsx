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
