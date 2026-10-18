import { applyBodySettingsClasses, emitSettingsUpdated, getStoredSettings, saveStoredSettings, type ThemeMode } from '../layout/userSettings';
import { revealThemeChange } from './viewTransition';

// The one way to change the theme from anywhere (the top-bar toggle, Settings, the ⌘K palette).
// `from` is the control that asked, so the new theme spreads out from it.
function setTheme(next: ThemeMode, from?: Element | null): void {
  if (getStoredSettings().theme === next) return;
  const rect = from?.getBoundingClientRect();
  const origin = rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : null;
  revealThemeChange(() => {
    const settings = saveStoredSettings({ theme: next });
    applyBodySettingsClasses(settings);
    emitSettingsUpdated(settings);
  }, origin);
}

export function toggleTheme(from?: Element | null): void {
  setTheme(getStoredSettings().theme === 'light' ? 'dark' : 'light', from);
}
