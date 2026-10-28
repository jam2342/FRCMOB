export type ThemeMode = 'dark' | 'light';
import { storageGet, storageSet } from '../platform/safeStorage';
import { isEventKeyShape } from '../utils/eventKey';
export type DensityMode = 'comfortable' | 'compact';
export type UIMode = 'simple' | 'full';
export type QuickJumpRegion = 'all' | 'usa' | 'canada' | 'international' | 'tx' | 'ca' | 'mi' | 'ny';
export type TutorialScope =
  | 'home'
  | 'events'
  | 'scouting'
  | 'match-center'
  | 'team-center'
  | 'compare'
  | 'favorites'
  | 'settings'
  | 'my-team'
  | 'picklist'
  | 'pit-scouting'
  | 'assignments'
  | 'predictions'
  | 'strategy'
  | 'alliance-advisor';

export const TUTORIAL_SCOPES: TutorialScope[] = [
  'home',
  'events',
  'scouting',
  'match-center',
  'team-center',
  'compare',
  'favorites',
  'settings',
  'my-team',
  'picklist',
  'pit-scouting',
  'assignments',
  'predictions',
  'strategy',
  'alliance-advisor',
];

export const SCOUTING_SETTINGS_UPDATED_EVENT = 'scouting:settings-updated';

const SETTINGS_KEYS = {
  theme: 'scouting_theme_mode',
  density: 'scouting_density_mode',
  uiMode: 'scouting_ui_mode',
  quickJumpRegion: 'scouting_quick_jump_region',
  liveRefreshSec: 'scouting_live_refresh_sec',
  tutorialAutoplay: 'scouting_tutorial_autoplay',
} as const;

export const FAVORITES_KEYS = {
  teams: 'scouting_favorite_teams',
  events: 'scouting_favorite_events',
} as const;

const DEFAULTS = {
  theme: 'dark' as ThemeMode,
  density: 'comfortable' as DensityMode,
  uiMode: 'full' as UIMode,
  quickJumpRegion: 'all' as QuickJumpRegion,
  liveRefreshSec: 60,
  // Off: tours opening on their own blocked every section on a first visit.
  tutorialAutoplay: false,
};

function normalizeThemeMode(value: string | null): ThemeMode {
  return value === 'light' ? 'light' : 'dark';
}

function normalizeDensityMode(value: string | null): DensityMode {
  return value === 'compact' ? 'compact' : 'comfortable';
}

function normalizeUiMode(value: string | null): UIMode {
  return value === 'simple' ? 'simple' : 'full';
}

function normalizeQuickJumpRegion(value: string | null): QuickJumpRegion {
  if (
    value === 'usa' ||
    value === 'canada' ||
    value === 'international' ||
    value === 'tx' ||
    value === 'ca' ||
    value === 'mi' ||
    value === 'ny'
  ) {
    return value;
  }
  return 'all';
}

function normalizeLiveRefreshSec(value: string | null): number {
  // `Number(null)` is 0, not NaN, so the isFinite check alone never reached the
  // default — an unset key clamped to the 5s floor instead of the intended 60,
  // and every fresh browser polled twelve times harder than designed.
  if (value === null || value.trim() === '') return DEFAULTS.liveRefreshSec;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULTS.liveRefreshSec;
  return Math.max(5, Math.min(120, Math.round(parsed)));
}

function normalizeTutorialAutoplay(value: string | null): boolean {
  if (value === 'true' || value === '1') return true;
  if (value === 'false' || value === '0') return false;
  return DEFAULTS.tutorialAutoplay;
}

export type ScoutingSettings = {
  theme: ThemeMode;
  density: DensityMode;
  uiMode: UIMode;
  quickJumpRegion: QuickJumpRegion;
  liveRefreshSec: number;
  tutorialAutoplay: boolean;
};

export function getStoredSettings(): ScoutingSettings {
  return {
    theme: normalizeThemeMode(storageGet(SETTINGS_KEYS.theme)),
    density: normalizeDensityMode(storageGet(SETTINGS_KEYS.density)),
    uiMode: normalizeUiMode(storageGet(SETTINGS_KEYS.uiMode)),
    quickJumpRegion: normalizeQuickJumpRegion(storageGet(SETTINGS_KEYS.quickJumpRegion)),
    liveRefreshSec: normalizeLiveRefreshSec(storageGet(SETTINGS_KEYS.liveRefreshSec)),
    tutorialAutoplay: normalizeTutorialAutoplay(storageGet(SETTINGS_KEYS.tutorialAutoplay)),
  };
}

export function saveStoredSettings(partial: Partial<ScoutingSettings>): ScoutingSettings {
  const current = getStoredSettings();
  const next: ScoutingSettings = {
    theme: partial.theme ?? current.theme ?? DEFAULTS.theme,
    density: partial.density ?? current.density ?? DEFAULTS.density,
    uiMode: partial.uiMode ?? current.uiMode ?? DEFAULTS.uiMode,
    quickJumpRegion: partial.quickJumpRegion ?? current.quickJumpRegion ?? DEFAULTS.quickJumpRegion,
    liveRefreshSec: partial.liveRefreshSec ?? current.liveRefreshSec ?? DEFAULTS.liveRefreshSec,
    tutorialAutoplay: partial.tutorialAutoplay ?? current.tutorialAutoplay ?? DEFAULTS.tutorialAutoplay,
  };

  storageSet(SETTINGS_KEYS.theme, next.theme);
  storageSet(SETTINGS_KEYS.density, next.density);
  storageSet(SETTINGS_KEYS.uiMode, next.uiMode);
  storageSet(SETTINGS_KEYS.quickJumpRegion, next.quickJumpRegion);
  storageSet(SETTINGS_KEYS.liveRefreshSec, String(next.liveRefreshSec));
  storageSet(SETTINGS_KEYS.tutorialAutoplay, next.tutorialAutoplay ? 'true' : 'false');

  return next;
}

export function applyBodySettingsClasses(settings: ScoutingSettings): void {
  const body = document.body;
  const root = document.documentElement;

  body.classList.remove('theme-dark', 'theme-light');
  body.classList.add(settings.theme === 'light' ? 'theme-light' : 'theme-dark');
  body.classList.remove('density-comfortable', 'density-compact');
  body.classList.add(settings.density === 'compact' ? 'density-compact' : 'density-comfortable');

  root.classList.remove('theme-dark', 'theme-light');
  root.classList.add(settings.theme === 'light' ? 'theme-light' : 'theme-dark');
  root.classList.remove('density-comfortable', 'density-compact');
  root.classList.add(settings.density === 'compact' ? 'density-compact' : 'density-comfortable');
  syncBrowserChrome();
}

// The phone's status bar and the browser's toolbar take the page's own background. The app's
// theme is its own setting, so the OS-scheme tags in index.html only cover the first paint.
function syncBrowserChrome(): void {
  const background = getComputedStyle(document.documentElement).getPropertyValue('--color-bg').trim();
  if (!background) return;
  const tags = [...document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')];
  const [first, ...rest] = tags;
  rest.forEach((tag) => tag.remove());
  const tag = first ?? document.head.appendChild(Object.assign(document.createElement('meta'), { name: 'theme-color' }));
  tag.removeAttribute('media');
  tag.content = background;
}

export function emitSettingsUpdated(settings?: ScoutingSettings): void {
  const detail = settings || getStoredSettings();
  window.dispatchEvent(new CustomEvent<ScoutingSettings>(SCOUTING_SETTINGS_UPDATED_EVENT, { detail }));
}

export function readFavoriteTeams(): string[] {
  const raw = storageGet(FAVORITES_KEYS.teams);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((value) => String(value || '').trim().toLowerCase())
      .filter((value, idx, array) => /^frc\d+$/.test(value) && array.indexOf(value) === idx);
  } catch {
    return [];
  }
}

export function readFavoriteEvents(): string[] {
  const raw = storageGet(FAVORITES_KEYS.events);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((value) => String(value || '').trim().toLowerCase())
      .filter((value, idx, array) => isEventKeyShape(value) && array.indexOf(value) === idx);
  } catch {
    return [];
  }
}

export function saveFavoriteTeams(teamKeys: string[]): string[] {
  const normalized = teamKeys
    .map((value) => String(value || '').trim().toLowerCase())
    .filter((value, idx, array) => /^frc\d+$/.test(value) && array.indexOf(value) === idx);
  storageSet(FAVORITES_KEYS.teams, JSON.stringify(normalized));
  return normalized;
}

export function saveFavoriteEvents(eventKeys: string[]): string[] {
  const normalized = eventKeys
    .map((value) => String(value || '').trim().toLowerCase())
    .filter((value, idx, array) => isEventKeyShape(value) && array.indexOf(value) === idx);
  storageSet(FAVORITES_KEYS.events, JSON.stringify(normalized));
  return normalized;
}
