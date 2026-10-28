import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { getScoutingCoverage } from '../../api';
import { ScoutThanksNote } from './ScoutThanksNote';

vi.mock('../../api', () => ({ getScoutingCoverage: vi.fn() }));

it('thanks the scout holding the phone for their share of the event', async () => {
  vi.mocked(getScoutingCoverage).mockResolvedValue({
    ok: true, event_key: '2026mndu',
    summary: { total_slots: 0, covered_slots: 0, coverage_pct: 0, total_entries: 0, scout_count: 0, outlier_count: 0 },
    grid: [], outliers: [],
    leaderboard: [{ scout_profile: 'Test Scout', entry_count: 14, matches_covered: 14, teams_covered: 12, best_qual_streak: 9, last_entry_at: null }],
  });
  render(<ScoutThanksNote eventKey="2026mndu" me="Test Scout" allDone />);
  expect(await screen.findByText('Thank you for scouting!')).toBeInTheDocument();
  expect(screen.getByText(/14 reports, 9 matches in a row\./)).toBeInTheDocument();
  expect(getScoutingCoverage).toHaveBeenCalledWith('2026mndu');
});
