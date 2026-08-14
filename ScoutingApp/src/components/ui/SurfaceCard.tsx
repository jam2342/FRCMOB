import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

function cx(...args: (string | false | null | undefined | 0)[]): string {
  return args.filter(Boolean).join(' ');
}

type SurfaceCardProps = {
  title: string;
  subtitle?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  expandable?: boolean;
  mobileCollapsible?: boolean;
  collapsible?: boolean;
  compactable?: boolean;
};

type SurfaceCardGroupProps = {
  groupId?: string;
  children: ReactNode;
};

const MOBILE_COLLAPSE_MEDIA_QUERY = '(max-width: 900px)';

function ExpandIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M2 6V2h4" />
      <path d="M10 2h4v4" />
      <path d="M14 10v4h-4" />
      <path d="M6 14H2v-4" />
      <path d="m6 2-4 4" />
      <path d="m10 2 4 4" />
      <path d="m10 14 4-4" />
      <path d="m6 14-4-4" />
    </svg>
  );
}

function CompressIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M6 2H2v4" />
      <path d="M10 2h4v4" />
      <path d="M14 10v4h-4" />
      <path d="M2 10v4h4" />
      <path d="m2 2 4 4" />
      <path d="m14 2-4 4" />
      <path d="m14 14-4-4" />
      <path d="m2 14 4-4" />
    </svg>
  );
}

function MinimizeIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M3 8h10" />
    </svg>
  );
}

function RestoreIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M3 3h10v10H3z" />
      <path d="M3 6h10" />
    </svg>
  );
}

function viewportIsMobile(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(MOBILE_COLLAPSE_MEDIA_QUERY).matches;
}

/** No-op wrapper kept for backward-compat with call sites that still pass groupId. */
export function SurfaceCardGroup({ children }: SurfaceCardGroupProps) {
  return <>{children}</>;
}

export function SurfaceCard({
  title,
  subtitle,
  right,
  children,
  className = '',
  expandable = true,
  mobileCollapsible = true,
  collapsible = false,
  compactable = false,
}: SurfaceCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [isMobileViewport, setIsMobileViewport] = useState(viewportIsMobile);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (!expanded) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [expanded]);

  useEffect(() => {
    document.body.classList.toggle('surface-card-modal-open', expanded);
    return () => document.body.classList.remove('surface-card-modal-open');
  }, [expanded]);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const mediaQuery = window.matchMedia(MOBILE_COLLAPSE_MEDIA_QUERY);
    const update = () => setIsMobileViewport(mediaQuery.matches);
    update();
    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', update);
      return () => mediaQuery.removeEventListener('change', update);
    }
    mediaQuery.addListener(update);
    return () => mediaQuery.removeListener(update);
  }, []);

  const canExpand = Boolean(expandable);
  const canCollapse = Boolean((collapsible || (mobileCollapsible && isMobileViewport)) && !expanded);
  const isCollapsed = Boolean(canCollapse && collapsed);

  const handleToggleExpand = () =>
    setExpanded((previous) => {
      const next = !previous;
      if (next) setCollapsed(false);
      return next;
    });

  const normalCardClass = cx(
    'surface-card',
    expanded && 'surface-card--expand-source',
    isMobileViewport && 'surface-card--mobile',
    isCollapsed && 'surface-card--collapsed',
    isCollapsed && isMobileViewport && 'surface-card--mobile-collapsed',
    compactable && 'surface-card--compactable',
    className,
  );

  // Render backdrop + expanded card via portal so it escapes any ancestor
  // stacking context (will-change, transform, filter, etc.) that would
  // otherwise contain position:fixed children in the wrong layer.
  const expandedPortal =
    expanded && typeof document !== 'undefined'
      ? createPortal(
          <>
            <button
              type="button"
              className="surface-card-backdrop"
              onClick={() => setExpanded(false)}
              aria-label={`Close expanded ${title} panel`}
            />
            <section
              className={cx(
                'surface-card',
                'surface-card--expanded',
                isMobileViewport && 'surface-card--mobile',
                className,
              )}
            >
              <header className="surface-card-head">
                <div>
                  <h3>{title}</h3>
                  {subtitle ? <p>{subtitle}</p> : null}
                </div>
                <div className="surface-card-head-actions">
                  {right ? <div className="surface-card-right">{right}</div> : null}
                  <button
                    type="button"
                    className="surface-card-expand-btn"
                    onClick={() => setExpanded(false)}
                    aria-label={`Close expanded ${title} panel`}
                    title="Close"
                  >
                    <CompressIcon />
                  </button>
                </div>
              </header>
              <div className="surface-card-body">{children}</div>
            </section>
          </>,
          document.body,
        )
      : null;

  return (
    <>
      <section className={normalCardClass} aria-hidden={expanded || undefined}>
        <header className="surface-card-head">
          <div>
            <h3>{title}</h3>
            {subtitle && !isCollapsed ? <p>{subtitle}</p> : null}
          </div>
          {right || canExpand || canCollapse ? (
            <div className="surface-card-head-actions">
              {right && !isCollapsed ? <div className="surface-card-right">{right}</div> : null}
              {canCollapse ? (
                <button
                  type="button"
                  className="surface-card-collapse-btn"
                  onClick={() => setCollapsed((previous) => !previous)}
                  aria-label={isCollapsed ? `Expand ${title} block` : `Minimize ${title} block`}
                  title={isCollapsed ? 'Expand' : 'Minimize'}
                >
                  {isCollapsed ? <RestoreIcon /> : <MinimizeIcon />}
                  <span>{isCollapsed ? 'Expand' : 'Minimize'}</span>
                </button>
              ) : null}
              {canExpand ? (
                <button
                  type="button"
                  className="surface-card-expand-btn"
                  onClick={handleToggleExpand}
                  aria-label={`Open ${title} fullscreen`}
                  title="Fullscreen"
                >
                  <ExpandIcon />
                </button>
              ) : null}
            </div>
          ) : null}
        </header>
        {!isCollapsed && !expanded ? <div className="surface-card-body">{children}</div> : null}
      </section>
      {expandedPortal}
    </>
  );
}
