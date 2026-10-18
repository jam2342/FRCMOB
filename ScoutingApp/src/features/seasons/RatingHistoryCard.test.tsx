import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { getTeamRatingHistory } from '../../api';
import { RatingHistoryCard } from './RatingHistoryCard';

vi.mock('../../api', () => ({ getTeamRatingHistory: vi.fn() }));

it('lists each season apart and opens an event', async () => {
  vi.mocked(getTeamRatingHistory).mockResolvedValue({
    ok: true, team_key: 'frc3100', first_rated_season: 2026,
    seasons: [
      { season: 2027, game: null, current: true, frozen: false, events: [
        { event_key: '2027mnwk', event_name: 'Week 2 Minnesota', start: 1, rating_0_100: 74.1, points_per_match: 128, matches_here: 10, rank: 4, field_size: 40, won_event: false },
      ] },
      { season: 2026, game: 'REBUILT', current: false, frozen: true, events: [
        { event_key: '2026mndu', event_name: 'Lake Superior Regional', start: 1, rating_0_100: 83.17, points_per_match: 136, matches_here: 11, rank: 1, field_size: 42, won_event: true },
      ] },
    ],
  });
  const open = vi.fn();
  render(<RatingHistoryCard teamKey="frc3100" onOpenEvent={open} />);
  expect(await screen.findByText('2026 · REBUILT')).toBeInTheDocument();
  expect(screen.getByText('2027')).toBeInTheDocument();
  expect(screen.getByText('final')).toBeInTheDocument();
  expect(screen.getByText('Won')).toBeInTheDocument();
  expect(screen.getByText('Seasons before 2026 have results but no FRCMOB ratings.')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Lake Superior Regional'));
  expect(open).toHaveBeenCalledWith('2026mndu');
});
