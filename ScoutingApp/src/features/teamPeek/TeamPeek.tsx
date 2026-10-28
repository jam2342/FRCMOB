import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { showToast } from '../../components/ui/toast';
import { readStoredCenterContext } from '../../layout/centerContext';
import { readFavoriteTeams, saveFavoriteTeams } from '../../layout/userSettings';
import { hapticTap } from '../../utils/haptics';
import { navigateWithTransition } from '../../utils/viewTransition';
import { loadTeamCenterIntel } from '../prefetch/pageRequests';
import { peekFromIntel, type TeamPeekData } from './peekData';
import styles from './TeamPeek.module.css';
import './TeamPeekAnchors.css';

// A quick look at a team without leaving the page. Anything carrying data-team-peek="frc254"
// (optionally data-team-peek-event) gets it: hover or keyboard focus on a desktop, a long press on
// a phone. The data is the request Team Center makes first, so opening the team afterwards is
// instant.

const HOVER_DELAY_MS = 380;
const HIDE_DELAY_MS = 180;
const LONG_PRESS_MS = 450;
const MOVE_TOLERANCE_PX = 10;
const CARD_WIDTH = 300;
// ComparePage's stored selection (COMPARE_STORAGE_KEYS.teams).
const COMPARE_TEAMS_STORAGE = 'scouting_compare_team_keys';

type Anchor = { teamKey: string; eventKey: string; rect: DOMRect; mode: 'hover' | 'sheet' };

function peekTarget(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element ? target.closest<HTMLElement>('[data-team-peek]') : null;
}

function anchorFrom(el: HTMLElement, mode: Anchor['mode']): Anchor | null {
  const teamKey = (el.dataset.teamPeek || '').trim().toLowerCase();
  if (!/^frc\d+[a-z]?$/.test(teamKey)) return null;
  const eventKey = (el.dataset.teamPeekEvent || readStoredCenterContext().eventKey || '').toLowerCase();
  return { teamKey, eventKey, rect: el.getBoundingClientRect(), mode };
}

function cardPosition(rect: DOMRect): { left: number; top: number } {
  const margin = 12;
  const fitsRight = rect.right + margin + CARD_WIDTH < window.innerWidth - margin;
  const left = fitsRight ? rect.right + margin : Math.max(margin, rect.left - margin - CARD_WIDTH);
  const top = Math.min(Math.max(margin, rect.top - 12), window.innerHeight - 280);
  return { left, top };
}

export function TeamPeek() {
  const navigate = useNavigate();
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const [data, setData] = useState<TeamPeekData | null>(null);
  const [failed, setFailed] = useState(false);
  const [favorite, setFavorite] = useState(false);
  const showTimer = useRef<number | null>(null);
  const hideTimer = useRef<number | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const suppressClick = useRef(false);
  // Until when the click that ends a long press is swallowed (set when that finger lifts).
  const swallowClickUntil = useRef(0);

  const clearTimers = () => {
    if (showTimer.current !== null) window.clearTimeout(showTimer.current);
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    showTimer.current = null;
    hideTimer.current = null;
  };

  const close = useCallback(() => {
    clearTimers();
    setAnchor(null);
  }, []);

  const scheduleHide = useCallback(() => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setAnchor((current) => (current?.mode === 'hover' ? null : current)), HIDE_DELAY_MS);
  }, []);

  // Load what the card shows whenever it opens on a new team.
  const teamKey = anchor?.teamKey ?? '';
  const eventKey = anchor?.eventKey ?? '';
  useEffect(() => {
    if (!teamKey) return undefined;
    let cancelled = false;
    setData(null);
    setFailed(false);
    setFavorite(readFavoriteTeams().includes(teamKey));
    loadTeamCenterIntel(teamKey, eventKey || undefined)
      .then((intel) => {
        if (!cancelled) setData(peekFromIntel(intel as unknown as Record<string, unknown>, teamKey));
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [teamKey, eventKey]);

  useEffect(() => {
    let press: { timer: number; x: number; y: number } | null = null;
    const cancelPress = () => {
      if (press) window.clearTimeout(press.timer);
      press = null;
    };

    const onPointerOver = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      const el = peekTarget(event.target);
      if (!el) return;
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
      if (showTimer.current !== null) window.clearTimeout(showTimer.current);
      showTimer.current = window.setTimeout(() => {
        const next = anchorFrom(el, 'hover');
        if (next && el.isConnected) setAnchor(next);
      }, HOVER_DELAY_MS);
    };
    const onPointerOut = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      const el = peekTarget(event.target);
      if (!el) return;
      const into = event.relatedTarget instanceof Node ? event.relatedTarget : null;
      if (into && (el.contains(into) || cardRef.current?.contains(into))) return;
      if (showTimer.current !== null) window.clearTimeout(showTimer.current);
      showTimer.current = null;
      scheduleHide();
    };
    const onFocusIn = (event: FocusEvent) => {
      const el = peekTarget(event.target);
      if (!el || !el.matches(':focus-visible')) return;
      if (showTimer.current !== null) window.clearTimeout(showTimer.current);
      showTimer.current = window.setTimeout(() => {
        const next = anchorFrom(el, 'hover');
        if (next && document.activeElement === el) setAnchor(next);
      }, HOVER_DELAY_MS);
    };
    const onFocusOut = (event: FocusEvent) => {
      if (!peekTarget(event.target)) return;
      const into = event.relatedTarget instanceof Node ? event.relatedTarget : null;
      if (into && cardRef.current?.contains(into)) return;
      if (showTimer.current !== null) window.clearTimeout(showTimer.current);
      scheduleHide();
    };

    // Phones: hold a team for a moment. The tap that would have opened it is swallowed.
    const onPointerDown = (event: PointerEvent) => {
      if (event.pointerType !== 'touch') return;
      const el = peekTarget(event.target);
      if (!el) return;
      cancelPress();
      press = {
        x: event.clientX,
        y: event.clientY,
        timer: window.setTimeout(() => {
          press = null;
          const next = anchorFrom(el, 'sheet');
          if (!next) return;
          suppressClick.current = true;
          hapticTap();
          setAnchor(next);
        }, LONG_PRESS_MS),
      };
    };
    const onPointerMove = (event: PointerEvent) => {
      if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > MOVE_TOLERANCE_PX) cancelPress();
    };
    // The sheet opens under the finger that is still holding, so the click from
    // lifting it lands on the sheet ("Open team" or the backdrop), not on the team
    // that was pressed. Swallow that one click wherever it lands, and nothing after.
    const onPointerUp = (event: PointerEvent) => {
      cancelPress();
      if (event.pointerType === 'touch' && suppressClick.current) {
        suppressClick.current = false;
        swallowClickUntil.current = Date.now() + 500;
      }
    };
    const onPointerCancel = () => {
      cancelPress();
      suppressClick.current = false;
    };
    const onClickCapture = (event: MouseEvent) => {
      if (Date.now() > swallowClickUntil.current) return;
      swallowClickUntil.current = 0;
      event.preventDefault();
      event.stopPropagation();
    };
    const onContextMenu = (event: Event) => {
      if (peekTarget(event.target) && (press || suppressClick.current)) event.preventDefault();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    const onScroll = () => setAnchor((current) => (current?.mode === 'hover' ? null : current));

    document.addEventListener('pointerover', onPointerOver);
    document.addEventListener('pointerout', onPointerOut);
    document.addEventListener('focusin', onFocusIn);
    document.addEventListener('focusout', onFocusOut);
    document.addEventListener('pointerdown', onPointerDown, { passive: true });
    document.addEventListener('pointermove', onPointerMove, { passive: true });
    document.addEventListener('pointerup', onPointerUp, { passive: true });
    document.addEventListener('pointercancel', onPointerCancel, { passive: true });
    document.addEventListener('click', onClickCapture, true);
    document.addEventListener('contextmenu', onContextMenu);
    window.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      cancelPress();
      clearTimers();
      document.removeEventListener('pointerover', onPointerOver);
      document.removeEventListener('pointerout', onPointerOut);
      document.removeEventListener('focusin', onFocusIn);
      document.removeEventListener('focusout', onFocusOut);
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', onPointerUp);
      document.removeEventListener('pointercancel', onPointerCancel);
      document.removeEventListener('click', onClickCapture, true);
      document.removeEventListener('contextmenu', onContextMenu);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [close, scheduleHide]);

  if (!anchor || typeof document === 'undefined') return null;
  const number = anchor.teamKey.replace(/^frc/, '');
  const sheet = anchor.mode === 'sheet';
  const position = sheet ? undefined : cardPosition(anchor.rect);
  const eventParam = anchor.eventKey ? `event=${anchor.eventKey}&` : '';

  const go = (to: string) => {
    close();
    navigateWithTransition(navigate, to, { direction: 'forward' });
  };
  // Adds this team to the teams already being compared (up to four), rather than replacing them.
  const compareWith = () => {
    let current: string[] = [];
    try {
      const parsed = JSON.parse(window.localStorage.getItem(COMPARE_TEAMS_STORAGE) || '[]') as unknown;
      if (Array.isArray(parsed)) current = parsed.map((value) => String(value).toLowerCase()).filter((value) => /^frc\d+$/.test(value));
    } catch {
      current = [];
    }
    const teams = [...current.filter((key) => key !== anchor.teamKey), anchor.teamKey].slice(-4);
    go(`/compare?teams=${teams.join(',')}`);
  };
  const toggleFavorite = () => {
    const current = readFavoriteTeams();
    const next = current.includes(anchor.teamKey) ? current.filter((key) => key !== anchor.teamKey) : [anchor.teamKey, ...current];
    saveFavoriteTeams(next);
    const added = next.includes(anchor.teamKey);
    setFavorite(added);
    showToast({ message: added ? `Team ${number} added to favorites` : `Team ${number} removed from favorites`, tone: 'info' });
  };

  const card = (
    <div
      ref={cardRef}
      className={`${styles.card} ${sheet ? styles.sheet : ''}`}
      style={position ? { left: position.left, top: position.top, width: CARD_WIDTH } : undefined}
      role="dialog"
      aria-label={`Team ${number} at a glance`}
      onPointerEnter={() => {
        if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === 'mouse') scheduleHide();
      }}
      onBlur={(event) => {
        if (!sheet && !(event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget))) scheduleHide();
      }}
    >
      <div className={styles.head}>
        <div className={styles.identity}>
          <strong className={styles.number}>{number}</strong>
          <span className={styles.name}>{data?.nickname ?? (failed ? 'Team details unavailable' : 'Loading…')}</span>
        </div>
        {data?.rating != null ? (
          <div className={styles.rating}>
            <strong>{data.rating.toFixed(1)}</strong>
            <span>rating</span>
          </div>
        ) : null}
      </div>
      <div className={styles.stats}>
        <div><strong>{data ? data.fuelRate ?? '—' : '…'}</strong><span>Fuel / active min</span></div>
        <div><strong>{data ? data.climb ?? '—' : '…'}</strong><span>Climb</span></div>
        <div><strong>{data ? data.record ?? '—' : '…'}</strong><span>{data?.recordLabel ?? 'Record'}</span></div>
      </div>
      {data?.status ? <p className={styles.status}>{data.status}</p> : null}
      <div className={styles.actions}>
        <button type="button" className={styles.primary} onClick={() => go(`/team-center?${eventParam}team=${anchor.teamKey}`)}>Open team</button>
        <button type="button" className={styles.secondary} onClick={compareWith}>Compare</button>
        <button type="button" className={styles.secondary} onClick={toggleFavorite} aria-pressed={favorite}>
          {favorite ? 'Favorited' : 'Favorite'}
        </button>
      </div>
    </div>
  );

  return createPortal(
    sheet ? (
      <div className={styles.sheetLayer}>
        <div className={styles.scrim} onClick={close} aria-hidden="true" />
        {card}
      </div>
    ) : card,
    document.body,
  );
}
