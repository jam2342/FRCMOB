import { useMemo } from 'react';
import type { RatingTrend } from '../../api';

type RatingSparklineProps = {
  trend: RatingTrend | null | undefined;
  width?: number;
  height?: number;
  className?: string;
};

// Minimal dependency-free SVG sparkline. Renders the recent rating series with
// a color keyed to overall direction (up=green, down=red, flat=muted). A single
// data point renders as a flat midline so it never looks broken.
export function RatingSparkline({
  trend,
  width = 64,
  height = 20,
  className,
}: RatingSparklineProps) {
  const path = useMemo(() => {
    const values = trend?.sparkline ?? [];
    if (values.length === 0) return null;

    const pad = 2;
    const innerW = Math.max(1, width - pad * 2);
    const innerH = Math.max(1, height - pad * 2);

    if (values.length === 1) {
      const y = pad + innerH / 2;
      return `M ${pad} ${y} L ${width - pad} ${y}`;
    }

    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = innerW / (values.length - 1);

    return values
      .map((v, i) => {
        const x = pad + i * stepX;
        // invert y: higher rating -> higher on screen (smaller y)
        const y = pad + innerH - ((v - min) / span) * innerH;
        return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join(' ');
  }, [trend, width, height]);

  const direction = trend?.direction ?? 'flat';
  const stroke =
    direction === 'up'
      ? 'var(--color-success, #16a34a)'
      : direction === 'down'
        ? 'var(--color-danger, #dc2626)'
        : 'var(--color-text-muted, #94a3b8)';

  if (!path) {
    return null;
  }

  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Rating trend, ${direction}`}
      style={{ display: 'block', overflow: 'visible' }}
    >
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
