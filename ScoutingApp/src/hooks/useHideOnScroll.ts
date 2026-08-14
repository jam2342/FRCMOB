import { useEffect, useRef, useState } from 'react';

/**
 * Returns whether the bottom bar should be visible based on scroll direction.
 * Hides on scroll down, shows on scroll up (like iOS Safari).
 */
export function useHideOnScroll(threshold = 8): boolean {
  const [visible, setVisible] = useState(true);
  const lastScrollY = useRef(0);
  const ticking = useRef(false);

  useEffect(() => {
    // Find the scrollable content container
    const scrollContainer =
      document.querySelector('.ps-content') as HTMLElement | null;

    if (!scrollContainer) return;

    function onScroll() {
      if (ticking.current) return;
      ticking.current = true;

      requestAnimationFrame(() => {
        const currentY = scrollContainer!.scrollTop;
        const delta = currentY - lastScrollY.current;

        if (delta > threshold && currentY > 60) {
          // Scrolling down past threshold — hide
          setVisible(false);
        } else if (delta < -threshold || currentY <= 10) {
          // Scrolling up or at top — show
          setVisible(true);
        }

        lastScrollY.current = currentY;
        ticking.current = false;
      });
    }

    scrollContainer.addEventListener('scroll', onScroll, { passive: true });
    return () => scrollContainer.removeEventListener('scroll', onScroll);
  }, [threshold]);

  return visible;
}
