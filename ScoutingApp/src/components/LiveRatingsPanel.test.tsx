import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { LiveRatingsPanel } from './LiveRatingsPanel';

const ratings = Array.from({ length: 25 }, (_, i) => ({
  team_key: `frc${i + 1}`,
  team_number: i + 1,
  nickname: `Team ${i + 1}`,
  rating_0_100: 90 - i,
  rating_trend: null,
}));

let hookState: Record<string, unknown> = {};
vi.mock('../hooks/useLiveRatings', () => ({
  useLiveRatings: () => hookState,
}));

function setHook(overrides: Record<string, unknown>) {
  hookState = {
    ratings,
    lastFetchedAtMs: Date.now(),
    loading: false,
    error: null,
    recentChanges: new Map(),
    refreshNow: () => {},
    ...overrides,
  };
}

describe('LiveRatingsPanel', () => {
  it('shows the top ten and expands to the full board', () => {
    setHook({});
    render(<LiveRatingsPanel eventKey="2026arc" />);
    expect(screen.getAllByRole('listitem')).toHaveLength(10);
    fireEvent.click(screen.getByRole('button', { name: 'Show all 25' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(25);
  });

  it('says so when an event has no ratings yet', () => {
    setHook({ ratings: [] });
    render(<LiveRatingsPanel eventKey="2026arc" />);
    expect(screen.getByText(/No ratings for this event yet/)).toBeInTheDocument();
  });
});
