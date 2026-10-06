import { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import './MoreSheet.css';

function linkClass({ isActive }: { isActive: boolean }) {
  return `ms__link ${isActive ? 'ms__link--active' : ''}`.trim();
}

interface MoreSheetItem {
  to: string;
  label: string;
  icon: React.ReactNode;
  category: 'main' | 'tools' | 'settings';
}

const SHEET_ITEMS: MoreSheetItem[] = [
  {
    to: '/my-team',
    label: 'My Team',
    category: 'main',
    icon: (
      <svg viewBox="0 0 24 24">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    to: '/team-center',
    label: 'Team Center',
    category: 'main',
    icon: (
      <svg viewBox="0 0 24 24">
        <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" />
        <circle cx="9" cy="7" r="4" stroke="currentColor" strokeWidth="1.6" fill="none" />
        <path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    to: '/compare',
    label: 'Compare',
    category: 'main',
    icon: (
      <svg viewBox="0 0 24 24">
        <path d="M18 20V10M12 20V4M6 20v-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
      </svg>
    ),
  },
  {
    to: '/favorites',
    label: 'Favorites',
    category: 'main',
    icon: (
      <svg viewBox="0 0 24 24">
        <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinejoin="round" />
      </svg>
    ),
  },
  // A scout's or lead's team tools: on a phone these were only reachable through the Scout
  // page's view switcher.
  {
    to: '/scouting/assignments',
    label: 'Assignments',
    category: 'tools',
    icon: (
      <svg viewBox="0 0 24 24">
        <path d="M9 11l3 3 8-8" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M20 12v7a2 2 0 01-2 2H6a2 2 0 01-2-2V5a2 2 0 012-2h9" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    to: '/scouting/pit',
    label: 'Pit Scouting',
    category: 'tools',
    icon: (
      <svg viewBox="0 0 24 24">
        <path d="M14.7 6.3a4 4 0 00-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 005.4-5.4l-2.5 2.5-2.5-2.5 2.5-2.5z" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    to: '/compare/picklist',
    label: 'Picklist',
    category: 'tools',
    icon: (
      <svg viewBox="0 0 24 24">
        <path d="M8 6h13M8 12h13M8 18h13" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" />
        <path d="M3 6h.01M3 12h.01M3 18h.01" stroke="currentColor" strokeWidth="2.4" fill="none" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    to: '/match-center/predictions',
    label: 'Predictions',
    category: 'tools',
    icon: (
      <svg viewBox="0 0 24 24">
        <path d="M22 12h-4l-3 9L9 3l-3 9H2" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    to: '/match-center/strategy',
    label: 'Strategy',
    category: 'tools',
    icon: (
      <svg viewBox="0 0 24 24">
        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    to: '/settings',
    label: 'Settings',
    category: 'settings',
    icon: (
      <svg viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.6" fill="none" />
        <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" stroke="currentColor" strokeWidth="1.6" fill="none" />
      </svg>
    ),
  },
];

const SWIPE_CLOSE_THRESHOLD = 60;
const CLOSE_ANIMATION_MS = 200;

interface MoreSheetProps {
  open: boolean;
  onClose: () => void;
}

export function MoreSheet({ open, onClose }: MoreSheetProps) {
  const [closing, setClosing] = useState(false);
  const touchStartY = useRef(0);
  const sheetRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  function startClose() {
    setClosing(true);
  }

  useEffect(() => {
    if (!closing) return;
    const timer = setTimeout(() => {
      setClosing(false);
      onClose();
    }, CLOSE_ANIMATION_MS);
    return () => clearTimeout(timer);
  }, [closing, onClose]);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const focusableSelector = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const focusable = Array.from(sheetRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? []);
    (focusable[0] ?? sheetRef.current)?.focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        setClosing(true);
        return;
      }
      if (event.key !== 'Tab') return;
      const items = Array.from(sheetRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? []);
      if (items.length === 0) {
        event.preventDefault();
        sheetRef.current?.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || !sheetRef.current?.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
      previousFocusRef.current?.focus();
      previousFocusRef.current = null;
    };
  }, [open]);

  function handleTouchStart(e: React.TouchEvent) {
    touchStartY.current = e.touches[0].clientY;
  }

  function handleTouchEnd(e: React.TouchEvent) {
    const delta = e.changedTouches[0].clientY - touchStartY.current;
    if (delta > SWIPE_CLOSE_THRESHOLD) startClose();
  }

  if (!open && !closing) return null;

  const mainItems = SHEET_ITEMS.filter((i) => i.category === 'main');
  const toolItems = SHEET_ITEMS.filter((i) => i.category === 'tools');
  const settingsItems = SHEET_ITEMS.filter((i) => i.category === 'settings');

  return (
    <>
      <button
        type="button"
        className={`ms__backdrop${closing ? ' ms__backdrop--closing' : ''}`}
        onClick={startClose}
        aria-label="Close navigation menu"
      />
      <section
        id="mobile-more-sheet"
        ref={sheetRef}
        className={`ms${closing ? ' ms--closing' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="mobile-more-sheet-title"
        tabIndex={-1}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
      >
        <div className="ms__handle" aria-hidden="true" />

        <div className="ms__section">
          <h3 id="mobile-more-sheet-title" className="ms__section-title">Pages</h3>
          <div className="ms__grid">
            {mainItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={linkClass}
                onClick={startClose}
              >
                <span className="ms__link-icon">{item.icon}</span>
                <span className="ms__link-label">{item.label}</span>
              </NavLink>
            ))}
          </div>
        </div>

        <div className="ms__divider" />

        <div className="ms__section">
          <h3 className="ms__section-title">Tools</h3>
          <div className="ms__grid">
            {toolItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={linkClass}
                onClick={startClose}
              >
                <span className="ms__link-icon">{item.icon}</span>
                <span className="ms__link-label">{item.label}</span>
              </NavLink>
            ))}
          </div>
        </div>

        <div className="ms__divider" />

        <div className="ms__section">
          <div className="ms__grid">
            {settingsItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={linkClass}
                onClick={startClose}
              >
                <span className="ms__link-icon">{item.icon}</span>
                <span className="ms__link-label">{item.label}</span>
              </NavLink>
            ))}
          </div>
        </div>

        <div className="ms__legal">
          <NavLink to="/privacy" className="ms__legal-link" onClick={startClose}>
            Privacy Policy
          </NavLink>
          <span className="ms__legal-sep" aria-hidden="true">·</span>
          <NavLink to="/terms" className="ms__legal-link" onClick={startClose}>
            Terms of Service
          </NavLink>
        </div>
      </section>
    </>
  );
}
