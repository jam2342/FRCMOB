import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../hooks/useMobileLayout', () => ({ MOBILE_LAYOUT_BREAKPOINT: 760, useMobileLayout: () => false }));
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../components/EventPicker', () => ({ EventPicker: ({ onSelect, value }: { onSelect: (key: string) => void; value: string }) => <>
  <button onClick={() => onSelect('2026new')}>New event</button>
  <button onClick={() => onSelect(value)}>Load event</button>
</> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });
import { ExportPage } from './ExportPage';
import { getEventRatings, getEventSchedule, getEventRankings } from '../api';
import { downloadCsv } from '../utils/csvExport';
vi.mock('../api', () => ({ getEventRatings: vi.fn(), getEventSchedule: vi.fn(), getEventRankings: vi.fn() }));
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
