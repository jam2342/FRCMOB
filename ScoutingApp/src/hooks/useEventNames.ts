import { useEffect, useState } from 'react';
import { eventDisplayName, preloadEventSearchIndex } from '../utils/eventSearchIndex';

// Names come from the event index the app already loads at startup, so a list
// of saved reports never fetches a schedule per event just to learn its name.
// `knownKey`/`knownName` let a page that has the name in hand use it first.
export function useEventNames(knownKey = '', knownName = '') {
  const [, setIndexVersion] = useState(0);
  useEffect(() => {
    let live = true;
    void preloadEventSearchIndex().then(() => {
      if (live) setIndexVersion((version) => version + 1);
    });
    return () => { live = false; };
  }, []);
  return (key: string) => (knownKey && key === knownKey && knownName) || eventDisplayName(key);
}
