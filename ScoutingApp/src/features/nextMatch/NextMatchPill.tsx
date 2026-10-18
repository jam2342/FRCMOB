import { formatTime } from '../../utils/formatTime';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getEventSchedule, type EventScheduleItem } from '../../api';
import { showToast } from '../../components/ui/toast';
import { usePageClock } from '../../hooks/usePageClock';
import { usePageVisibility } from '../../hooks/usePageVisibility';
import { useSingleFlightPolling } from '../../hooks/useSingleFlightPolling';
import { CENTER_CONTEXT_UPDATED_EVENT, readStoredCenterContext } from '../../layout/centerContext';
import { buildMatchCenterPath } from '../../pages/centerUtils';
import { navigateWithTransition } from '../../utils/viewTransition';
import { readMyTeamKey } from '../workspace/myTeam';
import { countdownLabel, findNextMatch, shortMatchLabel } from './nextMatch';
import styles from './NextMatchPill.module.css';

const HIDDEN_KEY = 'frcmob_next_match_hidden';
const POLL_MS = 60_000;
// Pages where the pill would cover the work (the scouting board's buttons, the recorder).
const HIDDEN_ON = ['/scouting', '/scouting/record', '/scouting/calibrate'];
const SPACE_VAR = '--next-match-pill-space';

function readHidden(): string {
  try {
    return window.localStorage.getItem(HIDDEN_KEY) || '';
  } catch {
    return '';
  }
}

function writeHidden(matchKey: string) {
  try {
    if (matchKey) window.localStorage.setItem(HIDDEN_KEY, matchKey);
    else window.localStorage.removeItem(HIDDEN_KEY);
  } catch {
    // Storage unavailable: hidden for this visit only.
  }
}

function clockTime(ms: number): string {
  return formatTime(ms);
}

export function NextMatchPill({ pathname, search }: { pathname: string; search: string }) {
  const navigate = useNavigate();
  const visible = usePageVisibility();
  const [eventKey, setEventKey] = useState(() => readStoredCenterContext().eventKey);
  const [teamKey, setTeamKey] = useState(() => readMyTeamKey());
  const [matches, setMatches] = useState<EventScheduleItem[]>([]);
  const [hiddenKey, setHiddenKey] = useState(readHidden);
  const [expanded, setExpanded] = useState(false);
  const pillRef = useRef<HTMLDivElement | null>(null);

  // The event and team can change on any page; re-read them as you move around.
  useEffect(() => {
    const sync = () => {
      setEventKey(readStoredCenterContext().eventKey);
      setTeamKey(readMyTeamKey());
    };
    sync();
    window.addEventListener(CENTER_CONTEXT_UPDATED_EVENT, sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(CENTER_CONTEXT_UPDATED_EVENT, sync);
      window.removeEventListener('storage', sync);
    };
  }, [pathname]);

  const run = useCallback(async () => {
    if (!eventKey) return;
    const schedule = await getEventSchedule(eventKey, false);
    setMatches(Array.isArray(schedule.matches) ? schedule.matches : []);
  }, [eventKey]);

  useEffect(() => {
    setMatches([]);
  }, [eventKey, teamKey]);

  useSingleFlightPolling({ enabled: Boolean(eventKey && teamKey), visible, intervalMs: POLL_MS, run });

  // Ticks every second only while there is a match to count down to.
  const candidate = useMemo(() => findNextMatch(matches, teamKey, Date.now()), [matches, teamKey]);
  const nowMs = usePageClock(visible && Boolean(candidate));
  const next = useMemo(() => findNextMatch(matches, teamKey, nowMs), [matches, teamKey, nowMs]);

  const onThatMatch = Boolean(next && pathname.startsWith('/match-center') && new URLSearchParams(search).get('match') === next.match.match_key);
  const show = Boolean(next) && next?.match.match_key !== hiddenKey && !HIDDEN_ON.includes(pathname.replace(/\/$/, '')) && !onThatMatch;

  // Pages leave room for the pill so it never covers their last row.
  useLayoutEffect(() => {
    const root = document.documentElement;
    const pill = pillRef.current;
    if (!show || !pill) {
      root.style.removeProperty(SPACE_VAR);
      return undefined;
    }
    root.style.setProperty(SPACE_VAR, `${pill.querySelector('button')?.offsetHeight ?? 52}px`);
    return () => {
      root.style.removeProperty(SPACE_VAR);
    };
  }, [show]);

  useEffect(() => {
    if (!expanded) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false);
    };
    const onPointer = (event: PointerEvent) => {
      if (pillRef.current && !pillRef.current.contains(event.target as Node)) setExpanded(false);
    };
    window.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onPointer);
    return () => {
      window.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onPointer);
    };
  }, [expanded]);

  if (!show || !next) return null;
  const label = shortMatchLabel(next.match);
  const countdown = countdownLabel(next.startMs, nowMs);
  const headline = next.state === 'now' ? `${label} · on the field` : `${label} · you're ${next.stationLabel}`;

  const openMatch = () => {
    setExpanded(false);
    navigateWithTransition(navigate, buildMatchCenterPath(eventKey, next.match.match_key), { direction: 'forward' });
  };
  const tuckAway = () => {
    const key = next.match.match_key;
    setExpanded(false);
    writeHidden(key);
    setHiddenKey(key);
    showToast({
      message: `Hidden for ${label}. It comes back for your next match.`,
      tone: 'info',
      actionLabel: 'Undo',
      onAction: () => {
        writeHidden('');
        setHiddenKey('');
      },
    });
  };

  return (
    <div
      ref={pillRef}
      className={`${styles.pill} ${expanded ? styles.expanded : ''} ${next.state !== 'later' ? styles.soon : ''}`}
      data-alliance={next.alliance}
    >
      <div className={styles.details} aria-hidden={!expanded} inert={!expanded}>
        <div className={styles.detailsInner}>
          <div className={styles.headRow}>
            <div>
              <span className={styles.eyebrow}>Your next match</span>
              <strong className={styles.title}>{next.match.display_name} · {next.stationLabel}</strong>
            </div>
            <button type="button" className={styles.iconButton} onClick={() => setExpanded(false)} aria-label="Collapse">
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            </button>
          </div>
          <div className={styles.clockRow}>
            <strong className={styles.bigClock}>{countdown}</strong>
            <span className={styles.muted}>{next.state === 'now' ? 'Started' : 'Expected'} {clockTime(next.startMs)}</span>
          </div>
          <div className={styles.lineups}>
            <div>
              <span className={styles.eyebrow}>With</span>
              <span className={next.alliance === 'red' ? styles.red : styles.blue}>
                {next.partners.length ? next.partners.join(' · ') : 'TBD'}
              </span>
            </div>
            <div>
              <span className={styles.eyebrow}>Against</span>
              <span className={next.alliance === 'red' ? styles.blue : styles.red}>
                {next.opponents.length ? next.opponents.join(' · ') : 'TBD'}
              </span>
            </div>
          </div>
          <div className={styles.actions}>
            <button type="button" className={styles.primary} onClick={openMatch}>Open Match Center</button>
            <button type="button" className={styles.secondary} onClick={tuckAway}>Hide</button>
          </div>
        </div>
      </div>
      <button
        type="button"
        className={styles.bar}
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
        aria-label={`Your next match: ${headline}, ${countdown}`}
      >
        <span className={styles.dot} aria-hidden="true" />
        <span className={styles.headline}>{headline}</span>
        <strong className={styles.countdown}>{countdown}</strong>
        <svg className={styles.chevron} viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M6 15l6-6 6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
      </button>
    </div>
  );
}
