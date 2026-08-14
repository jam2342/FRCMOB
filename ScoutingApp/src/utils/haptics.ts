/**
 * Haptic feedback helpers.
 *
 * Scouts watch the robot, not the screen — a short vibration confirms a
 * counter tap registered without needing to look down. No-ops silently on
 * devices/browsers without the Vibration API (iOS Safari, desktops).
 */

function vibrate(pattern: number | number[]): void {
  try {
    if (typeof navigator !== 'undefined' && 'vibrate' in navigator) {
      navigator.vibrate(pattern);
    }
  } catch {
    // Haptic not available — silently ignore
  }
}

/** Light tick for counter increments and scale selections. */
export function hapticTap(): void {
  vibrate(8);
}

/** Slightly stronger pulse for decrements/corrections so they feel distinct. */
export function hapticUndo(): void {
  vibrate([12, 30, 12]);
}

/** Confirmation buzz for saving an entry. */
export function hapticSuccess(): void {
  vibrate([10, 40, 20]);
}
