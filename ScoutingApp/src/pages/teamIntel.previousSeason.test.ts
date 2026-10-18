import { describe, expect, it } from 'vitest';
import { previousSeasonFromIntel, previousSeasonText, ratingFromIntel } from './teamIntel';

describe('last season in Team Center', () => {
  const intel = {
    rating: {
      available: false,
      season: 2027,
      previous_season: { season: 2026, game: 'REBUILT', event_key: '2026mndu', event_name: 'Lake Superior Regional', rating_0_100: 83.17, rank: 1, field_size: 42 },
    },
  };

  it('keeps last season apart from the headline rating', () => {
    expect(ratingFromIntel(intel, 'frc3100', 3100, null)).toBeNull();
    const previous = previousSeasonFromIntel(intel);
    expect(previous).toMatchObject({ season: 2026, rating: 83.17, rank: 1, fieldSize: 42 });
    expect(previousSeasonText(previous!)).toBe(
      "Last season (2026 · REBUILT): 83.2, 1st of 42 at Lake Superior Regional. A different game, so it doesn't compare with this season.",
    );
  });

  it('has nothing to say when this season has a rating or there is no earlier one', () => {
    expect(previousSeasonFromIntel({ rating: { available: true, rating_0_100: 70 } })).toBeNull();
    expect(previousSeasonFromIntel({ rating: { available: false } })).toBeNull();
  });
});
