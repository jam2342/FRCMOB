import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { getEventTeamsIntel, getTeamIntel } from '../api';
import { ComparePage } from './ComparePage';

vi.mock('../api', () => ({
  getEventTeamsIntel: vi.fn(),
  getTeamIntel: vi.fn(),
  searchTeams: vi.fn(async () => ({ teams: [] })),
  getTeamHeadToHead: vi.fn(async () => ({ ok: true, team_key: '', other_key: '', together: { played: 0, wins: 0, losses: 0, ties: 0 }, against: { played: 0, wins: 0, losses: 0, ties: 0 }, seasons: [], match_count: 0, matches: [] })),
}));

function intelFor(nickname: string) {
  return {
    team: { team_number: 254, nickname },
    analysis: { averages: {}, matches_analyzed: 3 },
    rating: { rating_0_100: 80 },
  };
}

function GoTo({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(to)}>{`go ${to}`}</button>;
}

function renderCompare(initial: string) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <GoTo to="/compare?event=2026aaa&teams=frc254" />
      <GoTo to="/compare?event=2026bbb&teams=frc254" />
      <ComparePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(getEventTeamsIntel).mockResolvedValue({ teams: [], teams_count: 0 } as never);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('opens a shared link with its teams', async () => {
  vi.mocked(getTeamIntel).mockResolvedValue(intelFor('The Cheesy Poofs') as never);
  renderCompare('/compare?event=2026aaa&teams=frc254,1678');
  await waitFor(() => expect(getTeamIntel).toHaveBeenCalledWith('frc254', expect.objectContaining({ event_key: '2026aaa' }), expect.anything()));
  expect(getTeamIntel).toHaveBeenCalledWith('frc1678', expect.anything(), expect.anything());
});

it("never shows another event's numbers after returning to an event whose stale load failed", async () => {
  let failA: (error: Error) => void = () => {};
  vi.mocked(getTeamIntel).mockImplementation(async (_team, options) => {
    if ((options as { event_key?: string }).event_key === '2026aaa' && vi.mocked(getTeamIntel).mock.calls.length === 1) {
      return new Promise((_resolve, reject) => { failA = reject; }) as never;
    }
    const event = (options as { event_key?: string }).event_key;
    return intelFor(event === '2026bbb' ? 'From B' : 'From A') as never;
  });
  renderCompare('/compare?event=2026aaa&teams=frc254');
  await waitFor(() => expect(getTeamIntel).toHaveBeenCalledTimes(1));

  fireEvent.click(screen.getByRole('button', { name: 'go /compare?event=2026bbb&teams=frc254' }));
  await screen.findAllByText('From B');
  await act(async () => { failA(new Error('offline')); });

  fireEvent.click(screen.getByRole('button', { name: 'go /compare?event=2026aaa&teams=frc254' }));
  await waitFor(() => expect(getTeamIntel).toHaveBeenCalledTimes(3));
  await screen.findAllByText('From A');
  expect(screen.queryByText('From B')).toBeNull();
});
