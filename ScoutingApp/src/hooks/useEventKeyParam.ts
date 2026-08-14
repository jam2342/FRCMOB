import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * Shared hook that manages an event key from URL search-params + localStorage,
 * keeping them in sync.  Every page that needs an "event key" input can use
 * this instead of repeating the ~20-line boilerplate.
 *
 * `fetchTrigger` increments on every explicit user action (select / commit),
 * even when the key hasn't changed.  Depend on it to force re-fetches.
 *
 * @param storageKey  localStorage key to persist the last-used event key.
 */
export function useEventKeyParam(storageKey: string) {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlEvent = searchParams.get('event') || '';

  const [eventKey, setEventKey] = useState<string>(
    () => urlEvent || localStorage.getItem(storageKey) || '',
  );
  const [eventInput, setEventInput] = useState(eventKey);
  const fetchTriggerRef = useRef(0);
  const [fetchTrigger, setFetchTrigger] = useState(0);

  /* URL → state */
  useEffect(() => {
    if (!urlEvent) return;
    setEventKey((current) => {
      if (urlEvent === current) return current;
      setEventInput(urlEvent);
      fetchTriggerRef.current++;
      setFetchTrigger(fetchTriggerRef.current);
      return urlEvent;
    });
  }, [urlEvent]);

  /* state → URL + localStorage */
  useEffect(() => {
    if (eventKey) {
      localStorage.setItem(storageKey, eventKey);
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set('event', eventKey);
        return next;
      }, { replace: true });
    }
  }, [eventKey, setSearchParams, storageKey]);

  /** Call when the user confirms the typed input (Enter / button click). */
  function commitInput() {
    const normalized = eventInput.trim().toLowerCase();
    if (!normalized) return;
    setEventKey(normalized);
    setEventInput(normalized);
    fetchTriggerRef.current++;
    setFetchTrigger(fetchTriggerRef.current);
  }

  /** Programmatically set the event key (e.g. from an autocomplete pick). */
  function selectEvent(key: string) {
    const normalized = key.trim().toLowerCase();
    if (!normalized) return;
    setEventKey(normalized);
    setEventInput(normalized);
    fetchTriggerRef.current++;
    setFetchTrigger(fetchTriggerRef.current);
  }

  return {
    eventKey,
    eventInput,
    setEventInput,
    commitInput,
    selectEvent,
    /** Increments on every explicit user action — depend on this in your
     *  fetch `useEffect` to guarantee re-fetches even for the same key. */
    fetchTrigger,
  } as const;
}
