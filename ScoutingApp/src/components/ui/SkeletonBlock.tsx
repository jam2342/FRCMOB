type SkeletonBlockProps = {
  rows?: number;
  compact?: boolean;
};

export function SkeletonBlock({ rows = 4, compact = false }: SkeletonBlockProps) {
  const count = Math.max(1, Math.floor(rows));
  return (
    <div className={`skeleton-stack ${compact ? 'compact' : ''}`.trim()} aria-hidden="true">
      {Array.from({ length: count }).map((_, idx) => (
        <div key={`skeleton-row-${idx}`} className="skeleton-line" />
      ))}
    </div>
  );
}

