import type { RatingTrend } from '../../api';
import './RatingTrendBadge.css';

type RatingTrendBadgeProps = {
  trend: RatingTrend | null | undefined;
  className?: string;
};

// SofaScore-style momentum chip: a direction arrow plus the signed delta since
// the previous snapshot. Renders nothing useful until there are >=2 snapshots
// (a fresh event shows a neutral dash rather than a misleading +0.0).
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
      title={`Changed ${sign}${delta.toFixed(1)} since last update`}
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
