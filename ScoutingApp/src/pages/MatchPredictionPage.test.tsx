import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../hooks/useMobileLayout', () => ({ MOBILE_LAYOUT_BREAKPOINT: 760, useMobileLayout: () => false }));
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../components/EventPicker', () => ({ EventPicker: ({ onSelect, value }: { onSelect: (key: string) => void; value: string }) => <>
  <button onClick={() => onSelect('2026new')}>New event</button>
  <button onClick={() => onSelect(value)}>Load event</button>
</> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });
import { MatchPredictionPage } from './MatchPredictionPage';
import { getEventSchedule, getEventScheduleWithSynergy } from '../api';
vi.mock('../api', () => ({ getEventSchedule: vi.fn(), getEventScheduleWithSynergy: vi.fn(), getEventPredictions: vi.fn(async () => ({ predictions: null })) }));
it('uses unknown match status when schedule fails and offers retry', async () => {
  const alliance = { teams: [], synergy: { available: false, pair_breakdown: [] } };
  vi.mocked(getEventScheduleWithSynergy).mockResolvedValue({ matches: [{ match_key: '2026old_qm1', comp_level: 'qm', match_number: 1, set_number: 1, red: alliance, blue: alliance }] } as never);
  vi.mocked(getEventSchedule).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ matches: [{ match_key: '2026old_qm1', is_completed: true, red_score: 10, blue_score: 5, winner_alliance: 'red' }] } as never);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><MatchPredictionPage /></MemoryRouter>);
  await screen.findByText(/Schedule unavailable\./);
  expect(screen.getAllByLabelText('Result unknown').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button', { name: 'Retry schedule' }));
  await waitFor(() => expect(screen.queryByText(/Schedule unavailable\./)).not.toBeInTheDocument());
  expect(screen.getByText('10–5')).toBeInTheDocument();
});
