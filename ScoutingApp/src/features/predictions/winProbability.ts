import type { PredictedScheduleMatch } from '../../api';

// One answer to "who wins this match" for every page: the server's prediction, which is
// each alliance's expected points from results before the match (the team model the
// rating reads), or a rating-margin fallback when an alliance is incomplete. No
// prediction means no number; pages say so instead of guessing.

export type WinProbabilitySource = 'points_model' | 'rating_model';

export type WinProbability = {
  red: number;
  blue: number;
  source: WinProbabilitySource;
  edgeConfidence: number | null;
};

export function matchWinProbability(match: PredictedScheduleMatch | null | undefined): WinProbability | null {
  const prediction = match?.prediction;
  if (!prediction?.available || typeof prediction.red_win_prob !== 'number') return null;
  const red = prediction.red_win_prob;
  return {
    red,
    blue: typeof prediction.blue_win_prob === 'number' ? prediction.blue_win_prob : 1 - red,
    source: /(^|_)(points|team_model)(_|$)/.test(prediction.source_label || '') ? 'points_model' : 'rating_model',
    edgeConfidence: prediction.edge_confidence_0_1 ?? null,
  };
}
