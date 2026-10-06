import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../components/EventPicker', () => ({ EventPicker: ({ onSelect, value }: { onSelect: (key: string) => void; value: string }) => <>
  <button onClick={() => onSelect('2026new')}>New event</button>
  <button onClick={() => onSelect(value)}>Load event</button>
</> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
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
it.each([false, true])('shows predictions before accuracy and defaults to Upcoming (phone=%s)', async phone => {
  vi.stubGlobal('matchMedia', () => ({ matches: phone, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const alliance = { teams: [], synergy: { available: false, pair_breakdown: [] } };
  vi.mocked(getEventScheduleWithSynergy).mockResolvedValue({ matches: [1,2].map(number => ({ match_key:`2026old_qm${number}`,comp_level:'qm',match_number:number,set_number:1,red:alliance,blue:alliance,prediction:{available:true,red_win_prob:0.9,blue_win_prob:0.1,source_label:'deterministic_fuel_v1'} })) } as never);
  vi.mocked(getEventSchedule).mockResolvedValue({ matches: [{match_key:'2026old_qm1',is_completed:true,winner_alliance:'red',red_score:10,blue_score:5}, {match_key:'2026old_qm2',is_completed:false}] } as never);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><MatchPredictionPage /></MemoryRouter>);
  const accuracy = await screen.findByText('Forecast check');
  const list = screen.getByRole('heading', {name:'Match Predictions'});
  expect(list.compareDocumentPosition(accuracy) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole('button',{name:'Upcoming'})).toHaveClass('active');
  expect(screen.queryByText('Qual 1')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'All'}));
  expect(screen.getByText('Qual 1')).toBeInTheDocument();
});
it('defaults to All when every match is completed', async () => {
  const alliance = { teams: [], synergy: { available: false, pair_breakdown: [] } };
  vi.mocked(getEventScheduleWithSynergy).mockResolvedValue({matches:[{match_key:'2026old_qm1',comp_level:'qm',match_number:1,set_number:1,red:alliance,blue:alliance}]} as never);
  vi.mocked(getEventSchedule).mockResolvedValue({matches:[{match_key:'2026old_qm1',is_completed:true}]} as never);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><MatchPredictionPage /></MemoryRouter>);
  await screen.findByRole('heading',{name:'Match Predictions'});
  expect(screen.getByRole('button',{name:'All'})).toHaveClass('active');
});
it('shows a phone 20 matches at a time', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const alliance = { teams: [], synergy: { available: false, pair_breakdown: [] } };
  const numbers = Array.from({ length: 25 }, (_, i) => i + 1);
  vi.mocked(getEventScheduleWithSynergy).mockResolvedValue({ matches: numbers.map(number => ({ match_key:`2026old_qm${number}`,comp_level:'qm',match_number:number,set_number:1,red:alliance,blue:alliance })) } as never);
  vi.mocked(getEventSchedule).mockResolvedValue({ matches: numbers.map(number => ({ match_key:`2026old_qm${number}`,is_completed:true })) } as never);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><MatchPredictionPage /></MemoryRouter>);
  await screen.findByText('Qual 20');
  expect(screen.queryByText('Qual 21')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Show 5 more'}));
  expect(screen.getByText('Qual 25')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/Show \d+ more/})).not.toBeInTheDocument();
});
