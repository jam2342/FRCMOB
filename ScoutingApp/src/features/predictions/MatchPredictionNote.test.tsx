import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { PredictedScheduleMatch } from '../../api';
import { MatchPredictionNote } from './MatchPredictionNote';

function match(red: number | null): PredictedScheduleMatch {
  return {
    match_key: '2026x_qm1',
    red: { teams: [] },
    blue: { teams: [] },
    prediction: red == null ? { available: false } : { available: true, red_win_prob: red, blue_win_prob: 1 - red, source_label: 'team_model_corrected_v4' },
  } as unknown as PredictedScheduleMatch;
}

describe('MatchPredictionNote', () => {
  afterEach(cleanup);

  it('names the favourite before the match', () => {
    render(<MatchPredictionNote match={match(0.2149)} completed={false} winner={null} />);
    expect(screen.getByTestId('match-prediction-note')).toHaveTextContent('Blue favoured · 79% to win');
  });

  it('says whether it called a finished match', () => {
    const { rerender } = render(<MatchPredictionNote match={match(0.7)} completed winner="red" />);
    expect(screen.getByTestId('match-prediction-note')).toHaveTextContent('We gave Red 70% · called it');
    rerender(<MatchPredictionNote match={match(0.7)} completed winner="blue" />);
    expect(screen.getByTestId('match-prediction-note')).toHaveTextContent('We gave Red 70% · upset');
    rerender(<MatchPredictionNote match={match(0.51)} completed winner="blue" />);
    expect(screen.getByTestId('match-prediction-note')).toHaveTextContent('We called it about 50/50');
  });

  it('renders nothing without a prediction', () => {
    render(<MatchPredictionNote match={match(null)} completed={false} winner={null} />);
    expect(screen.queryByTestId('match-prediction-note')).toBeNull();
  });
});
