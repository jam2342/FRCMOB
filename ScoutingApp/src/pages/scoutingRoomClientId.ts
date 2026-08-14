const ROOM_CLIENT_ID_STORAGE = 'scouting_room_client_id_v1';

function normalizeClientId(raw: string): string {
  return String(raw || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '')
    .slice(0, 80);
}

export function getOrCreateScoutingRoomClientId(): string {
  const fallback = `client-${Math.random().toString(36).slice(2, 10)}`;
  if (typeof window === 'undefined') return fallback;
  try {
    const stored = normalizeClientId(window.sessionStorage.getItem(ROOM_CLIENT_ID_STORAGE) || '');
    if (stored) return stored;
    const next = normalizeClientId(fallback) || fallback;
    window.sessionStorage.setItem(ROOM_CLIENT_ID_STORAGE, next);
    return next;
  } catch {
    return fallback;
  }
}

