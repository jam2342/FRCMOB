import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { PastSeasonBanner } from './PastSeasonBanner';
import { pastSeasonNote } from './pastSeason';

describe('PastSeasonBanner', () => {
  afterEach(cleanup);

  it('says nothing for this season or no event', () => {
    expect(pastSeasonNote('2026mndu', 2026)).toBeNull();
    expect(pastSeasonNote('', 2026)).toBeNull();
    expect(pastSeasonNote(null, 2026)).toBeNull();
  });

  it('labels a rated past season and a season without ratings', () => {
    expect(pastSeasonNote('2026mndu', 2027)).toBe("2026 season · REBUILT · final ratings. They don't compare with 2027.");
    expect(pastSeasonNote('2025mndu', 2026)).toBe(
      '2025 season · REEFSCAPE. FRCMOB ratings start in 2026, so this event has results but no ratings or predictions.',
    );
  });

  it('renders the note for a past event', () => {
    render(<PastSeasonBanner eventKey="2024casj" />);
    expect(screen.getByTestId('past-season-banner')).toHaveTextContent('2024 season · CRESCENDO');
  });
});
