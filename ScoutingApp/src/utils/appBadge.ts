// The number on the installed app's icon: this scout's assignments still to scout. Badging API
// where the browser has it (installed PWAs on Chromium, Safari 16.4+ home-screen apps); a no-op
// everywhere else, including the native app's web view.
type BadgingNavigator = Navigator & {
  setAppBadge?: (count?: number) => Promise<void>;
  clearAppBadge?: () => Promise<void>;
};

let shown: number | null = null;

export function setAppBadgeCount(count: number): void {
  const next = Math.max(0, Math.floor(count));
  if (next === shown) return;
  const nav = (typeof navigator === 'undefined' ? null : navigator) as BadgingNavigator | null;
  if (!nav?.setAppBadge || !nav.clearAppBadge) return;
  shown = next;
  try {
    void (next > 0 ? nav.setAppBadge(next) : nav.clearAppBadge()).catch(() => { shown = null; });
  } catch {
    shown = null;
  }
}

export function clearAppBadge(): void {
  setAppBadgeCount(0);
}

export function resetAppBadgeForTests(): void {
  shown = null;
}
