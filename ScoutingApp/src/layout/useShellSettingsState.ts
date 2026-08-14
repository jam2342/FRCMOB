import { useEffect, useMemo, useState } from 'react';
import {
  applyBodySettingsClasses,
  emitSettingsUpdated,
  getStoredSettings,
  saveStoredSettings,
  SCOUTING_SETTINGS_UPDATED_EVENT,
  type DensityMode,
  type QuickJumpMode,
  type QuickJumpRegion,
  type ScoutingSettings,
  type ThemeMode,
} from './userSettings';

export function useShellSettingsState() {
  const defaults = useMemo(() => getStoredSettings(), []);
  const [themeMode, setThemeMode] = useState<ThemeMode>(defaults.theme);
  const [densityMode, setDensityMode] = useState<DensityMode>(defaults.density);
  const [jumpMode, setJumpMode] = useState<QuickJumpMode>(defaults.quickJumpMode);
  const [jumpRegion, setJumpRegion] = useState<QuickJumpRegion>(defaults.quickJumpRegion);
  const [tutorialAutoplay, setTutorialAutoplay] = useState<boolean>(defaults.tutorialAutoplay);

  useEffect(() => {
    applyBodySettingsClasses({
      ...getStoredSettings(),
      theme: themeMode,
      density: densityMode,
      quickJumpMode: jumpMode,
      quickJumpRegion: jumpRegion,
    });
  }, [densityMode, jumpMode, jumpRegion, themeMode]);

  useEffect(() => {
    const next = saveStoredSettings({
      theme: themeMode,
      density: densityMode,
      quickJumpMode: jumpMode,
      quickJumpRegion: jumpRegion,
    });
    emitSettingsUpdated(next);
  }, [densityMode, jumpMode, jumpRegion, themeMode]);

  useEffect(() => {
    function onSettingsUpdated(event: Event) {
      const customEvent = event as CustomEvent<ScoutingSettings>;
      const detail = customEvent.detail || getStoredSettings();
      setThemeMode(detail.theme);
      setDensityMode(detail.density);
      setJumpMode(detail.quickJumpMode);
      setJumpRegion(detail.quickJumpRegion);
      setTutorialAutoplay(detail.tutorialAutoplay);
    }

    window.addEventListener(SCOUTING_SETTINGS_UPDATED_EVENT, onSettingsUpdated as EventListener);
    return () => window.removeEventListener(SCOUTING_SETTINGS_UPDATED_EVENT, onSettingsUpdated as EventListener);
  }, []);

  return {
    jumpMode,
    jumpRegion,
    densityMode,
    themeMode,
    tutorialAutoplay,
    setJumpMode,
    setJumpRegion,
    setDensityMode,
    setThemeMode,
    setTutorialAutoplay,
  };
}
