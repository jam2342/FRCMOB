import { useEffect, useState } from 'react';
import {
  getStoredSettings,
  SCOUTING_SETTINGS_UPDATED_EVENT,
  type ScoutingSettings,
} from '../layout/userSettings';

export function useLiveRefreshSetting(): number {
  const [liveRefreshSec, setLiveRefreshSec] = useState(() => getStoredSettings().liveRefreshSec);

  useEffect(() => {
    function onSettingsUpdated(event: Event) {
      const customEvent = event as CustomEvent<ScoutingSettings>;
      const next = customEvent.detail || getStoredSettings();
      setLiveRefreshSec(next.liveRefreshSec);
    }

    window.addEventListener(SCOUTING_SETTINGS_UPDATED_EVENT, onSettingsUpdated as EventListener);
    return () => window.removeEventListener(SCOUTING_SETTINGS_UPDATED_EVENT, onSettingsUpdated as EventListener);
  }, []);

  return liveRefreshSec;
}
