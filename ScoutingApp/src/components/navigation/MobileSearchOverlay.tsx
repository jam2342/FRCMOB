import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { type QuickJumpMode, type QuickJumpRegion } from '../../layout/userSettings';
import { useRecentSearches } from '../../hooks/useRecentSearches';
import { Spinner } from '../ui/Spinner';
import './MobileSearchOverlay.css';

interface MobileSearchOverlayProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (query: string) => void;
  busy?: boolean;
  jumpMode?: QuickJumpMode;
  jumpRegion?: QuickJumpRegion;
}

const MAX_RECENT = 8;
const HINTS_DISMISSED_KEY = 'mso_hints_dismissed';

const MODE_LABELS: Record<QuickJumpMode, string> = {
  auto: 'Auto',
  team: 'Team',
  event: 'Event',
};

const REGION_LABELS: Record<QuickJumpRegion, string> = {
  all: 'All Regions',
  usa: 'USA',
  canada: 'Canada',
  international: 'International',
  tx: 'Texas',
  ca: 'California',
  mi: 'Michigan',
  ny: 'New York',
};

function readHintsDismissed(): boolean {
  try {
    return window.localStorage.getItem(HINTS_DISMISSED_KEY) === '1';
  } catch {
    return false;
  }
}

function writeHintsDismissed(): void {
  try {
    window.localStorage.setItem(HINTS_DISMISSED_KEY, '1');
  } catch {
    // ignore
  }
}

export function MobileSearchOverlay({ open, onClose, onSubmit, busy, jumpMode = 'auto', jumpRegion = 'all' }: MobileSearchOverlayProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [hintsDismissed, setHintsDismissed] = useState(readHintsDismissed);
  const [clearPending, setClearPending] = useState(false);
  const { recentSearches, addRecentSearch, refreshRecentSearches, clearRecentSearches } = useRecentSearches(MAX_RECENT);

  const handleClose = useCallback(() => {
    setClearPending(false);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (open) {
      refreshRecentSearches();
      const raf = requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
      return () => cancelAnimationFrame(raf);
    }
  }, [open, refreshRecentSearches]);

  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = '';
      };
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') handleClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, handleClose]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || busy) return;
    addRecentSearch(trimmed);
    onSubmit(trimmed);
    handleClose();
  }

  function handleRecentClick(value: string) {
    setQuery(value);
    addRecentSearch(value);
    onSubmit(value);
    handleClose();
  }

  function handleDismissHints() {
    writeHintsDismissed();
    setHintsDismissed(true);
  }

  function handleClearClick() {
    if (clearPending) {
      clearRecentSearches();
      setClearPending(false);
    } else {
      setClearPending(true);
    }
  }

  if (!open) return null;

  return (
    <div className="mso" role="dialog" aria-label="Search" aria-modal="true">
      <div className="mso__backdrop" onClick={handleClose} />
      <div className="mso__content">
        <form className="mso__form" onSubmit={handleSubmit}>
          <button
            type="button"
            className="mso__back"
            onClick={handleClose}
            aria-label="Close search"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M15 19l-7-7 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
            </svg>
          </button>
          <input
            ref={inputRef}
            type="search"
            className="mso__input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search teams, events, matches..."
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
          {query && (
            <button
              type="button"
              className="mso__clear"
              onClick={() => {
                setQuery('');
                inputRef.current?.focus();
              }}
              aria-label="Clear search"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
          )}
          <button
            type="submit"
            className="mso__submit"
            disabled={busy || !query.trim()}
            aria-label={busy ? 'Searching…' : 'Search'}
          >
            {busy ? <Spinner size={16} /> : 'Search'}
          </button>
        </form>

        <div className="mso__context-chips">
          <NavLink to="/settings" className="mso__context-chip mso__context-chip--link" onClick={handleClose} title="Change in Settings">
            {MODE_LABELS[jumpMode]}
          </NavLink>
          <NavLink to="/settings" className="mso__context-chip mso__context-chip--link" onClick={handleClose} title="Change in Settings">
            {REGION_LABELS[jumpRegion]}
          </NavLink>
          <span className="mso__context-chip-hint">Tap to change</span>
        </div>

        {recentSearches.length > 0 && !query.trim() && (
          <div className="mso__recent">
            <div className="mso__recent-header">
              <span>Recent searches</span>
              <button
                type="button"
                className={`mso__recent-clear${clearPending ? ' mso__recent-clear--confirm' : ''}`}
                onClick={handleClearClick}
                onBlur={() => setClearPending(false)}
              >
                {clearPending ? 'Confirm?' : 'Clear all'}
              </button>
            </div>
            <div className="mso__recent-list">
              {recentSearches.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="mso__recent-item"
                  onClick={() => handleRecentClick(s)}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true" className="mso__recent-icon">
                    <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
                    <path d="M12 7v5l3 3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                  </svg>
                  <span>{s}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {!hintsDismissed && (
          <div className="mso__hints">
            <button
              type="button"
              className="mso__hints-dismiss"
              onClick={handleDismissHints}
              aria-label="Dismiss format hints"
            >
              ×
            </button>
            <div className="mso__hint">
              <span className="mso__hint-label">Team</span>
              <span className="mso__hint-example">frc118, 254, "team citrus"</span>
            </div>
            <div className="mso__hint">
              <span className="mso__hint-label">Event</span>
              <span className="mso__hint-example">2026txhou, "houston regional"</span>
            </div>
            <div className="mso__hint">
              <span className="mso__hint-label">Match</span>
              <span className="mso__hint-example">2026txhou_qm1</span>
              <span className="mso__hint-description">event key + _ + match code (qm=qual, sf=semi, f=final)</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
