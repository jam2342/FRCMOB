import { useEffect, useMemo, useState } from 'react';
import {
  applyBodySettingsClasses,
  emitSettingsUpdated,
  getStoredSettings,
  saveStoredSettings,
  SCOUTING_SETTINGS_UPDATED_EVENT,
  type DensityMode,
  type QuickJumpRegion,
  type ScoutingSettings,
  type ThemeMode,
} from './userSettings';

export function useShellSettingsState() {
  const defaults = useMemo(() => getStoredSettings(), []);
  const [themeMode, setThemeMode] = useState<ThemeMode>(defaults.theme);
  const [densityMode, setDensityMode] = useState<DensityMode>(defaults.density);
  const [jumpRegion, setJumpRegion] = useState<QuickJumpRegion>(defaults.quickJumpRegion);
  const [tutorialAutoplay, setTutorialAutoplay] = useState<boolean>(defaults.tutorialAutoplay);

  useEffect(() => {
    applyBodySettingsClasses({
      ...getStoredSettings(),
      theme: themeMode,
      density: densityMode,
      quickJumpRegion: jumpRegion,
    });
  }, [densityMode, jumpRegion, themeMode]);

  useEffect(() => {
    const next = saveStoredSettings({
      theme: themeMode,
      density: densityMode,
      quickJumpRegion: jumpRegion,
    });
    emitSettingsUpdated(next);
  }, [densityMode, jumpRegion, themeMode]);

  useEffect(() => {
    function onSettingsUpdated(event: Event) {
      const customEvent = event as CustomEvent<ScoutingSettings>;
      const detail = customEvent.detail || getStoredSettings();
      setThemeMode(detail.theme);
      setDensityMode(detail.density);
      setJumpRegion(detail.quickJumpRegion);
      setTutorialAutoplay(detail.tutorialAutoplay);
    }

    window.addEventListener(SCOUTING_SETTINGS_UPDATED_EVENT, onSettingsUpdated as EventListener);
    return () => window.removeEventListener(SCOUTING_SETTINGS_UPDATED_EVENT, onSettingsUpdated as EventListener);
  }, []);

  return {
    jumpRegion,
    densityMode,
    themeMode,
    tutorialAutoplay,
    setJumpRegion,
    setDensityMode,
    setThemeMode,
    setTutorialAutoplay,
  };
}
