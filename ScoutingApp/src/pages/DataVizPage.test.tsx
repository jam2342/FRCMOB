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
import { DataVizPage } from './DataVizPage';
import { getEventRatings, getTeamBreakdown } from '../api';
vi.mock('../api', () => ({ getEventRatings: vi.fn(), getTeamBreakdown: vi.fn() }));
function ratings(number: number) { return { ok: true, ratings: [{ team_key: `frc${number}`, team_number: number, pros: [], cons: [], rating_0_100: 75, confidence_0_1: 0.8, subscores: { throughput: 1, endgame: 1, consistency: 1 } }] }; }
function breakdown(value: number) { return { ok: true, recent_matches: [{ match_time: 1, fuel_scoring_rate: 1, cycle_time_sec: 2, defensive_engagement_sec: 3, climb_success_prob: value }, { match_time: 2, fuel_scoring_rate: 2, cycle_time_sec: 3, defensive_engagement_sec: 4, climb_success_prob: value + 0.1 }] }; }
it('drops old event ratings and retries the same event', async () => {
  let finish!: (payload: never) => void;
  vi.mocked(getEventRatings).mockImplementation(key => key === '2026old' ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(ratings(2222) as never));
  vi.mocked(getTeamBreakdown).mockResolvedValue({ ok: true } as never);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><DataVizPage /></MemoryRouter>);
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.click(screen.getByText('New event'));
  await waitFor(() => expect(screen.getAllByText('#2222').length).toBeGreaterThan(0));
  await act(async () => { finish(ratings(1111) as never); });
  expect(screen.queryAllByText('#1111')).toHaveLength(0);
  const calls = vi.mocked(getEventRatings).mock.calls.length;
  fireEvent.click(screen.getByText('Load event'));
  await waitFor(() => expect(getEventRatings).toHaveBeenCalledTimes(calls + 1));
});
it('clears old team charts, rejects late breakdowns, and uses percentage axes and units', async () => {
  let finish!: (payload: never) => void;
  vi.mocked(getEventRatings).mockResolvedValue(ratings(2222) as never);
  vi.mocked(getTeamBreakdown).mockImplementation(team => team === 'frc1111' ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(breakdown(0.6) as never));
  render(<MemoryRouter initialEntries={['/?event=2026old']}><DataVizPage /></MemoryRouter>);
  const team = screen.getByPlaceholderText('Team number, e.g. 254');
  fireEvent.change(team, { target: { value: '1111' } });
  fireEvent.click(screen.getByRole('tab', { name: 'Team Deep Dive' }));
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.change(team, { target: { value: '2222' } });
  await screen.findByText('70.0%');
  await act(async () => { finish(breakdown(0.1) as never); });
  expect(screen.queryByText('20.0%')).not.toBeInTheDocument();
  expect(screen.getByText('Fuel Rate (fuel/active min)')).toBeInTheDocument();
  // Only what the official per-match record holds; cycle and defense came from video.
  expect(screen.queryByText('Cycle Time (s)')).not.toBeInTheDocument();
  expect(screen.queryByText('Defense Time (s)')).not.toBeInTheDocument();
  vi.mocked(getTeamBreakdown).mockRejectedValue(new Error('offline'));
  fireEvent.change(team, { target: { value: '3333' } });
  await screen.findByText('Could not load team data. Try loading the event again.');
  expect(screen.queryByText('70.0%')).not.toBeInTheDocument();
});

it('uses field-position labels and leaves unavailable radar dimensions empty', async () => {
  const payload = ratings(254);
  payload.ratings[0].pros = [{ label: 'Throughput elite', percentile: 99.53, metric_value: 10 }] as never;
  payload.ratings[0].cons = [{ label: 'Weak autonomous output', percentile: 4.5, metric_value: 0 }] as never;
  vi.mocked(getEventRatings).mockResolvedValue(payload as never);
  vi.mocked(getTeamBreakdown).mockResolvedValue({ ok: true } as never);
  const { container } = render(<MemoryRouter initialEntries={['/?event=2026old']}><DataVizPage /></MemoryRouter>);
  await screen.findByText('#254');
  const autoCell = container.querySelector('tbody tr')?.children[6];
  expect(autoCell?.textContent).toBe('—');
  fireEvent.change(screen.getByPlaceholderText('Team number, e.g. 254'), { target: { value: '254' } });
  fireEvent.click(screen.getByRole('tab', { name: 'Team Deep Dive' }));
  expect(screen.getByText('Top 1%')).toBeInTheDocument();
  expect(screen.getByText('Bottom 5%')).toBeInTheDocument();
  expect(container.querySelector('.dviz-radar-shape')).toBeNull();
  expect(container.querySelectorAll('.dviz-radar-dot')).toHaveLength(3);
  // The radar shows what official results measure: teleop, auto, climb, consistency.
  expect(container.querySelectorAll('.dviz-radar-legend-val')).toHaveLength(4);
  expect(Array.from(container.querySelectorAll('.dviz-radar-legend-val')).filter(node => node.textContent === '—')).toHaveLength(1);
  expect(screen.getByText('Missing scores are shown as gaps.')).toBeInTheDocument();
});
it.each(['ratings', 'breakdown'])('keeps the loading banner until %s also finishes', async pending => {
  let finish!: (payload: never) => void;
  vi.mocked(getEventRatings).mockImplementation(() => pending === 'ratings'
    ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(ratings(254) as never));
  vi.mocked(getTeamBreakdown).mockImplementation(() => pending === 'breakdown'
    ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({ ok: true } as never));
  render(<MemoryRouter initialEntries={['/?event=2026old']}><DataVizPage /></MemoryRouter>);
  fireEvent.change(screen.getByPlaceholderText('Team number, e.g. 254'), { target: { value: '254' } });
  await waitFor(() => expect(getTeamBreakdown).toHaveBeenCalled());
  await act(async () => {});
  expect(screen.getByText('Loading data…')).toBeInTheDocument();
  await act(async () => { finish((pending === 'ratings' ? ratings(254) : { ok: true }) as never); });
  await waitFor(() => expect(screen.queryByText('Loading data…')).not.toBeInTheDocument());
});
