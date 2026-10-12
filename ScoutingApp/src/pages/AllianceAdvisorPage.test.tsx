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
import { getEventTeamsIntel, getTheoreticalAlliance } from '../api';
vi.mock('../api', () => ({ getEventTeamsIntel: vi.fn(), getTheoreticalAlliance: vi.fn() }));
function teams(number: number) { return { teams: Array.from({ length: 6 }, (_, i) => ({ team_key: `frc${number+i}`, team_number: number+i })) }; }
it('drops old team loads and selection results after model inputs change', async () => {
  let finishTeams!: (payload: never) => void;
  let finishModel!: (payload: never) => void;
  vi.mocked(getEventTeamsIntel).mockImplementation(key => key === '2026old' ? new Promise(resolve => { finishTeams = resolve; }) : Promise.resolve(teams(2000) as never));
  vi.mocked(getTheoreticalAlliance).mockImplementation(() => new Promise(resolve => { finishModel = resolve; }));
  render(<MemoryRouter initialEntries={['/?event=2026old']}><AllianceAdvisorPage /></MemoryRouter>);
  await waitFor(() => expect(finishTeams).toBeDefined());
  fireEvent.click(screen.getByText('New event'));
  await waitFor(() => expect(screen.getByText('Run Selection Model')).toBeEnabled());
  await act(async () => { finishTeams(teams(1000) as never); });
  fireEvent.click(screen.getByText('Run Selection Model'));
  await waitFor(() => expect(finishModel).toBeDefined());
  expect(vi.mocked(getTheoreticalAlliance).mock.calls.at(-1)?.[1].team_keys).toEqual(['frc2000', 'frc2001', 'frc2002']);
  fireEvent.click(screen.getByRole('button', { name: /Increase simulations/i }));
  await act(async () => { finishModel({ selection_model: null } as never); });
  expect(screen.queryByText('The selection model was not returned. The event may not have enough data.')).not.toBeInTheDocument();
});
it('drops alliance analysis after a slot changes', async () => {
  let finish!: (payload: never) => void;
  vi.mocked(getEventTeamsIntel).mockResolvedValue(teams(2000) as never);
  vi.mocked(getTheoreticalAlliance).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<MemoryRouter initialEntries={['/?event=2026new&teams=frc2000,frc2001,frc2002']}><AllianceAdvisorPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analyze Alliance' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Analyze Alliance' }));
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'frc2003' } });
  await act(async () => { finish({ alliance: { team_keys: [] } } as never); });
  expect(screen.queryByText('Alliance Analysis')).not.toBeInTheDocument();
});
it('uses readable sources and signals and omits unlabeled objects', async () => {
  vi.mocked(getEventTeamsIntel).mockResolvedValue(teams(2000) as never);
  vi.mocked(getTheoreticalAlliance).mockResolvedValue({
    weighted_total_score_0_100: 80,
    compatibility: { confidence_0_1: 0.8, compatibility_score_0_100: 80, alliance_synergy_points: 8,
      pair_breakdown: ['projection', 'blended', 'measured'].map(source => ({ team_key_a: 'frc2000', team_key_b: 'frc2001', source })),
    },
    pros_cons: {},
    teams: [{ team_key: 'frc2000', pros_top: [{ label: 'Throughput elite' }, { secret_field: 'hidden-value' }], cons_top: [{ label: 'Weak autonomous output' }, { label: '' }] }],
  } as never);
  render(<MemoryRouter initialEntries={['/?event=2026new&teams=frc2000,frc2001,frc2002']}><AllianceAdvisorPage /></MemoryRouter>);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analyze Alliance' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Analyze Alliance' }));
  await screen.findByText('Elite scorer');
  expect(screen.getByText('Weak autonomous')).toBeInTheDocument();
  for (const label of ['Estimated', 'Results + estimates', 'Scouted']) expect(screen.getByText(label)).toBeInTheDocument();
  expect(screen.queryByText(/hidden-value|secret_field|projection|blended/)).not.toBeInTheDocument();
});
