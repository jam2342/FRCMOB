import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getTeamSeasonStory, type SeasonStoryResponse } from '../../api';
import { SeasonStoryCard } from './SeasonStoryCard';
import { bestMatchText, finishText, rankText, recordText } from './story';

vi.mock('../../api', () => ({ getTeamSeasonStory: vi.fn() }));
vi.mock('./renderRecapCard', () => ({ renderRecapCard: vi.fn(async () => new Blob(['png'], { type: 'image/png' })) }));

const STORY: SeasonStoryResponse = {
  ok: true, team_key: 'frc3100', nickname: 'Lightning Turtles', season: 2026, game: 'REBUILT',
  events: [
    { event_key: '2026mndu', event_name: 'Lake Superior Regional', start: 1, qual_rank: 2, field_size: 40, record: { wins: 11, losses: 3, ties: 0 },
      best_match: { match_key: '2026mndu_f1m2', alliance: 'red', score: 188, opponent_score: 150, result: 'win' }, finish: 'won', rating_0_100: 83.2, points_per_match: 60 },
    { event_key: '2026mnmi', event_name: 'Minnesota North Star', start: 2, qual_rank: 9, field_size: 50, record: { wins: 6, losses: 5, ties: 1 },
      best_match: null, finish: 'quals', rating_0_100: null, points_per_match: null },
  ],
  totals: { wins: 17, losses: 8, ties: 1, events: 2, event_wins: 1, best_match: { match_key: '2026mndu_f1m2', alliance: 'red', score: 188, opponent_score: 150, result: 'win', event_key: '2026mndu' } },
};

afterEach(() => vi.mocked(getTeamSeasonStory).mockReset());

describe('SeasonStoryCard', () => {
  it('tells the season event by event, with totals, and opens an event', async () => {
    vi.mocked(getTeamSeasonStory).mockResolvedValue(STORY);
    const open = vi.fn();
    render(<SeasonStoryCard teamKey="frc3100" onOpenEvent={open} />);
    expect(await screen.findByText('Won the event · 11–3')).toBeInTheDocument();
    expect(screen.getByText('Qual rank 2nd of 40').parentElement).toHaveTextContent('Qual rank 2nd of 40 · Rating 83.2 · Best F 1-2 · 188–150');
    expect(screen.getByText('Ranked 9th of 50 · 6–5–1')).toBeInTheDocument();
    expect(screen.getByText('2 events · 17–8–1').parentElement).toHaveTextContent('2 events · 17–8–1 · 1 event win · season best F 1-2 · 188–150 at Lake Superior Regional');
    fireEvent.click(screen.getByRole('button', { name: 'Minnesota North Star' }));
    expect(open).toHaveBeenCalledWith('2026mnmi');
  });

  it('shares an event as a picture', async () => {
    vi.mocked(getTeamSeasonStory).mockResolvedValue(STORY);
    render(<SeasonStoryCard teamKey="frc3100" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Share Lake Superior Regional recap' }));
    expect(await screen.findByRole('dialog', { name: 'Share this event' })).toBeInTheDocument();
    expect(await screen.findByAltText('Picture of Team 3100 at Lake Superior Regional: Won the event, 11–3.')).toBeInTheDocument();
  });

  it('is not drawn before the team has played this season', async () => {
    vi.mocked(getTeamSeasonStory).mockResolvedValue({ ...STORY, events: [], totals: { ...STORY.totals, events: 0 } });
    const { container } = render(<SeasonStoryCard teamKey="frc3100" />);
    await vi.waitFor(() => expect(getTeamSeasonStory).toHaveBeenCalled());
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});

describe('story wording', () => {
  const base = STORY.events[1];
  it('names the finish, the rank and the best match', () => {
    expect(finishText({ ...base, finish: 'finalist' })).toBe('Finalists');
    expect(finishText({ ...base, finish: 'playoffs' })).toBe('Made the playoffs');
    expect(finishText({ ...base, qual_rank: null })).toBe('Played quals');
    expect(finishText({ ...base, field_size: null })).toBe('Ranked 9th');
    expect(rankText({ ...base, field_size: null })).toBeNull();
    expect(recordText({ wins: 3, losses: 0, ties: 0 })).toBe('3–0');
    expect(bestMatchText({ match_key: '2026x_qm4', alliance: 'blue', score: 90, opponent_score: null, result: 'win' })).toBe('QM 4 · 90');
  });
});
