import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RatingTrendBadge } from './RatingTrendBadge';

const trend = (delta: number, direction: 'up' | 'down' | 'flat') => ({ delta, direction, snapshot_count: 5 }) as never;

describe('RatingTrendBadge', () => {
  it('points the arrow the way the number moved, even when the server calls it flat', () => {
    const { container } = render(<RatingTrendBadge trend={trend(-0.12, 'flat')} />);
    expect(container.querySelector('.rating-trend-badge')).toHaveClass('is-down');
    expect(screen.getByText('▼')).toBeInTheDocument();
    // The arrow carries the sign: "▬ −0.1" read as a double minus.
    expect(screen.getByText('0.1')).toBeInTheDocument();
  });

  it('calls a move that rounds to 0.0 flat', () => {
    const { container } = render(<RatingTrendBadge trend={trend(0.04, 'up')} />);
    expect(container.querySelector('.rating-trend-badge')).toHaveClass('is-flat');
  });
});
