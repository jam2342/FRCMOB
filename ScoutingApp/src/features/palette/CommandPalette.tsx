import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router-dom';
import { searchTeams, type TeamSearchResponse } from '../../api';
import { showToast } from '../../components/ui/toast';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { copyTextToClipboard } from '../../pages/centerUtils';
import { shareableAppUrl } from '../../platform/runtime';
import { eventDisplayName, getEventSearchIndexSnapshot } from '../../utils/eventSearchIndex';
import { navigateWithTransition } from '../../utils/viewTransition';
import { getStoredSettings } from '../../layout/userSettings';
import { toggleTheme } from '../../utils/theme';
import { readStoredCenterContext } from '../../layout/centerContext';
import { readMyTeamKey } from '../workspace/myTeam';
import { buildPaletteItems, type PaletteActionId, type PaletteItem, type PalettePage } from './paletteItems';
import styles from './CommandPalette.module.css';

// ⌘K / Ctrl+K anywhere: jump to a team, event, match or page, or run a quick action, without the
// mouse. Opened by the shell, which owns the keyboard shortcut.

const PAGES: PalettePage[] = [
  { to: '/home', label: 'Home', keywords: 'today matches schedule' },
  { to: '/events', label: 'Events', keywords: 'event finder calendar' },
  { to: '/match-center', label: 'Match Center', keywords: 'matches live score' },
  { to: '/match-center/predictions', label: 'Predictions', keywords: 'forecast win chance' },
  { to: '/match-center/strategy', label: 'Strategy briefing', keywords: 'plan opponents' },
  { to: '/team-center', label: 'Team Center', keywords: 'teams robot' },
  { to: '/compare', label: 'Compare teams', keywords: 'versus' },
  { to: '/compare/alliance-advisor', label: 'Alliance Advisor', keywords: 'alliance selection picks' },
  { to: '/compare/picklist', label: 'Picklist', keywords: 'pick list draft' },
  { to: '/scouting', label: 'Scouting', keywords: 'scout board match' },
  { to: '/scouting/pit', label: 'Pit scouting', keywords: 'pits robot specs' },
  { to: '/scouting/assignments', label: 'Scouting assignments', keywords: 'roster schedule scouts' },
  { to: '/scouting/coverage', label: 'Scouting coverage', keywords: 'missing' },
  { to: '/my-team', label: 'My Team', keywords: 'workspace join code members' },
  { to: '/favorites', label: 'Favorites', keywords: 'saved starred' },
  { to: '/settings', label: 'Settings', keywords: 'theme preferences' },
];

const TEAM_SEARCH_DELAY_MS = 180;

const GROUP_ICONS: Record<PaletteItem['group'], string> = {
  Recent: 'M12 7v5l3 2M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9z',
  Teams: 'M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7z',
  Events: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  Matches: 'M5 4h14v16H5zM9 9h6M9 13h6',
  Pages: 'M4 5h16M4 12h16M4 19h10',
  Actions: 'M13 3 4 14h7l-1 7 9-11h-7z',
};

type CommandPaletteProps = {
  open: boolean;
  onClose: () => void;
  recentEvents: readonly string[];
  recentTeams: readonly string[];
  onStartTour: () => void;
};

export function CommandPalette({ open, onClose, recentEvents, recentTeams, onStartTour }: CommandPaletteProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [teamResults, setTeamResults] = useState<TeamSearchResponse['teams']>([]);
  const listId = useId();
  useFocusTrap(dialogRef, open);

  // Team names come from the server; numbers show at once without waiting for it.
  useEffect(() => {
    const value = query.trim();
    // Results for what was typed before must not linger under what is typed now.
    setTeamResults([]);
    if (!open || value.length < 2 || /^\d{4}[a-z]/i.test(value)) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      searchTeams(value, 6)
        .then((payload) => {
          if (!cancelled) setTeamResults(payload.teams || []);
        })
        .catch(() => {
          if (!cancelled) setTeamResults([]);
        });
    }, TEAM_SEARCH_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, query]);

  const themeIsLight = getStoredSettings().theme === 'light';
  const myTeamKey = open ? readMyTeamKey() : '';
  const actions = useMemo<PaletteItem[]>(() => {
    const list: PaletteItem[] = [
      { id: 'action-theme', group: 'Actions', label: themeIsLight ? 'Switch to dark theme' : 'Switch to light theme', action: 'theme', keywords: 'theme dark light mode' },
      { id: 'action-copy', group: 'Actions', label: 'Copy link to this page', action: 'copy-link', keywords: 'share url' },
      { id: 'action-tour', group: 'Actions', label: 'Take the tour of this page', action: 'tour', keywords: 'help tutorial guide' },
    ];
    if (myTeamKey) {
      list.unshift({ id: 'action-my-team', group: 'Actions', label: `Open my team (${myTeamKey.replace(/^frc/, '')})`, action: 'my-team', keywords: 'my team mine' });
    }
    return list;
  }, [myTeamKey, themeIsLight]);

  const items = useMemo(() => buildPaletteItems({
    query,
    recentEvents,
    recentTeams,
    teams: teamResults,
    events: open ? getEventSearchIndexSnapshot() : [],
    contextEventKey: readStoredCenterContext().eventKey,
    pages: PAGES,
    actions,
    eventName: eventDisplayName,
  }), [actions, open, query, recentEvents, recentTeams, teamResults]);

  const activeIndex = Math.min(active, Math.max(0, items.length - 1));

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex]);

  if (!open || typeof document === 'undefined') return null;

  const runAction = async (action: PaletteActionId) => {
    if (action === 'theme') {
      toggleTheme(null);
      return;
    }
    if (action === 'copy-link') {
      const copied = await copyTextToClipboard(shareableAppUrl(`${location.pathname}${location.search}`));
      showToast({ message: copied ? 'Link copied' : 'Could not copy the link on this device', tone: copied ? 'success' : 'warning' });
      return;
    }
    if (action === 'my-team' && myTeamKey) {
      navigateWithTransition(navigate, `/team-center?team=${myTeamKey}`, { direction: 'forward' });
      return;
    }
    if (action === 'tour') onStartTour();
  };

  const choose = (item: PaletteItem | undefined) => {
    if (!item) return;
    onClose();
    if (item.to) {
      navigateWithTransition(navigate, item.to, { direction: 'forward' });
      return;
    }
    if (item.action) void runAction(item.action);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive(Math.min(items.length - 1, activeIndex + 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive(Math.max(0, activeIndex - 1));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      choose(items[activeIndex]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    }
  };

  return createPortal(
    <div className={styles.layer}>
      <div className={styles.backdrop} onClick={onClose} aria-hidden="true" />
      <div ref={dialogRef} className={styles.dialog} role="dialog" aria-modal="true" aria-label="Search and jump">
        <div className={styles.inputRow}>
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" className={styles.searchIcon}>
            <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
            <path d="M20 20l-3.5-3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <input
            className={styles.input}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            placeholder="Team number, event, match (q41), page or action"
            aria-label="Search teams, events, matches, pages and actions"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={items[activeIndex] ? `${listId}-${activeIndex}` : undefined}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
          <kbd className={styles.kbd}>esc</kbd>
        </div>
        <div ref={listRef} id={listId} role="listbox" aria-label="Results" className={styles.list}>
          {items.length === 0 ? (
            <p className={styles.empty}>Nothing matches “{query.trim()}”. Try a team number like 254, or an event name.</p>
          ) : null}
          {items.map((item, index) => (
            <div key={item.id} className={styles.itemWrap}>
              {index === 0 || items[index - 1].group !== item.group ? (
                <div className={styles.group} aria-hidden="true">{item.group}</div>
              ) : null}
              <div
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                data-index={index}
                className={`${styles.item} ${index === activeIndex ? styles.active : ''}`}
                onMouseMove={() => {
                  if (index !== activeIndex) setActive(index);
                }}
                onClick={() => choose(item)}
              >
                <span className={styles.icon} aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="16" height="16"><path d={GROUP_ICONS[item.group]} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </span>
                <span className={styles.label}>{item.label}</span>
                {item.detail ? <span className={styles.detail}>{item.detail}</span> : null}
                <span className={styles.enter} aria-hidden="true">↵</span>
              </div>
            </div>
          ))}
        </div>
        <div className={styles.footer} aria-hidden="true">
          <span><kbd className={styles.kbd}>↑</kbd> <kbd className={styles.kbd}>↓</kbd> move</span>
          <span><kbd className={styles.kbd}>↵</kbd> open</span>
          <span><kbd className={styles.kbd}>esc</kbd> close</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
