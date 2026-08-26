import { useEffect, useState } from 'react';

/* Canvas has no cascade. `ctx.fillStyle = 'var(--field-canvas-bg)'` is not an
   error — it is an *invalid* value, so the assignment is ignored and the
   context keeps whatever colour it had. That is why every field render in this
   app hardcoded dark-theme colours: there was no way to reference a token from
   a draw call, so nobody tried.

   The way through is to resolve the tokens off the document and hand the draw
   code plain colour strings. Which means the canvas also has to be told when
   the theme changes — a stylesheet swap repaints the DOM, but a canvas keeps
   the pixels it was last given. Hence the observer: the theme lives in a class
   on <html>, so watching that attribute catches every route into it, including
   the one the shell applies during boot. */

function readTokens(names: readonly string[]): Record<string, string> {
  if (typeof window === 'undefined' || typeof getComputedStyle !== 'function') {
    return Object.fromEntries(names.map((name) => [name, '']));
  }
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(names.map((name) => [name, style.getPropertyValue(name).trim()]));
}

function sameTokens(a: Record<string, string>, b: Record<string, string>): boolean {
  const keys = Object.keys(b);
  if (Object.keys(a).length !== keys.length) return false;
  return keys.every((key) => a[key] === b[key]);
}

export function useCanvasTokens(names: readonly string[]): Record<string, string> {
  // The names are a fixed list per call site; keying on the joined string keeps
  // the effect from re-subscribing on every render over a fresh array literal.
  const key = names.join('|');
  const [tokens, setTokens] = useState(() => readTokens(names));

  useEffect(() => {
    if (typeof window === 'undefined' || typeof MutationObserver !== 'function') return undefined;
    const list = key.split('|');
    const update = () => {
      const next = readTokens(list);
      setTokens((prev) => (sameTokens(prev, next) ? prev : next));
    };
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, [key]);

  return tokens;
}
