import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

export function useEventKeyParam(storageKey: string) {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlEvent = (searchParams.get('event') || '').trim().toLowerCase();
  const [initialEvent] = useState(() => {
    try { return (localStorage.getItem(storageKey) || '').trim().toLowerCase(); }
    catch { return ''; }
  });
  // The URL is authoritative during Back/Forward and direct-link navigation.
  // Mirroring it into state while another effect writes state back to the URL
  // let the two effects repeatedly restore each other's previous event.
  const eventKey = urlEvent || initialEvent;
  const [eventInput, setEventInput] = useState(eventKey);
  const [fetchTrigger, setFetchTrigger] = useState(0);

  useEffect(() => { setEventInput(eventKey); }, [eventKey]);
  useEffect(() => {
    if (!eventKey) return;
    try { localStorage.setItem(storageKey, eventKey); } catch { /* The URL still carries the event. */ }
    if (!urlEvent) {
      setSearchParams(prev => {
        const next = new URLSearchParams(prev);
        next.set('event', eventKey);
        return next;
      }, { replace: true });
    }
  }, [eventKey, urlEvent, setSearchParams, storageKey]);

  function selectEvent(key: string) {
    const normalized = key.trim().toLowerCase();
    if (!normalized) return;
    setEventInput(normalized);
    setSearchParams(prev => {
      const next = new URLSearchParams(prev);
      next.set('event', normalized);
      return next;
    }, { replace: true });
    // Retry the same event even when its key did not change.
    setFetchTrigger(current => current + 1);
  }

  function commitInput() { selectEvent(eventInput); }

  return { eventKey, eventInput, setEventInput, commitInput, selectEvent, fetchTrigger } as const;
}
