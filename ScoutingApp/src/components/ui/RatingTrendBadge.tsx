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

  // The arrow follows the number shown, not the server's own direction (which has a dead zone):
  // they disagreed into "▬ −0.1". A move that rounds to 0.0 is flat. The arrow carries the sign.
  const change = signedChange(trend.delta ?? 0, 1);
  const direction = change === '0.0' ? 'flat' : change.startsWith('+') ? 'up' : 'down';
  const arrow = direction === 'up' ? '▲' : direction === 'down' ? '▼' : '▬';
  const magnitude = change.replace(/^[+−]/, '');

  return (
    <span
      className={`rating-trend-badge is-${direction} ${className ?? ''}`}
      title={`Last match here: ${change} rating`}
    >
      <span className="rating-trend-badge__arrow" aria-hidden="true">
        {arrow}
      </span>
      <span className="rating-trend-badge__delta">
        {magnitude}
      </span>
    </span>
  );
}
