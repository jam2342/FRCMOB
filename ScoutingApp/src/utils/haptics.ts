/**
 * Haptic feedback helpers.
 *
 * Scouts watch the robot, not the screen — a short vibration confirms a
 * counter tap registered without needing to look down. In the native app the
 * Capacitor Haptics plugin drives the Taptic Engine, so iPhones buzz too; in a
 * browser the Vibration API does (Android). No-ops silently everywhere else
 * (iOS Safari, desktops).
 */
import { isNativeApp } from '../platform/runtime';

type HapticsPlugin = typeof import('@capacitor/haptics');

let nativeHaptics: Promise<HapticsPlugin | null> | null = null;

function loadNativeHaptics(): Promise<HapticsPlugin | null> {
  if (!nativeHaptics) {
    nativeHaptics = import('@capacitor/haptics').catch(() => null);
  }
  return nativeHaptics;
}

type NativeCall = (plugin: HapticsPlugin) => Promise<void>;

function buzz(pattern: number | number[], native: NativeCall): void {
  try {
    if (isNativeApp()) {
      void loadNativeHaptics()
        .then((plugin) => (plugin ? native(plugin) : undefined))
        .catch(() => {
          // Haptic not available — silently ignore
        });
      return;
    }
    if (typeof navigator !== 'undefined' && 'vibrate' in navigator) {
      navigator.vibrate(pattern);
    }
  } catch {
    // Haptic not available — silently ignore
  }
}

/** Light tick for counter increments, scale selections and navigation. */
export function hapticTap(): void {
  buzz(8, ({ Haptics, ImpactStyle }) => Haptics.impact({ style: ImpactStyle.Light }));
}

/** Slightly stronger pulse for decrements/corrections so they feel distinct. */
export function hapticUndo(): void {
  buzz([12, 30, 12], ({ Haptics, ImpactStyle }) => Haptics.impact({ style: ImpactStyle.Medium }));
}

/** Confirmation buzz for saving an entry. */
export function hapticSuccess(): void {
  buzz([10, 40, 20], ({ Haptics, NotificationType }) => Haptics.notification({ type: NotificationType.Success }));
}

/** A long, unmistakable pattern for the match moving on (auto over, endgame starting):
 *  felt in a pocket or a hand, unlike a tap. */
export function hapticPhase(): void {
  buzz([60, 80, 60, 80, 60], ({ Haptics, NotificationType }) => Haptics.notification({ type: NotificationType.Warning }));
}
