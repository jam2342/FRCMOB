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

// What a stored key must look like to be used: a corrupted or old value ("[]") was
// shown as-is in the match box.
const STORED_EVENT_KEY = /^(19|20)\d{2}[a-z][a-z0-9]{1,15}$/;
const STORED_MATCH_KEY = /^\d{4}[a-z0-9]+_[a-z0-9]+$/;
const STORED_TEAM_KEY = /^frc\d{1,5}[a-z]?$/;
const valid = (value: string, shape: RegExp) => (shape.test(value) ? value : '');

export function readStoredCenterContext(): CenterContextState {
  return {
    eventKey: valid(normalizeCenterEventKey(safeGet(CENTER_EVENT_KEY_STORAGE)), STORED_EVENT_KEY),
    matchKey: valid(normalizeCenterMatchKey(safeGet(CENTER_MATCH_KEY_STORAGE)), STORED_MATCH_KEY),
    teamKey: valid(normalizeCenterTeamKey(safeGet(CENTER_TEAM_KEY_STORAGE)), STORED_TEAM_KEY),
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
