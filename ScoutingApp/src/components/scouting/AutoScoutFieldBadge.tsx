import './AutoScoutFieldBadge.css';

type AutoScoutFieldBadgeProps = {
  label: string;
  tone: 'auto' | 'warning' | 'review' | 'manual' | 'blank';
  confidence?: number | null;
  evidenceCount?: number;
  onClick?: () => void;
};

export function AutoScoutFieldBadge({
  label,
  tone,
  confidence = null,
  evidenceCount = 0,
  onClick,
}: AutoScoutFieldBadgeProps) {
  const content = (
    <>
      <span>{label}</span>
      {confidence !== null ? <small>{Math.round(confidence * 100)}%</small> : null}
      {evidenceCount > 0 ? <small>{evidenceCount} ref{evidenceCount === 1 ? '' : 's'}</small> : null}
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        className={`auto-scout-badge auto-scout-badge--${tone}`.trim()}
        onClick={onClick}
      >
        {content}
      </button>
    );
  }

  return <span className={`auto-scout-badge auto-scout-badge--${tone}`.trim()}>{content}</span>;
}
