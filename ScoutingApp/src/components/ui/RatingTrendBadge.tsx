import type { RatingTrend } from '../../api';
import { signedChange } from '../../utils/formatValue';
import './RatingTrendBadge.css';

type RatingTrendBadgeProps = {
  trend: RatingTrend | null | undefined;
  className?: string;
};

// Momentum chip: a direction arrow plus how much the team's last
// match here moved its rating. A team that hasn't played here shows a dash.
export function RatingTrendBadge({ trend, className }: RatingTrendBadgeProps) {
  if (!trend || trend.snapshot_count < 2) {
    return <span className={`rating-trend-badge is-new ${className ?? ''}`}>—</span>;
  }

  // A move that rounds to 0.0 is flat: the badge showed "▼ -0.0" for a 0.04 drop.
  const change = signedChange(trend.delta ?? 0, 1);
  const direction = change === '0.0' ? 'flat' : trend.direction;
  const arrow = direction === 'up' ? '▲' : direction === 'down' ? '▼' : '▬';

  return (
    <span
      className={`rating-trend-badge is-${direction} ${className ?? ''}`}
      title={`Last match here: ${change} rating`}
    >
      <span className="rating-trend-badge__arrow" aria-hidden="true">
        {arrow}
      </span>
      <span className="rating-trend-badge__delta">
        {change}
      </span>
    </span>
  );
}
