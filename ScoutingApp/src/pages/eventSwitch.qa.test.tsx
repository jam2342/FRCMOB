// Regression from the 2026-10-04 QA pass.
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getEventSchedule, getEventScheduleWithSynergy, getScoutingCoverage } from '../api';
import { MatchPredictionPage } from './MatchPredictionPage';
import { ScoutingCoveragePage } from './ScoutingCoveragePage';
import { StrategyBriefingPage } from './StrategyBriefingPage';

vi.mock('../api', () => ({
  getEventScheduleWithSynergy: vi.fn(),
  getEventSchedule: vi.fn(async () => ({ matches: [] })),
  getEventPredictions: vi.fn(async () => ({ predictions: null })),
  getScoutingCoverage: vi.fn(),
  getEventRatings: vi.fn(async () => ({ ratings: [] })),
  getEventTeamLiveForm: vi.fn(async () => ({ team_statuses: {} })),
  isClientAdminModeEnabled: () => false,
}));
vi.mock('../components/EventPicker', () => ({
  EventPicker: ({ value, onSelect }: { value: string; onSelect: (key: string) => void }) => <>
    <p data-testid="selected-event">{value}</p>
    <button onClick={() => onSelect('2026new')}>New event</button>
  </>,
}));
vi.mock('../features/workspace/WorkspaceGate', () => ({ WorkspaceGate: ({ children }: { children: React.ReactNode }) => children }));
vi.mock('../components/LiveRatingsPanel', () => ({ LiveRatingsPanel: () => null }));

function prediction(event: string, number: number) {
  const alliance = { teams: [{ team_key: `frc${number}`, team_number: number, station: 'r1' }], synergy: { available: false, pair_breakdown: [] } };
  return { ok: true, event_key: event, matches: [{ match_key: `${event}_qm1`, comp_level: 'qm', set_number: 1, match_number: 1,
    red: alliance, blue: alliance, prediction: { available: true, red_win_prob: 0.9, blue_win_prob: 0.1, source_label: 'deterministic_fuel_v1' } }] };
}
function coverage(event: string, number: number) {
  return { ok: true, event_key: event, summary: { coverage_pct: 100, covered_slots: 1, total_slots: 1, total_entries: 1, scout_count: 1, outlier_count: 0 },
    grid: [{ match_key: `${event}_qm1`, label: 'Q1', slots: [{ team_key: `frc${number}`, alliance: 'red', entry_count: 1, scouts: ['Scout'] }] }], leaderboard: [], outliers: [] };
}

describe('QA: changing events while a request is pending', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); window.localStorage.clear(); });

  it.each(['predictions', 'coverage'])('keeps the new event on the %s page when the old response arrives late', async page => {
    let finishOld!: (value: never) => void;
    const payload = page === 'predictions' ? prediction : coverage;
    const load = (event: string): Promise<never> => event === '2026old'
      ? new Promise<never>(resolve => { finishOld = resolve; })
      : Promise.resolve(payload('2026new', 2222) as never);
    if (page === 'predictions') vi.mocked(getEventScheduleWithSynergy).mockImplementation(load);
    else vi.mocked(getScoutingCoverage).mockImplementation(load);
    render(<MemoryRouter initialEntries={['/?event=2026old']}>
      {page === 'predictions' ? <MatchPredictionPage /> : <ScoutingCoveragePage />}
    </MemoryRouter>);
    await waitFor(() => expect(finishOld).toBeDefined());
    fireEvent.click(screen.getByRole('button', { name: 'New event' }));
    await waitFor(() => expect(screen.getAllByText('2222').length).toBeGreaterThan(0));
    await act(async () => { finishOld(payload('2026old', 1111) as never); });
    expect(screen.getByTestId('selected-event')).toHaveTextContent('2026new');
    expect(screen.queryAllByText('1111')).toHaveLength(0);
    expect(screen.getAllByText('2222').length).toBeGreaterThan(0);
  });

  it('clears the previous strategy event when the newly selected schedule fails', async () => {
    vi.mocked(getEventSchedule).mockImplementation(event => event === '2026old'
      ? Promise.resolve({ matches: [], event_name: 'Old Event' } as never)
      : Promise.reject(new Error('New event schedule unavailable')));
    vi.mocked(getEventScheduleWithSynergy).mockResolvedValue({ matches: [] } as never);
    render(<MemoryRouter initialEntries={['/?event=2026old']}><StrategyBriefingPage /></MemoryRouter>);
    await screen.findByText('Old Event');
    fireEvent.click(screen.getByRole('button', { name: 'New event' }));
    await screen.findByText('New event schedule unavailable');
    expect(screen.getByTestId('selected-event')).toHaveTextContent('2026new');
    expect(screen.queryByText('Old Event')).toBeNull();
  });
});
