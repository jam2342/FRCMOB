export type ThemeMode = 'dark' | 'light';
export type DensityMode = 'comfortable' | 'compact';
export type UIMode = 'simple' | 'full';
export type QuickJumpMode = 'auto' | 'team' | 'event';
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
  | 'ops';

export const TUTORIAL_SCOPES: TutorialScope[] = [
  'home',
  'events',
  'scouting',
  'match-center',
  'team-center',
  'compare',
  'favorites',
  'settings',
  'ops',
];

export const SCOUTING_SETTINGS_UPDATED_EVENT = 'scouting:settings-updated';

const SETTINGS_KEYS = {
  theme: 'scouting_theme_mode',
  density: 'scouting_density_mode',
  uiMode: 'scouting_ui_mode',
  quickJumpMode: 'scouting_quick_jump_mode',
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
  quickJumpMode: 'auto' as QuickJumpMode,
  quickJumpRegion: 'all' as QuickJumpRegion,
  liveRefreshSec: 60,
  tutorialAutoplay: true,
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

function normalizeQuickJumpMode(value: string | null): QuickJumpMode {
  return value === 'team' || value === 'event' ? value : 'auto';
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
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULTS.liveRefreshSec;
  return Math.max(5, Math.min(120, Math.round(parsed)));
}

function normalizeTutorialAutoplay(value: string | null): boolean {
  if (value === 'false') return false;
  if (value === '0') return false;
  return true;
}

export type ScoutingSettings = {
  theme: ThemeMode;
  density: DensityMode;
  uiMode: UIMode;
  quickJumpMode: QuickJumpMode;
  quickJumpRegion: QuickJumpRegion;
  liveRefreshSec: number;
  tutorialAutoplay: boolean;
};

export function getStoredSettings(): ScoutingSettings {
  return {
    theme: normalizeThemeMode(window.localStorage.getItem(SETTINGS_KEYS.theme)),
    density: normalizeDensityMode(window.localStorage.getItem(SETTINGS_KEYS.density)),
    uiMode: normalizeUiMode(window.localStorage.getItem(SETTINGS_KEYS.uiMode)),
    quickJumpMode: normalizeQuickJumpMode(window.localStorage.getItem(SETTINGS_KEYS.quickJumpMode)),
    quickJumpRegion: normalizeQuickJumpRegion(window.localStorage.getItem(SETTINGS_KEYS.quickJumpRegion)),
    liveRefreshSec: normalizeLiveRefreshSec(window.localStorage.getItem(SETTINGS_KEYS.liveRefreshSec)),
    tutorialAutoplay: normalizeTutorialAutoplay(window.localStorage.getItem(SETTINGS_KEYS.tutorialAutoplay)),
  };
}

export function saveStoredSettings(partial: Partial<ScoutingSettings>): ScoutingSettings {
  const current = getStoredSettings();
  const next: ScoutingSettings = {
    theme: partial.theme ?? current.theme ?? DEFAULTS.theme,
    density: partial.density ?? current.density ?? DEFAULTS.density,
    uiMode: partial.uiMode ?? current.uiMode ?? DEFAULTS.uiMode,
    quickJumpMode: partial.quickJumpMode ?? current.quickJumpMode ?? DEFAULTS.quickJumpMode,
    quickJumpRegion: partial.quickJumpRegion ?? current.quickJumpRegion ?? DEFAULTS.quickJumpRegion,
    liveRefreshSec: partial.liveRefreshSec ?? current.liveRefreshSec ?? DEFAULTS.liveRefreshSec,
    tutorialAutoplay: partial.tutorialAutoplay ?? current.tutorialAutoplay ?? DEFAULTS.tutorialAutoplay,
  };

  window.localStorage.setItem(SETTINGS_KEYS.theme, next.theme);
  window.localStorage.setItem(SETTINGS_KEYS.density, next.density);
  window.localStorage.setItem(SETTINGS_KEYS.uiMode, next.uiMode);
  window.localStorage.setItem(SETTINGS_KEYS.quickJumpMode, next.quickJumpMode);
  window.localStorage.setItem(SETTINGS_KEYS.quickJumpRegion, next.quickJumpRegion);
  window.localStorage.setItem(SETTINGS_KEYS.liveRefreshSec, String(next.liveRefreshSec));
  window.localStorage.setItem(SETTINGS_KEYS.tutorialAutoplay, next.tutorialAutoplay ? 'true' : 'false');

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
}

export function emitSettingsUpdated(settings?: ScoutingSettings): void {
  const detail = settings || getStoredSettings();
  window.dispatchEvent(new CustomEvent<ScoutingSettings>(SCOUTING_SETTINGS_UPDATED_EVENT, { detail }));
}

export function readFavoriteTeams(): string[] {
  const raw = window.localStorage.getItem(FAVORITES_KEYS.teams);
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
  const raw = window.localStorage.getItem(FAVORITES_KEYS.events);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((value) => String(value || '').trim().toLowerCase())
      .filter((value, idx, array) => /^\d{4}[a-z0-9]+$/.test(value) && array.indexOf(value) === idx);
  } catch {
    return [];
  }
}

export function saveFavoriteTeams(teamKeys: string[]): string[] {
  const normalized = teamKeys
    .map((value) => String(value || '').trim().toLowerCase())
    .filter((value, idx, array) => /^frc\d+$/.test(value) && array.indexOf(value) === idx);
  window.localStorage.setItem(FAVORITES_KEYS.teams, JSON.stringify(normalized));
  return normalized;
}

export function saveFavoriteEvents(eventKeys: string[]): string[] {
  const normalized = eventKeys
    .map((value) => String(value || '').trim().toLowerCase())
    .filter((value, idx, array) => /^\d{4}[a-z0-9]+$/.test(value) && array.indexOf(value) === idx);
  window.localStorage.setItem(FAVORITES_KEYS.events, JSON.stringify(normalized));
  return normalized;
}
