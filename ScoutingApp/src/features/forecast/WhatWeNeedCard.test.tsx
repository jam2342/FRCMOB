import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getEventWhatIf, type EventWhatIfResponse } from '../../api';
import { WhatWeNeedCard } from './WhatWeNeedCard';
import { whatIfLists } from './whatIf';

vi.mock('../../api', () => ({ getEventWhatIf: vi.fn() }));

const rank = (expected: number, top8: number) => ({ expected_rank: expected, top8, top4: top8 / 2, rank_likely_best: Math.floor(expected) - 1, rank_likely_worst: Math.ceil(expected) + 2, rank_counts: [] });

function response(whatIfRank = 9.4): EventWhatIfResponse {
  return {
    ok: true, event_key: '2026mndu', team_key: 'frc3100', field_size: 40, runs: 1000,
    matches: [
      { match_key: '2026mndu_qm41', alliance: 'red', partners: ['frc254', 'frc118'], opponents: ['frc1', 'frc2', 'frc3'], model_win_prob: 0.62, forced: null },
      { match_key: '2026mndu_qm55', alliance: 'blue', partners: ['frc7', 'frc8'], opponents: ['frc4', 'frc5', 'frc6'], model_win_prob: 0.4, forced: null },
    ],
    model: rank(9.4, 0.41),
    what_if: rank(whatIfRank, 0.8),
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => { vi.useRealTimers(); vi.mocked(getEventWhatIf).mockReset(); });

async function settle() {
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
}

describe('WhatWeNeedCard', () => {
  it("shows the model's rank, then the rank with the results set", async () => {
    vi.mocked(getEventWhatIf).mockResolvedValue(response());
    render(<WhatWeNeedCard eventKey="2026mndu" teamKey="frc3100" />);
    await settle();
    expect(screen.getByText('9.4')).toBeInTheDocument();
    expect(screen.getByText('Set a match below')).toBeInTheDocument();
    const first = screen.getByRole('group', { name: 'Result of QM 41' });
    expect(within(first).getByRole('radio', { name: 'Model 62%' })).toBeChecked();
    expect(screen.getByText('with 254, 118 · vs 1, 2, 3')).toBeInTheDocument();

    vi.mocked(getEventWhatIf).mockResolvedValue(response(3.2));
    fireEvent.click(within(first).getByRole('radio', { name: 'Win' }));
    fireEvent.click(within(screen.getByRole('group', { name: 'Result of QM 55' })).getByRole('radio', { name: 'Loss' }));
    await settle();
    // Two taps, one question.
    expect(getEventWhatIf).toHaveBeenCalledTimes(2);
    expect(getEventWhatIf).toHaveBeenLastCalledWith('2026mndu', 'frc3100', ['2026mndu_qm41'], ['2026mndu_qm55']);
    expect(screen.getByText('3.2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Win them all' }));
    await settle();
    expect(getEventWhatIf).toHaveBeenLastCalledWith('2026mndu', 'frc3100', ['2026mndu_qm41', '2026mndu_qm55'], []);
    fireEvent.click(screen.getByRole('button', { name: 'Back to the model' }));
    await settle();
    expect(getEventWhatIf).toHaveBeenLastCalledWith('2026mndu', 'frc3100', [], []);
  });

  it("says so when it couldn't update for the latest picks", async () => {
    vi.mocked(getEventWhatIf).mockResolvedValue(response());
    render(<WhatWeNeedCard eventKey="2026mndu" teamKey="frc3100" />);
    await settle();
    vi.mocked(getEventWhatIf).mockRejectedValue(new Error('offline'));
    fireEvent.click(within(screen.getByRole('group', { name: 'Result of QM 41' })).getByRole('radio', { name: 'Win' }));
    await settle();
    expect(screen.getByText(/Couldn't update this for your latest picks/)).toBeInTheDocument();
  });

  it('is not drawn once the quals are over or for a past season', async () => {
    vi.mocked(getEventWhatIf).mockResolvedValue({ ok: true, event_key: 'e', team_key: 'frc1', matches: [], unavailable: 'quals_over' });
    const { container } = render(<WhatWeNeedCard eventKey="2026mndu" teamKey="frc1" />);
    await settle();
    expect(container).toBeEmptyDOMElement();
  });
});

describe('whatIfLists', () => {
  it('splits choices into sorted wins and losses, leaving the model out', () => {
    expect(whatIfLists({ b_qm2: 'loss', a_qm9: 'win', c_qm1: 'model', a_qm3: 'win' })).toEqual({ wins: ['a_qm3', 'a_qm9'], losses: ['b_qm2'] });
  });
});
