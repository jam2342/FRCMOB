import { useEffect, useState } from 'react';

/* The width the whole product switches layout at. Exported so a component that
   needs the same threshold in a prop — Table's cardBreakpoint, say — spells it
   the same way the hook does instead of repeating the number. */
export const MOBILE_LAYOUT_BREAKPOINT = 1120;

export function useMobileLayout(maxWidth = MOBILE_LAYOUT_BREAKPOINT) {
  const query = `(max-width: ${maxWidth}px)`;
  const [isMobileLayout, setIsMobileLayout] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mediaQuery = window.matchMedia(query);
    const onChange = (event: MediaQueryListEvent) => setIsMobileLayout(event.matches);
    mediaQuery.addEventListener('change', onChange);
    return () => mediaQuery.removeEventListener('change', onChange);
  }, [query]);

  return isMobileLayout;
}
