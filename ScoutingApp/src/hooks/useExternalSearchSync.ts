import { useCallback, useEffect, useMemo, useRef } from 'react';

// Pages here mirror their state into the query string with an effect. On its
// own that writer has two failure modes when the URL changes from outside —
// a link to the same page with other params, global search, back/forward:
//
//  - with no URL → state step, the writer puts the old state straight back,
//    so the link does nothing (Events, Compare, Scouting);
//  - with one, both run in the same commit and the writer still sees the old
//    state, so the two flip each other forever ("Maximum update depth
//    exceeded" on Team Center).
//
// This hook owns that handshake. Call it before the writer effect (effects
// run in declaration order), apply the URL in `onExternalChange`, and have the
// writer bail when `shouldWrite()` says so and call `markWritten` with what it
// wrote.
export function useExternalSearchSync(
  searchParams: URLSearchParams,
  onExternalChange: (params: URLSearchParams) => void,
) {
  const syncedRef = useRef(searchParams.toString());
  const skipNextWriteRef = useRef(false);
  const applyRef = useRef(onExternalChange);

  useEffect(() => {
    applyRef.current = onExternalChange;
  });

  useEffect(() => {
    const current = searchParams.toString();
    if (current === syncedRef.current) return;
    syncedRef.current = current;
    skipNextWriteRef.current = true;
    applyRef.current(searchParams);
  }, [searchParams]);

  const shouldWrite = useCallback(() => {
    if (!skipNextWriteRef.current) return true;
    skipNextWriteRef.current = false;
    return false;
  }, []);

  const markWritten = useCallback((search: string) => {
    syncedRef.current = search;
  }, []);

  return useMemo(() => ({ shouldWrite, markWritten }), [shouldWrite, markWritten]);
}
