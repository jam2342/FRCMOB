import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { getEventForecast } from '../../api';
import { EventForecast } from './EventForecast';

vi.mock('../../api', () => ({ getEventForecast: vi.fn() }));

it('lists each team with its chances, rounding the near-certain ones', async () => {
  vi.mocked(getEventForecast).mockResolvedValue({
    ok: true, event_key: '2026arc', runs: 2000, quals_played: 40, quals_remaining: 20, playoffs_started: false, simulated: true,
    teams: [
      { team_key: 'frc254', current_rank: 1, ranking_score: 3.4, matches_played: 8, points_per_match: 320, expected_rank: 1.2, top8: 0.999, captain: 0.99, on_alliance: 1, win_event: 0.41, expected_rp: 40 },
      { team_key: 'frc9999', current_rank: 30, ranking_score: 0.5, matches_played: 8, points_per_match: 20, expected_rank: 29.5, top8: 0.001, captain: 0, on_alliance: 0.2, win_event: 0, expected_rp: 5 },
    ],
  });
  render(<EventForecast eventKey="2026arc" onOpenTeam={() => {}} />);
  expect(await screen.findByText('#254')).toBeInTheDocument();
  expect(screen.getByText('20 qualification matches left, played 2,000 times')).toBeInTheDocument();
  expect(screen.getByText('>99%')).toBeInTheDocument();
  expect(screen.getByText('<1%')).toBeInTheDocument();
  expect(screen.getByText('41%')).toBeInTheDocument();
});

it('shows how a finished event ended instead of a simulation', async () => {
  vi.mocked(getEventForecast).mockResolvedValue({
    ok: true, event_key: '2026mndu', simulated: false, finished: true, winners: ['frc3100', 'frc2847', 'frc3926'], finalists: ['frc3297', 'frc5348', 'frc4624'],
    teams: [
      { team_key: 'frc3100', current_rank: 1, ranking_score: 3.5, matches_played: 11, points_per_match: 136, won_event: true, finalist: false },
      { team_key: 'frc3297', current_rank: 2, ranking_score: 3.5, matches_played: 11, points_per_match: 68, won_event: false, finalist: true },
    ],
  });
  render(<EventForecast eventKey="2026mndu" onOpenTeam={() => {}} />);
  expect(await screen.findByText('Final result')).toBeInTheDocument();
  expect(screen.getByText(/#3100 · #2847 · #3926/)).toBeInTheDocument();
  expect(screen.getByText('Won')).toBeInTheDocument();
  expect(screen.getByText('Finalist')).toBeInTheDocument();
});

it('says a season without ratings has no forecast', async () => {
  vi.mocked(getEventForecast).mockResolvedValue({ ok: true, event_key: '2025mndu', simulated: false, unavailable: 'season_not_rated', teams: [] });
  render(<EventForecast eventKey="2025mndu" onOpenTeam={() => {}} />);
  expect(await screen.findByText('No forecast for this season')).toBeInTheDocument();
});
