import { cx } from './primitives/cx';
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { createPortal } from 'react-dom';


type SurfaceCardProps = {
  title: string;
  subtitle?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  // Fullscreen is for content that outgrows its card: a table, a chart, a heat map, a video.
  // It used to be on by default, so a desktop screen carried five or six identical corner buttons.
  expandable?: boolean;
  mobileCollapsible?: boolean;
  collapsible?: boolean;
  compactable?: boolean;
  // On a phone, a card whose title only repeats the tab just tapped ("Schedule" under the
  // Schedule tab) keeps the title for screen readers and gives the space back.
  hideTitleOnPhone?: boolean;
  // What a page tour points at (`data-tour`); tutorialBlueprints.test.ts checks every hook exists.
  tourId?: string;
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

const viewportListeners = new Set<() => void>();
let viewportQuery: MediaQueryList | null = null;
const notifyViewport = () => { for (const listener of viewportListeners) listener(); };

// Cards share one viewport listener, including cards that only use mobile styling.
function subscribeViewport(listener: () => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => {};
  if (!viewportQuery) {
    viewportQuery = window.matchMedia(MOBILE_COLLAPSE_MEDIA_QUERY);
    if (typeof viewportQuery.addEventListener === 'function') viewportQuery.addEventListener('change', notifyViewport);
    else viewportQuery.addListener(notifyViewport);
  }
  viewportListeners.add(listener);
  return () => {
    viewportListeners.delete(listener);
    if (viewportListeners.size || !viewportQuery) return;
    if (typeof viewportQuery.removeEventListener === 'function') viewportQuery.removeEventListener('change', notifyViewport);
    else viewportQuery.removeListener(notifyViewport);
    viewportQuery = null;
  };
}

export function SurfaceCard({
  title,
  subtitle,
  right,
  children,
  className = '',
  expandable = false,
  // Off by default: on a phone people scroll past a card rather than collapse it, and a
  // minimize button on every card was clutter (and an easy mis-tap that hid content).
  mobileCollapsible = false,
  collapsible = false,
  compactable = false,
  hideTitleOnPhone = false,
  tourId,
}: SurfaceCardProps) {
  const [expanded, setExpanded] = useState(false);
  const expandedRef = useRef<HTMLElement>(null);
  useFocusTrap(expandedRef, expanded);
  const isMobileViewport = useSyncExternalStore(subscribeViewport, viewportIsMobile, () => false);
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



  // On a phone the card already fills the width, so "fullscreen" only added a button per card.
  const canExpand = Boolean(expandable) && !isMobileViewport;
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
              ref={expandedRef}
              role="dialog"
              aria-modal="true"
              aria-label={title}
              tabIndex={-1}
              className={cx(
                'surface-card',
                'surface-card--expanded',
                isMobileViewport && 'surface-card--mobile',
                className,
              )}
            >
              <header className="surface-card-head">
                <div>
                  {/* A collapsed title is cut with "…"; the full title is one hover or long-press away. */}
            <h3 title={isCollapsed ? title : undefined}>{title}</h3>
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

  const headless = hideTitleOnPhone && isMobileViewport && !right && !canCollapse;

  return (
    <>
      <section className={normalCardClass} aria-hidden={expanded || undefined} data-tour={tourId}>
        <header className={cx('surface-card-head', headless && 'surface-card-head--hidden')}>
          <div>
            {/* A collapsed title is cut with "…"; the full title is one hover or long-press away. */}
            <h3 title={isCollapsed ? title : undefined}>{title}</h3>
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
