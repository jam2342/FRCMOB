import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { EventScheduleItem } from '../api';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { liveTimerLabel, matchStartTime } from '../pages/centerUtils';
import { inferMatchCompleted } from '../pages/matchStatus';
import { formatDayTime, formatTime } from '../utils/formatTime';
import { matchLabel, matchLabelFromKey } from '../utils/matchLabel';
import { FieldText } from './ui/primitives';
import styles from './MatchSelect.module.css';

// The one way to pick a match from a schedule. Live Scouting and the recorder each
// did it differently (a button grid on phones and a dropdown on desktop, a dropdown with
// "(played)", a free-text box). Now all three use this: tappable buttons with a search box on
// phones, where phone scouting deliberately has no dropdowns, and a dropdown on desktop.
type Props = {
  matches: readonly EventScheduleItem[];
  value: string;
  onChange: (matchKey: string) => void;
  label?: string;
  id?: string;
  loading?: boolean;
  // When set, the first choice is "no particular match" with this wording (a filter, not a pick).
  anyLabel?: string;
  emptyText?: string;
  disabled?: boolean;
};

type Choice = { key: string; name: string; text: string; live: boolean };

function nameOf(match: EventScheduleItem): string {
  return match.display_name
    || (match.comp_level ? matchLabel(match.comp_level, match.set_number, match.match_number) : null)
    || matchLabelFromKey(match.match_key)
    || match.match_key;
}

function isQual(match: EventScheduleItem): boolean {
  return match.comp_level ? match.comp_level === 'qm' : /_qm\d+$/i.test(match.match_key);
}

export function MatchSelect({ matches, value, onChange, label = 'Match', id, loading = false, anyLabel, emptyText = 'No schedule yet', disabled }: Props) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  const phone = useMobileLayout();
  const [query, setQuery] = useState('');
  const gridRef = useRef<HTMLDivElement | null>(null);

  const groups = useMemo(() => {
    const nowMs = Date.now();
    const days = new Set(matches.map((m) => {
      const start = matchStartTime(m);
      return start ? new Date(start * 1000).toDateString() : '';
    }).filter(Boolean));
    const showDay = days.size > 1;
    const toChoice = (m: EventScheduleItem): Choice => {
      const startSec = matchStartTime(m);
      const played = inferMatchCompleted(m, nowMs);
      const live = !played && liveTimerLabel(startSec, nowMs).state === 'live';
      const parts = [nameOf(m)];
      if (live) parts.push('now');
      else if (startSec) parts.push(showDay ? formatDayTime(startSec * 1000) : formatTime(startSec * 1000));
      if (played) parts.push('played');
      return { key: m.match_key.toLowerCase(), name: nameOf(m), text: parts.join(' · '), live };
    };
    return [
      { label: 'Qualifications', items: matches.filter(isQual).map(toChoice) },
      { label: 'Playoffs', items: matches.filter((m) => !isQual(m)).map(toChoice) },
    ].filter((g) => g.items.length);
  }, [matches]);

  const normalized = value.trim().toLowerCase();
  const known = matches.some((m) => m.match_key.toLowerCase() === normalized);
  // A match chosen before the schedule arrived (a link, a saved pick) still shows as chosen.
  const orphan = normalized && !known ? normalized : '';
  const pending = loading && matches.length === 0 ? '' : undefined;

  // Keep the chosen match in view inside the button list (QM 60 shouldn't sit 30 rows down),
  // including after a search is cleared. Scrolls the list only, never the page.
  useEffect(() => {
    if (!phone) return;
    const grid = gridRef.current;
    const pressed = grid?.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (!grid || !pressed) return;
    const top = pressed.offsetTop - grid.offsetTop;
    if (top < grid.scrollTop || top + pressed.offsetHeight > grid.scrollTop + grid.clientHeight) {
      grid.scrollTop = Math.max(0, top - grid.clientHeight / 2 + pressed.offsetHeight / 2);
    }
  }, [phone, normalized, groups, query]);

  if (phone) {
    const needle = query.trim().toLowerCase();
    const all = groups.flatMap((g) => g.items);
    // A number finds matches whose number starts with it ("12" → QM 12, QM 120–125, not QM 112);
    // words search the name ("sf" → every semifinal).
    const shown = !needle
      ? all
      : /^\d+$/.test(needle)
        ? all.filter((c) => c.name.split(/\D+/).some((part) => part.startsWith(needle)))
        : all.filter((c) => c.name.toLowerCase().includes(needle));
    const button = (key: string, text: string, ariaLabel?: string, wide = false) => (
      <button
        type="button"
        key={key || 'any'}
        className={`center-btn ${key === normalized ? '' : 'ghost'} ${wide ? styles.wide : ''}`.trim()}
        aria-pressed={key === normalized}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => onChange(key)}
      >
        {text}
      </button>
    );
    return (
      <>
        <FieldText label="Find match" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Match name or number" />
        <div ref={gridRef} className={styles.choices} role="group" aria-label={label} data-vt-pending={pending}>
          {anyLabel ? button('', anyLabel, undefined, true) : null}
          {orphan ? button(orphan, matchLabelFromKey(orphan) ?? orphan) : null}
          {shown.map((c) => button(c.key, c.live ? `${c.name} · now` : c.name))}
          {matches.length === 0 ? (
            <p className={`center-callout muted ${styles.empty}`}>{loading ? 'Loading schedule…' : emptyText}</p>
          ) : shown.length === 0 ? (
            <p className={`center-callout muted ${styles.empty}`}>Nothing matches “{query.trim()}”.</p>
          ) : null}
        </div>
      </>
    );
  }

  const placeholder = loading ? 'Loading schedule…' : matches.length === 0 ? emptyText : 'Pick a match';
  return (
    <>
      <label className="center-label" htmlFor={selectId}>{label}</label>
      <select
        id={selectId}
        className="center-input"
        value={normalized && (known || orphan) ? normalized : ''}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled || (matches.length === 0 && !anyLabel && !orphan)}
        data-vt-pending={pending}
      >
        {anyLabel ? <option value="">{anyLabel}</option> : known || orphan ? null : <option value="">{placeholder}</option>}
        {orphan ? <option value={orphan}>{matchLabelFromKey(orphan) ?? orphan}</option> : null}
        {groups.length > 1
          ? groups.map((g) => (
            <optgroup key={g.label} label={g.label}>
              {g.items.map((c) => <option key={c.key} value={c.key}>{c.text}</option>)}
            </optgroup>
          ))
          : groups[0]?.items.map((c) => <option key={c.key} value={c.key}>{c.text}</option>)}
      </select>
    </>
  );
}
