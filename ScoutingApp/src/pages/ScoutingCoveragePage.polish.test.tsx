import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ScoutingCoveragePage } from './ScoutingCoveragePage';
import { getScoutingCoverage } from '../api';
const connection = vi.hoisted(() => ({online:true,isShowingOfflineData:false}));
vi.mock('../hooks/useOnlineStatus', () => ({useOnlineStatus: () => connection}));
vi.mock('../api', () => ({getScoutingCoverage:vi.fn()}));
vi.mock('../features/workspace/WorkspaceGate', () => ({WorkspaceGate: ({children}:{children:React.ReactNode}) => children}));
vi.mock('../components/EventPicker', () => ({EventPicker: ({value,onSelect}:{value:string;onSelect:(key:string)=>void}) => <button onClick={() => onSelect(value)}>Load event</button>}));
afterEach(() => {cleanup(); localStorage.clear(); connection.online=true; connection.isShowingOfflineData=false; vi.useRealTimers();});

const coverage = { summary: {total_entries:0, scout_count:0, coverage_pct:0, covered_slots:0, total_slots:6, outlier_count:0}, grid:[], leaderboard:[], outliers:[] };
it('shows saved figures with update time and retry after a background refresh fails', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.mocked(getScoutingCoverage).mockResolvedValueOnce(coverage as never).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(coverage as never);
  render(<MemoryRouter initialEntries={['/?event=2026test']}><ScoutingCoveragePage /></MemoryRouter>);
  await screen.findByText('0 of 6 slots scouted');
  const { act } = await import('@testing-library/react');
  await act(async () => { vi.advanceTimersByTime(30_000); });
  expect(screen.getByRole('alert')).toHaveTextContent(/Showing saved coverage. Last live update/);
  expect(screen.getByText('0 of 6 slots scouted')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  vi.useRealTimers();
});
it('asks for reports instead of claiming agreement with no quality evidence', async () => {
  vi.mocked(getScoutingCoverage).mockResolvedValue(coverage as never);
  render(<MemoryRouter initialEntries={['/?event=2026test']}><ScoutingCoveragePage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('tab', { name: 'Quality (0)' }));
  expect(screen.getByText(/Quality checks need scouting reports/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Start scouting' })).toHaveAttribute('href', '#/scouting?event=2026test');
});

it('labels an offline cached response without claiming a new live update', async () => {
  connection.online=false;
  connection.isShowingOfflineData=true;
  vi.mocked(getScoutingCoverage).mockResolvedValue(coverage as never);
  render(<MemoryRouter initialEntries={['/?event=2026test']}><ScoutingCoveragePage /></MemoryRouter>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Showing saved coverage. No live update confirmed yet.');
  expect(screen.getByText('0 of 6 slots scouted')).toBeInTheDocument();
});
