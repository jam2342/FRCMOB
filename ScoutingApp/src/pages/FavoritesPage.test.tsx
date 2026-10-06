import { act, fireEvent, render, screen } from '@testing-library/react';
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
