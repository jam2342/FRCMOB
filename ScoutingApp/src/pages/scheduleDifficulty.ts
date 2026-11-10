// Team Center's schedule difficulty: how strong a match's opponents are, 1-10.
//
// The model's expected points for the opposing alliance, ranked against every alliance
// predicted this season (red/blue_strength_pct from /events/{key}/schedule-with-synergy).
// One scale everywhere: Einstein's best alliances read 9.9-10 and a week-1 district's
// weakest read near 1. The old score compared the opponents' average rating with the
// event's own teams, which squeezed almost every match into 5-7 (Einstein read 6.2).
// That is still the fallback when the season has no predictions (unrated or frozen).

export type DifficultyTier = 'very-hard' | 'hard' | 'medium' | 'favorable';

export type DifficultyInput = {
  opponentStrengthPct: number | null | undefined;
  opponentRatingAvg: number;
  eventRatingMean: number;
  eventRatingSd: number;
};

export type Difficulty = { score: number; tier: DifficultyTier; basis: 'season' | 'event' };

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

export function difficultyTier(score: number): DifficultyTier {
  if (score >= 8.5) return 'very-hard';
  if (score >= 7) return 'hard';
  if (score >= 5) return 'medium';
  return 'favorable';
}

export function scheduleDifficulty(input: DifficultyInput): Difficulty {
  const pct = input.opponentStrengthPct;
  if (typeof pct === 'number' && Number.isFinite(pct)) {
    const score = clamp(1 + (9 * pct) / 100, 1, 10);
    return { score, tier: difficultyTier(score), basis: 'season' };
  }
  const spread = Math.max(1, input.eventRatingSd);
  const score = clamp(5 + ((input.opponentRatingAvg - input.eventRatingMean) / spread) * 1.75, 1, 10);
  return { score, tier: difficultyTier(score), basis: 'event' };
}

// "Top 1%" reads better than "99th percentile" next to a match.
export function opponentRankLabel(pct: number | null | undefined): string | null {
  if (typeof pct !== 'number' || !Number.isFinite(pct)) return null;
  const top = 100 - pct;
  if (top < 1) return 'Top 1% of alliances';
  if (top <= 50) return `Top ${Math.ceil(top)}% of alliances`;
  return `Bottom ${Math.ceil(pct)}% of alliances`;
}
