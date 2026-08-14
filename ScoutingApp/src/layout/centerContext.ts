const CENTER_EVENT_KEY_STORAGE = 'scouting_center_event_key';
const CENTER_MATCH_KEY_STORAGE = 'scouting_center_match_key';
const CENTER_TEAM_KEY_STORAGE = 'scouting_center_team_key';
export const CENTER_CONTEXT_UPDATED_EVENT = 'scouting:center-context-updated';

export type CenterContextState = {
  eventKey: string;
  matchKey: string;
  teamKey: string;
  sourcePath: string;
  updatedAt: number;
};

export type CenterContextUpdate = Partial<Pick<CenterContextState, 'eventKey' | 'matchKey' | 'teamKey'>> & {
  sourcePath?: string;
};

function safeGet(key: string): string {
  if (typeof window === 'undefined') return '';
  try {
    return String(window.localStorage.getItem(key) || '').trim();
  } catch {
    return '';
  }
}

function safeSet(key: string, value: string): void {
  if (typeof window === 'undefined') return;
  try {
    if (value) {
      window.localStorage.setItem(key, value);
      return;
    }
    window.localStorage.removeItem(key);
  } catch {
    // Ignore storage write failures.
  }
}

function normalizeCenterEventKey(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase();
}

function normalizeCenterMatchKey(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '');
}

function normalizeCenterTeamKey(value: string | null | undefined): string {
  const raw = String(value || '')
    .trim()
    .toLowerCase()
    .replace(/^team\s+/, '')
    .replace(/^#/, '');
  if (!raw) return '';
  if (/^frc\d+$/.test(raw)) return raw;
  if (/^\d{1,5}$/.test(raw)) return `frc${String(Number(raw))}`;
  return raw;
}

export function readStoredCenterContext(): CenterContextState {
  return {
    eventKey: normalizeCenterEventKey(safeGet(CENTER_EVENT_KEY_STORAGE)),
    matchKey: normalizeCenterMatchKey(safeGet(CENTER_MATCH_KEY_STORAGE)),
    teamKey: normalizeCenterTeamKey(safeGet(CENTER_TEAM_KEY_STORAGE)),
    sourcePath: '',
    updatedAt: 0,
  };
}

export function readCenterContextFromSearch(search: string): CenterContextState {
  const params = new URLSearchParams(search);
  const stored = readStoredCenterContext();
  return {
    eventKey: normalizeCenterEventKey(params.get('event')) || stored.eventKey,
    matchKey: normalizeCenterMatchKey(params.get('match')) || stored.matchKey,
    teamKey: normalizeCenterTeamKey(params.get('team')) || stored.teamKey,
    sourcePath: '',
    updatedAt: 0,
  };
}

export function writeCenterContext(update: CenterContextUpdate): CenterContextState {
  const current = readStoredCenterContext();
  const next: CenterContextState = {
    eventKey:
      update.eventKey !== undefined ? normalizeCenterEventKey(update.eventKey) : current.eventKey,
    matchKey:
      update.matchKey !== undefined ? normalizeCenterMatchKey(update.matchKey) : current.matchKey,
    teamKey:
      update.teamKey !== undefined ? normalizeCenterTeamKey(update.teamKey) : current.teamKey,
    sourcePath: String(update.sourcePath || '').trim(),
    updatedAt: Date.now(),
  };

  safeSet(CENTER_EVENT_KEY_STORAGE, next.eventKey);
  safeSet(CENTER_MATCH_KEY_STORAGE, next.matchKey);
  safeSet(CENTER_TEAM_KEY_STORAGE, next.teamKey);

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent<CenterContextState>(CENTER_CONTEXT_UPDATED_EVENT, { detail: next }));
  }

  return next;
}
