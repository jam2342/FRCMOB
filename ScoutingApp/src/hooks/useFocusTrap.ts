import { useEffect, type RefObject } from 'react';

const FOCUSABLE = [
  'a[href]',
  'button:not(:disabled)',
  'input:not(:disabled)',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

// Overlays can stack (a QR dialog over an expanded card). Only the newest one
// handles Tab; otherwise the outer trap sees focus in the inner portal as
// "escaped" and pulls it back out.
const activeTraps: symbol[] = [];

// Moves focus into an overlay while it is open, keeps Tab inside it, and puts
// focus back where it came from on close — otherwise a keyboard user tabs
// through the page hidden behind it and lands at the top when it closes.
export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean) {
  useEffect(() => {
    if (!active) return undefined;
    const restoreFocusTo = document.activeElement as HTMLElement | null;
    const token = Symbol('focus-trap');
    activeTraps.push(token);

    // One frame later: the portal content is in the DOM but not yet laid out
    // on the same tick, and focusing an unlaid-out element can scroll the page.
    const raf = requestAnimationFrame(() => {
      const node = ref.current;
      if (!node) return;
      const target = node.querySelector<HTMLElement>(FOCUSABLE) ?? node;
      target.focus();
    });

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || activeTraps[activeTraps.length - 1] !== token) return;
      const node = ref.current;
      if (!node) return;
      // Filter on attributes, not on layout. `offsetParent` is null for any
      // position:fixed element and for everything under jsdom, which would
      // empty the list and silently stop the trap.
      const focusable = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => !element.hasAttribute('hidden') && element.getAttribute('aria-hidden') !== 'true',
      );
      if (focusable.length === 0) {
        event.preventDefault();
        node.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;

      // Wrap at both ends, and also catch focus that has escaped the overlay.
      if (event.shiftKey && (current === first || !node.contains(current))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (current === last || !node.contains(current))) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('keydown', onKeyDown, true);
      activeTraps.splice(activeTraps.indexOf(token), 1);
      restoreFocusTo?.focus?.();
    };
  }, [active, ref]);
}
