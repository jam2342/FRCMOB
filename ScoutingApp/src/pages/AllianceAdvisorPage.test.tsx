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
import { AllianceAdvisorPage } from './AllianceAdvisorPage';
import { getAllianceOutlook, getEventTeamsIntel, getSelectionModel } from '../api';
vi.mock('../api', () => ({ getEventTeamsIntel: vi.fn(), getSelectionModel: vi.fn(), getAllianceOutlook: vi.fn(), getPickValues: vi.fn(() => Promise.resolve({ ok: true, event_key: '', partners: [], assumed_third: null, reference: [], candidates: [] })) }));
function teams(number: number) { return { teams: Array.from({ length: 6 }, (_, i) => ({ team_key: `frc${number+i}`, team_number: number+i })) }; }
it('drops old team loads and selection results after model inputs change', async () => {
  let finishTeams!: (payload: never) => void;
  let finishModel!: (payload: never) => void;
  vi.mocked(getEventTeamsIntel).mockImplementation(key => key === '2026old' ? new Promise(resolve => { finishTeams = resolve; }) : Promise.resolve(teams(2000) as never));
  vi.mocked(getSelectionModel).mockImplementation(() => new Promise(resolve => { finishModel = resolve; }));
  render(<MemoryRouter initialEntries={['/?event=2026old']}><AllianceAdvisorPage /></MemoryRouter>);
  await waitFor(() => expect(finishTeams).toBeDefined());
  fireEvent.click(screen.getByText('New event'));
  await waitFor(() => expect(screen.getByText('Run Selection Model')).toBeEnabled());
  await act(async () => { finishTeams(teams(1000) as never); });
  fireEvent.click(screen.getByText('Run Selection Model'));
  await waitFor(() => expect(finishModel).toBeDefined());
  expect(vi.mocked(getSelectionModel).mock.calls.at(-1)?.[0]).toBe('2026new');
  fireEvent.click(screen.getByRole('button', { name: /Increase simulations/i }));
  await act(async () => { finishModel({ selection_model: null } as never); });
  expect(screen.queryByText('The selection model was not returned. The event may not have enough data.')).not.toBeInTheDocument();
});
it('drops alliance analysis after a slot changes', async () => {
  let finish!: (payload: never) => void;
  vi.mocked(getEventTeamsIntel).mockResolvedValue(teams(2000) as never);
  vi.mocked(getAllianceOutlook).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<MemoryRouter initialEntries={['/?event=2026new&teams=frc2000,frc2001,frc2002']}><AllianceAdvisorPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analyze Alliance' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Analyze Alliance' }));
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'frc2003' } });
  await act(async () => { finish({ alliance: { team_keys: [] } } as never); });
  expect(screen.queryByText('Alliance Analysis')).not.toBeInTheDocument();
});
it('shows the alliance outlook with plain strengths and risks', async () => {
  vi.mocked(getEventTeamsIntel).mockResolvedValue(teams(2000) as never);
  vi.mocked(getAllianceOutlook).mockResolvedValue({
    ok: true,
    event_key: '2026new',
    alliance_points: 310,
    alliance_points_range_80: [270, 350],
    vs_strong: { teams: ['frc2003', 'frc2004', 'frc2005'], points: 330, win_prob: 0.41 },
    vs_typical: { teams: ['frc2003', 'frc2004', 'frc2005'], points: 250, win_prob: 0.83 },
    bonus_rp_chances: { energized: 0.9 },
    team_count: 6,
    teams: ['frc2000', 'frc2001', 'frc2002'].map((team_key, i) => ({
      team_key, rating_0_100: 90 - i, points_per_match: 110 - i, climb_points: 5, dud_rate: 0.02, event_rank_by_points: i + 1,
      strengths: i === 0 ? ['Throughput elite'] : [], risks: i === 0 ? ['Weak autonomous output'] : [],
    })),
  } as never);
  render(<MemoryRouter initialEntries={['/?event=2026new&teams=frc2000,frc2001,frc2002']}><AllianceAdvisorPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analyze Alliance' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Analyze Alliance' }));
  // The default 1 s ran out on a loaded CI runner (1.1 s on 2026-10-18).
  await screen.findByText('Beats a strong alliance', undefined, { timeout: 5000 });
  expect(vi.mocked(getAllianceOutlook).mock.calls.at(-1)?.[1]).toEqual(['frc2000', 'frc2001', 'frc2002']);
  expect(screen.getByText('41%')).toBeInTheDocument();
  expect(screen.getByText('83%')).toBeInTheDocument();
  expect(screen.getByText('Elite scorer')).toBeInTheDocument();
  expect(screen.getByText('Weak autonomous')).toBeInTheDocument();
  expect(screen.getByText('1 of 6')).toBeInTheDocument();
});
