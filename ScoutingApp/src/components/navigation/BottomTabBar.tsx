import { NavLink } from 'react-router-dom';
import { useHideOnScroll } from '../../hooks/useHideOnScroll';
import './BottomTabBar.css';

function cx(...args: (string | false | null | undefined | 0)[]): string {
  return args.filter(Boolean).join(' ');
}

/* ── Icon components (outlined inactive / filled active) ──────── */

function HomeIcon({ active }: { active: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {active ? (
        <path
          d="M12 3L4 9v11a1 1 0 001 1h4a1 1 0 001-1v-5h4v5a1 1 0 001 1h4a1 1 0 001-1V9l-8-6z"
          fill="currentColor"
          stroke="none"
        />
      ) : (
        <path
          d="M12 3L4 9v11a1 1 0 001 1h4a1 1 0 001-1v-5h4v5a1 1 0 001 1h4a1 1 0 001-1V9l-8-6z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}

function EventsIcon({ active }: { active: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {active ? (
        <>
          <rect x="3" y="4" width="18" height="18" rx="3" fill="currentColor" />
          <path d="M8 2v4M16 2v4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          <path d="M3 10h18" stroke="var(--color-bg, #10151b)" strokeWidth="1.5" />
          <circle cx="8" cy="15" r="1.5" fill="var(--color-bg, #10151b)" />
          <circle cx="12" cy="15" r="1.5" fill="var(--color-bg, #10151b)" />
          <circle cx="16" cy="15" r="1.5" fill="var(--color-bg, #10151b)" />
        </>
      ) : (
        <>
          <rect
            x="3" y="4" width="18" height="18" rx="3"
            fill="none" stroke="currentColor" strokeWidth="1.6"
          />
          <path d="M8 2v4M16 2v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          <path d="M3 10h18" stroke="currentColor" strokeWidth="1.6" />
          <circle cx="8" cy="15" r="1.2" fill="currentColor" />
          <circle cx="12" cy="15" r="1.2" fill="currentColor" />
          <circle cx="16" cy="15" r="1.2" fill="currentColor" />
        </>
      )}
    </svg>
  );
}

function ScoutIcon({ active }: { active: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {active ? (
        <>
          <path
            d="M9 2h6a1 1 0 011 1v1H8V3a1 1 0 011-1z"
            fill="currentColor"
          />
          <rect x="4" y="4" width="16" height="18" rx="2" fill="currentColor" />
          <path d="M8 9h8M8 13h6M8 17h4" stroke="var(--color-bg, #10151b)" strokeWidth="1.5" strokeLinecap="round" />
        </>
      ) : (
        <>
          <path
            d="M9 2h6a1 1 0 011 1v1H8V3a1 1 0 011-1z"
            fill="none" stroke="currentColor" strokeWidth="1.6"
          />
          <rect
            x="4" y="4" width="16" height="18" rx="2"
            fill="none" stroke="currentColor" strokeWidth="1.6"
          />
          <path d="M8 9h8M8 13h6M8 17h4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </>
      )}
    </svg>
  );
}

function MatchesIcon({ active }: { active: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {active ? (
        <>
          <rect x="2" y="5" width="20" height="14" rx="2.5" fill="currentColor" />
          <path d="M12 5v14" stroke="var(--color-bg, #10151b)" strokeWidth="1.5" />
          <path d="M2 12h20" stroke="var(--color-bg, #10151b)" strokeWidth="1.5" />
          <path d="M7 5v14M17 5v14" stroke="var(--color-bg, #10151b)" strokeWidth="1" strokeDasharray="2 2" />
        </>
      ) : (
        <>
          <rect
            x="2" y="5" width="20" height="14" rx="2.5"
            fill="none" stroke="currentColor" strokeWidth="1.6"
          />
          <path d="M12 5v14" stroke="currentColor" strokeWidth="1.6" />
          <path d="M2 12h20" stroke="currentColor" strokeWidth="1.6" />
          <path d="M7 5v14M17 5v14" stroke="currentColor" strokeWidth="1" strokeDasharray="2 2" />
        </>
      )}
    </svg>
  );
}

function MoreIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="5" r="1.8" fill="currentColor" />
      <circle cx="12" cy="12" r="1.8" fill="currentColor" />
      <circle cx="12" cy="19" r="1.8" fill="currentColor" />
    </svg>
  );
}

interface BottomTabItem {
  to: string;
  label: string;
}

interface BottomTabBarProps {
  moreOpen: boolean;
  onToggleMore: () => void;
}

const TAB_ITEMS: BottomTabItem[] = [
  { to: '/home', label: 'Home' },
  { to: '/events', label: 'Events' },
  { to: '/scouting', label: 'Scout' },
  { to: '/match-center', label: 'Matches' },
];

const ICON_MAP: Record<string, React.FC<{ active: boolean }>> = {
  '/home': HomeIcon,
  '/events': EventsIcon,
  '/scouting': ScoutIcon,
  '/match-center': MatchesIcon,
};

function triggerHaptic() {
  try {
    if ('vibrate' in navigator) {
      navigator.vibrate(8);
    }
  } catch {
    // Haptic not available — silently ignore
  }
}

export function BottomTabBar({ moreOpen, onToggleMore }: BottomTabBarProps) {
  const barVisible = useHideOnScroll();

  return (
    <nav
      className={cx('btb', !barVisible && 'btb--hidden')}
      aria-label="Quick navigation"
    >
      {TAB_ITEMS.map((tab) => (
        <NavLink
          key={`btab-${tab.to}`}
          to={tab.to}
          className={({ isActive }) => cx('btb__tab', isActive && 'btb__tab--active')}
          onClick={triggerHaptic}
        >
          {({ isActive }) => {
            const IconComponent = ICON_MAP[tab.to];
            return (
              <>
                <span className="btb__icon">
                  {IconComponent ? <IconComponent active={isActive} /> : null}
                </span>
                <span className="btb__label">{tab.label}</span>
                {isActive && <span className="btb__indicator" />}
              </>
            );
          }}
        </NavLink>
      ))}
      <button
        type="button"
        className={cx('btb__tab', 'btb__tab--more', moreOpen && 'btb__tab--active')}
        onClick={() => {
          triggerHaptic();
          onToggleMore();
        }}
        aria-label="More navigation"
      >
        <span className="btb__icon">
          <MoreIcon />
        </span>
        <span className="btb__label">More</span>
        {moreOpen && <span className="btb__indicator" />}
      </button>
    </nav>
  );
}
