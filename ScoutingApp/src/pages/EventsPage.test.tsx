import { cleanup, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { getEventSchedule } from '../api';
import { loadEventsRankings } from '../features/prefetch/pageRequests';
import { EventsPage } from './EventsPage';

vi.mock('../api', async importOriginal => ({
  ...await importOriginal<typeof import('../api')>(),
  clientAdminKeyAvailable: () => false,
  getApiHealth: vi.fn(async () => ({})),
  getEventSchedule: vi.fn(),
  getEventTeamLiveForm: vi.fn(async () => ({})),
  getEventAwards: vi.fn(async () => ({ awards: [] })),
  getEventRatings: vi.fn(async () => ({ teams: [] })),
  getEventAlliances: vi.fn(async () => ({ alliances: [] })),
}));
vi.mock('../features/events/eventCatalog', () => ({
  loadSeasonEventCatalog: vi.fn(async () => []),
  loadSeasonSearchFallback: vi.fn(async () => []),
}));
vi.mock('../features/prefetch/pageRequests', () => ({
  loadEventsRankings: vi.fn(),
  loadEventsTeamsHistory: vi.fn(async () => ({ teams: [] })),
}));
vi.mock('../hooks/useEventNames', () => ({ useEventNames: () => (key: string) => key }));

beforeEach(() => {
  localStorage.clear();
  vi.mocked(getEventSchedule).mockResolvedValue({ matches: [] } as never);
  vi.mocked(loadEventsRankings).mockResolvedValue({ rankings: { rankings: [] } } as never);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

function renderEvent(tab: string) {
  return render(<MemoryRouter initialEntries={[`/events?event=2026test&tab=${tab}`]}><EventsPage /></MemoryRouter>);
}

it('distinguishes a failed schedule from an unpublished one', async () => {
  vi.mocked(getEventSchedule).mockRejectedValue(new Error('offline'));
  renderEvent('schedule');
  await screen.findByText("Couldn't load the schedule");
  expect(screen.getByText('It retries on its own.')).toBeInTheDocument();
  expect(screen.queryByText('Schedule not posted yet')).not.toBeInTheDocument();
});

it('keeps not-posted copy after a successful empty schedule', async () => {
  renderEvent('schedule');
  await screen.findByText('Schedule not posted yet');
});

it.each(['overview', 'rankings'])('uses neutral rankings copy for a finished event on %s', async tab => {
  vi.mocked(getEventSchedule).mockResolvedValue({ matches: [{ match_key: '2026test_f1m1', comp_level: 'f', is_completed: true, red_score: 10, blue_score: 5, red_teams: [], blue_teams: [] }] } as never);
  vi.stubGlobal('matchMedia', () => ({ matches: tab === 'overview', addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  renderEvent(tab);
  await screen.findByText('No qualification rankings');
  expect(screen.getByText("This event didn't publish qualification rankings.")).toBeInTheDocument();
  expect(screen.queryByText('No rankings yet')).not.toBeInTheDocument();
});

it('keeps pre-qualification rankings copy before matches are played', async () => {
  renderEvent('rankings');
  await screen.findByText('No rankings yet');
  expect(screen.getByText('Rankings appear once qualification matches are played.')).toBeInTheDocument();
});

it.each([false, true])('formats ranking stats to one decimal and missing stats as a dash (phone=%s)', async phone => {
  vi.stubGlobal('matchMedia', () => ({ matches: phone, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  vi.mocked(loadEventsRankings).mockResolvedValue({ rankings: { rankings: [
    { team_key: 'frc254', rank: 1, sort_orders: [4, 4.8] },
    { team_key: 'frc118', rank: 2, sort_orders: [] },
  ] } } as never);
  const { container } = renderEvent('rankings');
  await screen.findAllByText('4.0');
  if (phone) {
    const rows = container.querySelectorAll('.fm-standings-row');
    expect(Array.from(rows, row => Array.from(row.children).slice(-2).map(node => node.textContent))).toEqual([['4.0', '4.8'], ['—', '—']]);
  } else {
    const table = screen.getByRole('table');
    expect(within(table).getByText('4.0')).toBeInTheDocument();
    expect(within(table).getByText('4.8')).toBeInTheDocument();
    expect(within(table).getAllByText('—').length).toBeGreaterThanOrEqual(2);
  }
});
