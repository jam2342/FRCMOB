import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { MatchClock } from './MatchClock';

it('names the REBUILT phase the match is in and how long it has left', () => {
  // 70 s into a 160 s match: Shift 2 runs 55-80.
  render(<MatchClock progress={(70 / 160) * 100} />);
  expect(screen.getByRole('progressbar', { name: 'Match clock' })).toHaveAttribute('aria-valuetext', 'Shift 2, 10s left in it');
  expect(screen.getByText('Shift 2')).toBeInTheDocument();
});

it('says when the match has not started', () => {
  render(<MatchClock progress={0} />);
  expect(screen.getByText('Not started yet')).toBeInTheDocument();
});
