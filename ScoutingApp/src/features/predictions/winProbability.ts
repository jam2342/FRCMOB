import type { ScheduleWithSynergyMatch } from '../../api';

// One answer to "who wins this match" for every page. The server's prediction
// (ratings, blended with the ML model when that is switched on) comes first; a
// logistic on the synergy-score gap is the fallback when the server has none.

export type WinProbabilitySource = 'ml_model' | 'rating_model' | 'synergy_derived';

export type WinProbability = {
  red: number;
  blue: number;
  source: WinProbabilitySource;
  edgeConfidence: number | null;
};

// A 15-point synergy-score gap moves the odds by a factor of e.
const SYNERGY_LOGISTIC_SCALE = 15;

export function synergyWinProbability(
  redSynergy: number | null | undefined,
  blueSynergy: number | null | undefined,
): number | null {
  if (typeof redSynergy !== 'number' || typeof blueSynergy !== 'number') return null;
  if (!Number.isFinite(redSynergy) || !Number.isFinite(blueSynergy)) return null;
  return 1 / (1 + Math.exp(-(redSynergy - blueSynergy) / SYNERGY_LOGISTIC_SCALE));
}

export function matchWinProbability(match: ScheduleWithSynergyMatch | null | undefined): WinProbability | null {
  if (!match) return null;
  const prediction = match.prediction;
  if (prediction?.available && typeof prediction.red_win_prob === 'number') {
    const red = prediction.red_win_prob;
    return {
      red,
      blue: typeof prediction.blue_win_prob === 'number' ? prediction.blue_win_prob : 1 - red,
      // The plain rating formula is what ships while the ML blend is off.
      source: /(^|_)ml(_|$)/.test(prediction.source_label || '') ? 'ml_model' : 'rating_model',
      edgeConfidence: prediction.edge_confidence_0_1 ?? null,
    };
  }
  const red = synergyWinProbability(
    match.red.synergy.alliance_synergy_score_0_100,
    match.blue.synergy.alliance_synergy_score_0_100,
  );
  if (red == null) return null;
  return { red, blue: 1 - red, source: 'synergy_derived', edgeConfidence: null };
}
