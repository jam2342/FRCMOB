import type { RatingTrend } from '../../api';
import './RatingTrendBadge.css';

type RatingTrendBadgeProps = {
  trend: RatingTrend | null | undefined;
  className?: string;
};

// SofaScore-style momentum chip: a direction arrow plus how much the team's last
// match here moved its rating. A team that hasn't played here shows a dash.
export function RatingTrendBadge({ trend, className }: RatingTrendBadgeProps) {
  if (!trend || trend.snapshot_count < 2) {
    return <span className={`rating-trend-badge is-new ${className ?? ''}`}>—</span>;
  }

  const direction = trend.direction;
  const delta = trend.delta ?? 0;
  const arrow = direction === 'up' ? '▲' : direction === 'down' ? '▼' : '▬';
  const sign = delta > 0 ? '+' : '';

  return (
    <span
      className={`rating-trend-badge is-${direction} ${className ?? ''}`}
      title={`Last match here: ${sign}${delta.toFixed(1)} rating`}
    >
      <span className="rating-trend-badge__arrow" aria-hidden="true">
        {arrow}
      </span>
      <span className="rating-trend-badge__delta">
        {direction === 'flat' ? '0.0' : `${sign}${delta.toFixed(1)}`}
      </span>
    </span>
  );
}
