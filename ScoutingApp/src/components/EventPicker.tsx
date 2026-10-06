import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { type EventSearchItem } from '../api';
import { loadSeasonEventCatalog } from '../features/events/eventCatalog';
import { smartSearchEvents } from '../utils/eventSearch';
import { CURRENT_SEASON_YEAR, FALLBACK_SEASON_YEAR } from '../pages/centerUtils';

const EVENT_PICKER_SUGGEST_LIMIT = 60;
const EVENT_PICKER_REMOTE_TEAM_COUNT_FETCH_LIMIT = 0;

/* ------------------------------------------------------------------ */
/*  Helpers (mirrored from EventsPage so styles match)                 */
/* ------------------------------------------------------------------ */

function eventLocation(event: EventSearchItem): string {
  const parts = [event.city, event.state_prov, event.country]
    .map((v) => (v || '').trim())
    .filter(Boolean);
  return parts.length > 0 ? parts.join(', ') : 'Location unavailable';
}

function eventTypeTags(event: EventSearchItem): string[] {
  const name = (event.name || '').toLowerCase();
  const tags: string[] = [];
  if (name.includes('district')) tags.push('District');
  if (name.includes('regional')) tags.push('Regional');
  if (name.includes('league')) tags.push('League');
  if (name.includes('championship') || name.includes('cmp')) tags.push('Championship');
  if (tags.length === 0) tags.push('Event');
  return tags;
}

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface EventPickerProps {
  /** The current committed event key (controls the "active" highlight). */
  value: string;
  /** Called when the user selects an event from the suggestions. */
  onSelect: (eventKey: string) => void;
  /** Current text in the input — managed externally so the parent
   *  retains full control (e.g. via useEventKeyParam). */
  inputValue: string;
  /** Update the raw input text. */
  onInputChange: (value: string) => void;
  /** Called when the user manually submits (Enter / button click)
   *  without picking from the dropdown.  Typically `commitInput`. */
  onSubmit?: () => void;
  /** Button / loading label. */
  loading?: boolean;
  /** Placeholder text. */
  placeholder?: string;
  /** Extra class on the outer wrapper. */
  className?: string;
  /** Whether to disable the input. */
  disabled?: boolean;
  /** Button text while typing; pages that add rather than load an event say so. */
  actionLabel?: string;
  /** Empty the box after a pick (adding to a list) instead of showing the chosen event. */
  clearOnSelect?: boolean;
  /** Lets a visible <label> point at the input. */
  inputId?: string;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function EventPicker({
  value,
  onSelect,
  inputValue,
  onInputChange,
  onSubmit,
  loading = false,
  placeholder = 'Search events — try "houston district" or "2026txhou"',
  className,
  disabled,
  actionLabel = 'Load event',
  clearOnSelect = false,
  inputId,
}: EventPickerProps) {
  /* ---- Internal state ---- */
  const [suggestions, setSuggestions] = useState<EventSearchItem[]>([]);
  const [searchResults, setSearchResults] = useState<EventSearchItem[]>([]);
  // The query the current results answer; Enter must not pick a result for older text.
  const [resultsQuery, setResultsQuery] = useState('');
  // Enter pressed before the search for that text answered: pick its top result when it does.
  const [pendingSubmitQuery, setPendingSubmitQuery] = useState<string | null>(null);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [open, setOpen] = useState(false);
  const searchSeqRef = useRef(0);
  const debounceRef = useRef<number | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  /* ---- Dropdown positioning (portal renders at body level) ---- */
  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number; width: number }>({
    top: 0,
    left: 0,
    width: 300,
  });

  /** Recompute dropdown position based on the input row's bounding rect. */
  const updateDropdownPos = useCallback(() => {
    if (!wrapperRef.current) return;
    const rect = wrapperRef.current.getBoundingClientRect();
    setDropdownPos({
      top: rect.bottom + 4,
      left: rect.left,
      width: rect.width,
    });
  }, []);

  /* ---- Load suggested events once ---- */
  useEffect(() => {
    let cancelled = false;
    async function run() {
      setLoadingSuggestions(true);
      try {
        const events = await loadSeasonEventCatalog({
          preferredYear: CURRENT_SEASON_YEAR,
          fallbackYear: FALLBACK_SEASON_YEAR,
          limit: EVENT_PICKER_SUGGEST_LIMIT,
          minTarget: 1,
          preferLiveNow: true,
          remoteTeamCountFetchLimit: EVENT_PICKER_REMOTE_TEAM_COUNT_FETCH_LIMIT,
        });
        if (cancelled) return;
        setSuggestions(events);
      } catch {
        // silent — suggestions aren't critical
      } finally {
        if (!cancelled) setLoadingSuggestions(false);
      }
    }
    void run();
    return () => { cancelled = true; };
  }, []);

  /* ---- Debounced smart search as user types ---- */
  useEffect(() => {
    const query = inputValue.trim();
    if (!query || query.length < 2) {
      setSearchResults([]);
      setResultsQuery(query);
      return;
    }
    const seq = ++searchSeqRef.current;
    // Clear previous debounce
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(async () => {
      debounceRef.current = null;
      setLoadingSearch(true);
      try {
        const result = await smartSearchEvents(query, {
          preferredYear: CURRENT_SEASON_YEAR,
          fallbackYear: FALLBACK_SEASON_YEAR,
          maxResults: 30,
          seedEvents: suggestions,
          fastMode: true,
          localOnly: true,
          maxNetworkVariants: 0,
          includeSuggestedFallback: false,
        });
        if (seq !== searchSeqRef.current) return;
        setSearchResults(result.events.slice(0, 30));
        setResultsQuery(query);
      } catch {
        if (seq === searchSeqRef.current) {
          setSearchResults([]);
          setResultsQuery(query);
        }
      } finally {
        if (seq === searchSeqRef.current) setLoadingSearch(false);
      }
    }, 200);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, [inputValue, suggestions]);

  /* ---- Close dropdown on outside click ---- */
  useEffect(() => {
    function handlePointerDown(e: MouseEvent) {
      const target = e.target as Node;
      // Ignore clicks inside the wrapper OR the portal dropdown
      if (wrapperRef.current?.contains(target)) return;
      if (dropdownRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, []);

  /* ---- Reposition dropdown when open or on scroll/resize ---- */
  useLayoutEffect(() => {
    if (!open) return;
    updateDropdownPos();
    window.addEventListener('scroll', updateDropdownPos, true);
    window.addEventListener('resize', updateDropdownPos);
    return () => {
      window.removeEventListener('scroll', updateDropdownPos, true);
      window.removeEventListener('resize', updateDropdownPos);
    };
  }, [open, updateDropdownPos]);

  /* ---- Derived lists ---- */
  const committedKey = value.trim().toLowerCase();
  // Once an event is chosen, show its name instead of the raw key, and drop the
  // button until the person types something new.
  const showingCommitted = committedKey !== '' && inputValue.trim().toLowerCase() === committedKey;
  const committedName = showingCommitted
    ? [...searchResults, ...suggestions].find((event) => event.event_key.toLowerCase() === committedKey)?.name
    : undefined;
  const hasQuery = inputValue.trim().length > 0 && !showingCommitted;
  const resultsAreCurrent = resultsQuery === inputValue.trim();
  const currentResults = resultsAreCurrent ? searchResults : [];
  const displayList = hasQuery ? currentResults : suggestions;
  const isSearching = hasQuery ? loadingSearch || !resultsAreCurrent : loadingSuggestions;
  // Stay open after a finished search with no hits, so "No events found" is actually seen.
  const showDropdown = open && (displayList.length > 0 || isSearching || hasQuery);

  /* ---- Handlers ---- */
  const handleSelect = useCallback(
    (eventKey: string) => {
      const normalized = eventKey.trim().toLowerCase();
      // Cancel any in-flight debounced search so it doesn't re-render mid-action
      searchSeqRef.current++;
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      onSelect(normalized);
      onInputChange(clearOnSelect ? '' : normalized);
      setOpen(false);
    },
    [clearOnSelect, onSelect, onInputChange],
  );

  function submitCurrent() {
    const query = inputValue.trim();
    if (hasQuery && query.length >= 2 && !resultsAreCurrent) {
      setPendingSubmitQuery(query);
      return;
    }
    if (hasQuery && currentResults.length > 0) {
      handleSelect(currentResults[0].event_key);
    } else {
      onSubmit?.();
    }
    setOpen(false);
  }

  useEffect(() => {
    if (pendingSubmitQuery === null) return;
    if (pendingSubmitQuery !== inputValue.trim()) {
      setPendingSubmitQuery(null);
      return;
    }
    if (resultsQuery !== pendingSubmitQuery) return;
    setPendingSubmitQuery(null);
    if (searchResults.length > 0) handleSelect(searchResults[0].event_key);
    else onSubmit?.();
    setOpen(false);
  }, [handleSelect, inputValue, onSubmit, pendingSubmitQuery, resultsQuery, searchResults]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      // If there's a top search result, select it; otherwise submit raw input
      submitCurrent();
    }
    if (e.key === 'Escape') {
      setOpen(false);
    }
  }

  /* ---- Portal dropdown ---- */
  const dropdownPortal = showDropdown
    ? createPortal(
        <div
          ref={dropdownRef}
          className="event-picker-dropdown"
          role="listbox"
          aria-label="Event suggestions"
          style={{
            position: 'fixed',
            top: dropdownPos.top,
            left: dropdownPos.left,
            width: dropdownPos.width,
          }}
        >
          {isSearching && displayList.length === 0 ? (
            <p className="event-picker-dropdown-hint">Searching…</p>
          ) : null}
          {!isSearching && hasQuery && displayList.length === 0 && inputValue.trim().length < 2 ? (
            <p className="event-picker-dropdown-hint">Keep typing to search.</p>
          ) : null}
          {!isSearching && hasQuery && displayList.length === 0 && inputValue.trim().length >= 2 ? (
            <p className="event-picker-dropdown-hint">No events match that. Try the city, the event name or a code like 2026txhou.</p>
          ) : null}
          {!hasQuery && displayList.length === 0 && !isSearching ? (
            <p className="event-picker-dropdown-hint">No suggested events available.</p>
          ) : null}
          {displayList.map((event) => (
            <button
              key={`ep-${event.event_key}`}
              type="button"
              role="option"
              aria-selected={value === event.event_key.toLowerCase()}
              className={`event-picker-dropdown-item ${
                value === event.event_key.toLowerCase() ? 'active' : ''
              }`.trim()}
              /* Use onMouseDown so selection fires immediately, before any
                 debounced re-render can shuffle the list and steal the click. */
              onMouseDown={(e) => {
                e.preventDefault();        // keep focus on input
                handleSelect(event.event_key);
              }}
              // Enter/Space on a focused suggestion fires click, not mousedown.
              onClick={(e) => {
                if (e.detail === 0) handleSelect(event.event_key);
              }}
            >
              <div className="event-picker-dropdown-item-main">
                <strong>{event.name}</strong>
                <span className="event-picker-dropdown-item-key">{event.event_key}</span>
              </div>
              <div className="event-picker-dropdown-item-meta">
                <span>{eventLocation(event)}</span>
                <span className="event-picker-dropdown-item-tags">
                  {eventTypeTags(event).map((tag) => (
                    <span key={`${event.event_key}-${tag}`} className="event-picker-tag">
                      {tag}
                    </span>
                  ))}
                  <span className="event-picker-tag subtle">{event.year}</span>
                </span>
              </div>
            </button>
          ))}
        </div>,
        document.body,
      )
    : null;

  return (
    <div
      ref={wrapperRef}
      className={`event-picker-wrapper ${className || ''}`.trim()}
    >
      <div className="center-input-row event-picker-input-row">
        <input
          ref={inputRef}
          id={inputId}
          className="center-input"
          value={committedName || inputValue}
          onChange={(e) => {
            onInputChange(e.target.value);
            setOpen(true);
          }}
          onFocus={(e) => {
            updateDropdownPos();
            setOpen(true);
            if (showingCommitted) e.currentTarget.select();
          }}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          // With a visible label (inputId) the label names the box; an aria-label would override it.
          aria-label={inputId ? undefined : 'Search events'}
          disabled={disabled}
          autoComplete="off"
        />
        {showingCommitted && !loading ? null : (
        <button
          type="button"
          className="center-btn"
          onClick={() => {
            submitCurrent();
          }}
          disabled={loading || disabled}
        >
          {loading ? 'Loading…' : actionLabel}
        </button>
        )}
      </div>
      {dropdownPortal}
    </div>
  );
}
