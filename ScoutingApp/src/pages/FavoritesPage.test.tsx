import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FavoritesPage } from './FavoritesPage';
import { saveFavoriteEvents, saveFavoriteTeams } from '../layout/userSettings';

const api = vi.hoisted(() => ({
  getEventSchedule: vi.fn(),
  getEventTeamsIntel: vi.fn(),
  getEventTeamLiveForm: vi.fn(),
  getTeamIntel: vi.fn(),
}));
vi.mock('../api', async (original) => ({ ...(await original<typeof import('../api')>()), ...api }));
vi.mock('../hooks/useLiveRefreshSetting', () => ({ useLiveRefreshSetting: () => 10 }));

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  saveFavoriteEvents(['2026test']);
  saveFavoriteTeams(['frc1']);
  api.getEventSchedule.mockReset().mockResolvedValue({ matches: [], event_name: 'Test' });
  api.getEventTeamsIntel.mockReset().mockResolvedValue({ teams: [], warnings: [] });
  api.getEventTeamLiveForm.mockReset().mockResolvedValue({ team_statuses: {} });
  api.getTeamIntel.mockReset().mockResolvedValue({ team: { team_number: 1, nickname: 'Team' }, analysis: {} });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function showFavorites() {
  return render(
    <MemoryRouter initialEntries={['/favorites?tab=events']}>
      <FavoritesPage />
    </MemoryRouter>,
  );
}

describe('favorites polling', () => {
  it('fetches only visible data and immediately refreshes the newly selected tab', async () => {
    showFavorites();
    await act(async () => {});
    expect(api.getEventSchedule).toHaveBeenCalledTimes(1);
    expect(api.getTeamIntel).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(api.getEventSchedule).toHaveBeenCalledTimes(2);
    expect(api.getTeamIntel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', { name: 'Teams' }));
    await act(async () => {});
    expect(api.getTeamIntel).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(api.getTeamIntel).toHaveBeenCalledTimes(2);
    expect(api.getEventSchedule).toHaveBeenCalledTimes(2);
  });

  it('backs off when an event request fails instead of reporting success', async () => {
    api.getEventSchedule.mockRejectedValue(new Error('No signal'));
    showFavorites();
    await act(async () => {});
    expect(api.getEventSchedule).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(api.getEventSchedule).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(api.getEventSchedule).toHaveBeenCalledTimes(2);
  });
});

it('loads an added favorite immediately and shows it while loading', async () => {
  let finish!: (value: never) => void;
  api.getTeamIntel.mockImplementation((key: string) => key === 'frc2'
    ? new Promise(resolve => { finish = resolve; })
    : Promise.resolve({team:{team_number:1,nickname:'One'},analysis:{}}));
  render(<MemoryRouter initialEntries={['/favorites?tab=teams']}><FavoritesPage /></MemoryRouter>);
  await act(async () => {});
  fireEvent.change(screen.getByPlaceholderText('Team key or number'),{target:{value:'2'}});
  fireEvent.click(screen.getByRole('button',{name:'Add Team'}));
  expect(screen.getByText('Loading team 2…')).toBeInTheDocument();
  await act(async () => {});
  expect(api.getTeamIntel).toHaveBeenCalledWith('frc2', expect.anything(), expect.anything());
  await act(async () => { finish({team:{team_number:2,nickname:'Two'},analysis:{}} as never); });
  expect(screen.queryByText('Loading team 2…')).not.toBeInTheDocument();
  expect(screen.getByText(/#2 Two/)).toBeInTheDocument();
});

it('preserves Auto-select and an active event outside the team registrations', async () => {
  localStorage.setItem('scouting_center_event_key', '2026active');
  api.getTeamIntel.mockResolvedValue({team:{team_number:1},analysis:{}, competitions:{registered_events_count:1,registered_events:[{event_key:'2026other',name:'Other',start_date:'2026-01-01'}]}});
  render(<MemoryRouter initialEntries={['/favorites?tab=teams']}><FavoritesPage /></MemoryRouter>);
  await act(async () => {});
  const context = screen.getByLabelText('Event for team ratings');
  fireEvent.change(context,{target:{value:''}});
  await act(async () => {});
  expect(context).toHaveValue('');
  fireEvent.click(screen.getByRole('button',{name:'Use Active'}));
  await act(async () => {});
  expect(context).toHaveValue('2026active');
});

it('shows unavailable event counts on an initial failure', async () => {
  api.getEventSchedule.mockRejectedValue(new Error('offline'));
  api.getEventTeamsIntel.mockRejectedValue(new Error('offline'));
  showFavorites();
  await act(async () => {});
  expect(screen.getByText('Matches unavailable')).toBeInTheDocument();
  expect(screen.getByText('Teams unavailable')).toBeInTheDocument();
  expect(screen.queryByText('0 matches')).not.toBeInTheDocument();
  expect(screen.queryByText('0 teams')).not.toBeInTheDocument();
  expect(screen.queryByText('Schedule not published yet.')).not.toBeInTheDocument();
});
it('retains successful event counts and schedule after a partial refresh failure', async () => {
  api.getEventSchedule.mockResolvedValue({ event_name: 'Test', matches: [{
    match_key: '2026test_qm1', display_name: 'Qual 1', is_completed: false, scheduled_time: Date.now() / 1000 + 3600,
  }] });
  api.getEventTeamsIntel.mockResolvedValue({ teams_count: 6 });
  showFavorites();
  await act(async () => {});
  expect(screen.getByText('1 match')).toBeInTheDocument();
  expect(screen.getByText('6 teams')).toBeInTheDocument();
  api.getEventSchedule.mockRejectedValue(new Error('offline'));
  api.getEventTeamsIntel.mockResolvedValue({ teams_count: 7 });
  await act(async () => { vi.advanceTimersByTime(10_000); });
  expect(screen.getByText('1 match')).toBeInTheDocument();
  expect(screen.getByText('7 teams')).toBeInTheDocument();
  expect(screen.getByText(/Next match: Qual 1/)).toBeInTheDocument();
  expect(screen.getByText('Showing saved data; it may be out of date.')).toBeInTheDocument();
  expect(screen.queryByText('All published matches are complete.')).not.toBeInTheDocument();
});
it('shows unavailable team counts on failure and preserves a successful snapshot across repeated failures', async () => {
  api.getTeamIntel.mockRejectedValue(new Error('offline'));
  render(<MemoryRouter initialEntries={['/favorites?tab=teams']}><FavoritesPage /></MemoryRouter>);
  await act(async () => {});
  expect(screen.getByText('Events unavailable')).toBeInTheDocument();
  expect(screen.queryByText('0 events')).not.toBeInTheDocument();
  api.getTeamIntel.mockResolvedValue({ team: { team_number: 1 }, analysis: {}, competitions: { registered_events_count: 1 } });
  await act(async () => { vi.advanceTimersByTime(15_000); });
  expect(screen.getByText('1 event')).toBeInTheDocument();
  api.getTeamIntel.mockRejectedValue(new Error('offline'));
  await act(async () => { vi.advanceTimersByTime(10_000); });
  await act(async () => { vi.advanceTimersByTime(15_000); });
  expect(screen.getByText('1 event')).toBeInTheDocument();
  expect(screen.getByText('Team data unavailable. Try refreshing again. Showing saved data; it may be out of date.')).toHaveAttribute('role', 'alert');
});
it.each([false, true])('distinguishes empty from completed schedules (completed=%s)', async completed => {
  api.getEventSchedule.mockResolvedValue({ event_name: 'Test', matches: completed ? [{ is_completed: true, scheduled_time: Date.now() / 1000, match_key: '2026test_qm1' }] : [] });
  showFavorites();
  await act(async () => {});
  expect(screen.getByText(completed ? 'All published matches are complete.' : 'Schedule not published yet.')).toBeInTheDocument();
});
