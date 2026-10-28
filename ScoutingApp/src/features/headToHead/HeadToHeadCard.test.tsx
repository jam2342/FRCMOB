import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getTeamHeadToHead, type HeadToHeadResponse } from '../../api';
import { HeadToHeadCard } from './HeadToHeadCard';
import { recordLine } from './headToHead';

vi.mock('../../api', () => ({ getTeamHeadToHead: vi.fn() }));

const history = (matches: HeadToHeadResponse['matches']): HeadToHeadResponse => ({
  ok: true, team_key: 'frc254', other_key: 'frc1678',
  together: { played: 3, wins: 2, losses: 1, ties: 0 },
  against: { played: 1, wins: 0, losses: 0, ties: 1 },
  seasons: [2026], match_count: matches.length, matches,
});
const match = (n: number, relation: 'together' | 'against', result: 'win' | 'loss' | 'tie' | null): HeadToHeadResponse['matches'][number] => ({
  match_key: `2026cmptx_qm${n}`, event_key: '2026cmptx', event_name: 'Championship', season: 2026, comp_level: 'qm',
  relation, alliance: 'red', score: 120, opponent_score: 98, result, time: n,
});

afterEach(() => vi.mocked(getTeamHeadToHead).mockReset());

describe('HeadToHeadCard', () => {
  it('shows records with and against, then the matches', async () => {
    vi.mocked(getTeamHeadToHead).mockResolvedValue(history([match(9, 'together', 'win'), match(4, 'against', 'tie'), match(2, 'together', null)]));
    render(<HeadToHeadCard teamKeys={['frc254', 'frc1678']} />);
    expect(await screen.findByText('2–1')).toBeInTheDocument();
    expect(screen.getByText('2 wins, 1 loss in 3 matches')).toBeInTheDocument();
    expect(screen.getByText('0–0–1')).toBeInTheDocument();
    expect(screen.getByText('#254 won')).toBeInTheDocument();
    expect(screen.getByText('Tie')).toBeInTheDocument();
    expect(screen.getByText('Not played')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Which two' })).not.toBeInTheDocument();
  });

  it('with three teams, offers each pair', async () => {
    vi.mocked(getTeamHeadToHead).mockResolvedValue(history([]));
    render(<HeadToHeadCard teamKeys={['frc254', 'frc1678', 'frc118']} />);
    expect(await screen.findByText('#254 and #1678 have never played in the same match.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: '#1678 & #118' }));
    await vi.waitFor(() => expect(getTeamHeadToHead).toHaveBeenLastCalledWith('frc1678', 'frc118'));
  });
});

describe('recordLine', () => {
  it('words a record', () => {
    expect(recordLine({ played: 1, wins: 1, losses: 0, ties: 0 })).toBe('1 win, 0 losses in 1 match');
    expect(recordLine({ played: 4, wins: 1, losses: 1, ties: 2 })).toBe('1 win, 1 loss, 2 ties in 4 matches');
    expect(recordLine({ played: 0, wins: 0, losses: 0, ties: 0 })).toBe('No played matches');
  });
});
