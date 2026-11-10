import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ScoutingCoveragePage } from './ScoutingCoveragePage';
import { getScoutingCoverage } from '../api';
vi.mock('../hooks/useOnlineStatus', () => ({ useOnlineStatus: () => ({ online: true, isShowingOfflineData: false }) }));
vi.mock('../api', () => ({ getScoutingCoverage: vi.fn() }));
vi.mock('../features/workspace/WorkspaceGate', () => ({ WorkspaceGate: ({ children }: { children: React.ReactNode }) => children }));
vi.mock('../components/EventPicker', () => ({ EventPicker: () => null }));
afterEach(() => { cleanup(); localStorage.clear(); });

const slot = (team: string, alliance: string, count: number, scouts: string[]) => ({ team_key: `frc${team}`, alliance, station: null, entry_count: count, scouts });
const coverage = {
  summary: { total_entries: 2, scout_count: 1, coverage_pct: 50, covered_slots: 6, total_slots: 12, outlier_count: 0 },
  grid: [
    { match_key: 'm1', label: 'Q1', comp_level: 'qm', time: null, slots: [
      slot('1', 'blue', 1, ['Ana']), slot('2', 'blue', 1, ['Ana']), slot('3', 'blue', 1, ['Ana']),
      slot('4', 'red', 1, ['Ben']), slot('5', 'red', 2, ['Ben', 'Cy']), slot('6', 'red', 1, ['Ben']) ] },
    { match_key: 'm2', label: 'Q2', comp_level: 'qm', time: null, slots: [
      slot('11', 'blue', 0, []), slot('12', 'blue', 1, ['Ana']), slot('13', 'blue', 1, ['Ana']),
      slot('14', 'red', 1, ['Ben']), slot('15', 'red', 1, ['Ben']), slot('16', 'red', 1, ['Ben']) ] },
  ],
  leaderboard: [], outliers: [],
};

function mockPhone() {
  window.matchMedia = ((query: string) => ({
    matches: true, media: query, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  })) as never;
}

it('on a phone shows a card per match with words for covered and missing, gaps first', async () => {
  mockPhone();
  vi.mocked(getScoutingCoverage).mockResolvedValue(coverage as never);
  render(<MemoryRouter initialEntries={['/?event=2026test']}><ScoutingCoveragePage /></MemoryRouter>);
  expect(await screen.findByText('Q2')).toBeInTheDocument();
  expect(screen.queryByText('Q1')).not.toBeInTheDocument();
  expect(screen.getByText('1 missing')).toBeInTheDocument();
  expect(screen.getByText(/Not scouted/)).toBeInTheDocument();
  expect(screen.getAllByText(/Scouted by Ana/).length).toBe(2);
  fireEvent.click(screen.getByRole('tab', { name: /^All/ }));
  const q1 = screen.getByText('Q1').closest('.coverage-card') as HTMLElement;
  expect(within(q1).getByText('All scouted')).toBeInTheDocument();
  expect(within(q1).getByText(/Scouted by Ben, Cy \(2 entries\)/)).toBeInTheDocument();
  // Red is listed before blue even though the payload sends blue first.
  const labels = [...q1.querySelectorAll('.coverage-card-alliance-label')].map((el) => el.textContent);
  expect(labels).toEqual(['Red', 'Blue']);
});
