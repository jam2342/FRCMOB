import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { loadTeamCenterIntel } from '../features/prefetch/pageRequests';
import { TeamCenterPage } from './TeamCenterPage';

vi.mock('../api', async importOriginal => ({
  ...await importOriginal<typeof import('../api')>(),
  isClientAdminModeEnabled: () => false,
  getEventSchedule: vi.fn(async () => ({ matches: [] })),
  getEventTeamLiveForm: vi.fn(async () => ({})),
  getEventTeamsIntel: vi.fn(async () => ({ teams: [] })),
  getTeamRobotImage: vi.fn(async () => ({})),
}));
vi.mock('../features/prefetch/pageRequests', () => ({
  loadTeamCenterIntel: vi.fn(),
  loadTeamCenterLogo: vi.fn(async () => ({})),
}));
vi.mock('../features/seasons/RatingHistoryCard', () => ({ RatingHistoryCard: () => null }));
vi.mock('../features/seasonStory/SeasonStoryCard', () => ({ SeasonStoryCard: () => null }));

beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it.each([
  { phone: false, tab: 'overview' },
  { phone: true, tab: 'overview' },
  { phone: true, tab: 'performance' },
])('uses a dash while loading and without climb data ($phone, $tab)', async ({ phone, tab }) => {
  vi.stubGlobal('matchMedia', () => ({ matches: phone, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  let finish: (value: never) => void = () => {};
  vi.mocked(loadTeamCenterIntel).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<MemoryRouter initialEntries={[`/team-center?team=frc254&tab=${tab}`]}><TeamCenterPage /></MemoryRouter>);
  await waitFor(() => expect(loadTeamCenterIntel).toHaveBeenCalled());
  const tile = screen.queryByText('Best Climb Level')?.closest('article');
  if (tab === 'overview') expect(tile?.querySelector('strong')).toHaveTextContent('—');
  expect(screen.queryByText('No Level Data')).not.toBeInTheDocument();
  await act(async () => { finish({ team: { team_number: 254 }, analysis: { averages: {}, recent_matches: [] } } as never); });
  expect(screen.getByText('Best Climb Level').closest('article')?.querySelector('strong')).toHaveTextContent('—');
});

it('keeps a real climb level after loading', async () => {
  vi.mocked(loadTeamCenterIntel).mockResolvedValue({ team: { team_number: 254 }, analysis: {
    averages: {}, climb_sources: { level_capability: { best_level: 'level1', best_level_label: 'Level 1' } },
  } } as never);
  render(<MemoryRouter initialEntries={['/team-center?team=frc254']}><TeamCenterPage /></MemoryRouter>);
  await screen.findByText('Level 1');
});
