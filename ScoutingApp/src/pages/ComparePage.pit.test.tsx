import { cleanup, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const workspaceState = vi.hoisted(() => ({ value: { workspace: { id: 3 } } as unknown }));
vi.mock('../features/workspace/useWorkspace', () => ({ useWorkspace: () => workspaceState.value }));
vi.mock('../api', () => ({
  getEventTeamsIntel: vi.fn(async () => ({ teams: [], teams_count: 0 })),
  getTeamIntel: vi.fn(async (teamKey: string) => ({
    team: { team_number: Number(teamKey.slice(3)), nickname: teamKey },
    analysis: { averages: {}, matches_analyzed: 3 },
    rating: { rating_0_100: 80 },
  })),
  searchTeams: vi.fn(async () => ({ teams: [] })),
  getTeamHeadToHead: vi.fn(async () => ({ ok: true, team_key: '', other_key: '', together: { played: 0, wins: 0, losses: 0, ties: 0 }, against: { played: 0, wins: 0, losses: 0, ties: 0 }, seasons: [], match_count: 0, matches: [] })),
  listPitEntries: vi.fn(async () => ({
    ok: true,
    entries: [
      { id: 1, event_key: '2026arc', team_key: 'frc254', scout_profile: null, payload: { drivetrain: 'Swerve', claimed_climb: 'Level 3' }, photos: [], created_at: null, updated_at: null },
    ],
  })),
}));

import { ComparePage } from './ComparePage';
import { resetPitEntriesCache } from '../features/pit/usePitEntries';

beforeEach(() => {
  localStorage.clear();
  resetPitEntriesCache();
  workspaceState.value = { workspace: { id: 3 } };
});
afterEach(cleanup);

function mount() {
  render(
    <MemoryRouter initialEntries={['/compare?event=2026arc&teams=frc254,frc1678']}>
      <ComparePage />
    </MemoryRouter>,
  );
}

it('compares pit answers side by side, with a dash where a team has none', async () => {
  mount();
  const card = (await screen.findByRole('heading', { name: 'Pit scouting' })).closest('section') as HTMLElement;
  const row = within(card).getByText('Drivetrain').closest('tr') as HTMLElement;
  expect(within(row).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['Swerve', '—']);
  expect(within(card).getByText('Claimed climb')).toBeTruthy();
  // Nobody answered the language question, so it is not a row.
  expect(within(card).queryByText('Language')).toBeNull();
});

it('has no pit group outside a workspace', async () => {
  workspaceState.value = null;
  mount();
  await screen.findAllByText(/#254|254/);
  expect(screen.queryByRole('heading', { name: 'Pit scouting' })).toBeNull();
});
