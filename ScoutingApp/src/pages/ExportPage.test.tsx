import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../hooks/useMobileLayout', () => ({ MOBILE_LAYOUT_BREAKPOINT: 760, useMobileLayout: () => false }));
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../components/EventPicker', () => ({ EventPicker: ({ onSelect, value }: { onSelect: (key: string) => void; value: string }) => <>
  <button onClick={() => onSelect('2026new')}>New event</button>
  <button onClick={() => onSelect(value)}>Load event</button>
</> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.clearAllMocks(); workspace.joined = false; });
import { ExportPage } from './ExportPage';
import { getEventRatings, getEventSchedule, getEventRankings, exportEventScoutingEntries, listPitEntries } from '../api';
import { downloadCsv } from '../utils/csvExport';
vi.mock('../api', () => ({ getEventRatings: vi.fn(), getEventSchedule: vi.fn(), getEventRankings: vi.fn(), exportEventScoutingEntries: vi.fn(), listPitEntries: vi.fn() }));
const workspace = vi.hoisted(() => ({ joined: false }));
vi.mock('../features/workspace/useWorkspace', () => ({ useWorkspace: () => workspace.joined ? { teamNumber: 254 } : null }));
vi.mock('../utils/csvExport', () => ({ downloadCsv: vi.fn(async () => true) }));
it('ignores old requests and exports rows under their loaded event', async () => {
  let finish!: (payload: never) => void;
  vi.mocked(getEventRatings).mockImplementation(key => key === '2026old' ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({ ratings: [] } as never));
  vi.mocked(getEventSchedule).mockImplementation(async key => ({ matches: [{ match_key: `${key}_qm1`, red: [], blue: [] }] }) as never);
  vi.mocked(getEventRankings).mockResolvedValue({ rankings: [] } as never);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><ExportPage /></MemoryRouter>);
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.click(screen.getByText('New event'));
  await screen.findByText('All data loaded and ready for export.');
  await act(async () => { finish({ ratings: [] } as never); });
  fireEvent.click(screen.getAllByRole('button', { name: 'Download CSV' })[1]);
  await waitFor(() => expect(downloadCsv).toHaveBeenCalled());
  expect(vi.mocked(downloadCsv).mock.calls.at(-1)?.[0]).toBe('2026new_match_schedule.csv');
  expect(vi.mocked(downloadCsv).mock.calls.at(-1)?.[2][0][0]).toBe('2026new_qm1');
});

it('exports the nested rankings response', async () => {
  const rows = [{ team_key: 'frc254', rank: 1, record: { wins: 2, losses: 0, ties: 0 } }];
  vi.mocked(getEventRatings).mockResolvedValue({ ratings: [] } as never);
  vi.mocked(getEventSchedule).mockResolvedValue({ matches: [] } as never);
  vi.mocked(getEventRankings).mockResolvedValue({ rankings: { rankings: rows } } as never);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><ExportPage /></MemoryRouter>);
  await screen.findByText('1 team ranking from TBA / FIRST.');
  fireEvent.click(screen.getAllByRole('button', { name: 'Download CSV' })[2]);
  await waitFor(() => expect(downloadCsv).toHaveBeenCalledWith(
    '2026old_rankings.csv', ['team_key', 'rank', 'record.wins', 'record.losses', 'record.ties'], [['frc254', 1, 2, 0, 0]],
  ));
});
it.each(['empty', 'failed'])('keeps workspace exports available when public data is %s', async state => {
  workspace.joined = true;
  if (state === 'failed') {
    vi.mocked(getEventRatings).mockRejectedValue(new Error('offline'));
    vi.mocked(getEventSchedule).mockRejectedValue(new Error('offline'));
    vi.mocked(getEventRankings).mockRejectedValue(new Error('offline'));
  } else {
    vi.mocked(getEventRatings).mockResolvedValue({ ratings: [] } as never);
    vi.mocked(getEventSchedule).mockResolvedValue({ matches: [] } as never);
    vi.mocked(getEventRankings).mockResolvedValue({ rankings: { rankings: [] } } as never);
  }
  vi.mocked(exportEventScoutingEntries).mockResolvedValue({ entries: [] } as never);
  vi.mocked(listPitEntries).mockResolvedValue({ entries: [] } as never);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><ExportPage /></MemoryRouter>);
  await screen.findByText(state === 'failed' ? 'Failed to load event data. Check the event key.' : 'All data loaded and ready for export.');
  expect(screen.queryByText('Team Ratings')).not.toBeInTheDocument();
  const buttons = screen.getAllByRole('button', { name: 'Download CSV' });
  expect(buttons).toHaveLength(2);
  expect(buttons[0]).toBeEnabled();
  expect(buttons[1]).toBeEnabled();
  fireEvent.click(buttons[0]);
  await waitFor(() => expect(exportEventScoutingEntries).toHaveBeenCalledWith('2026old'));
  fireEvent.click(buttons[1]);
  await waitFor(() => expect(listPitEntries).toHaveBeenCalledWith('2026old'));
});
