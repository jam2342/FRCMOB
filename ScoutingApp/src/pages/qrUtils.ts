/**
 * QR Code sharing utilities for scouting entries.
 *
 * Encodes a SavedScoutingEntry into a compact format suitable for QR codes,
 * and decodes it back. Uses browser-native CompressionStream (deflate) when
 * available, with a JSON fallback for older browsers.
 *
 * Flow:
 *   Entry → strip large fields → compact JSON → deflate → base64url → QR string
 *   QR string → base64url → inflate → JSON → normalizeEntry
 */

import type { SavedScoutingEntry, ScoutFormState, RpState } from './scoutingPage.types';

/* ------------------------------------------------------------------ */
/*  Compact key mapping – keeps QR payload small                      */
/* ------------------------------------------------------------------ */

/** Compact representation that strips computed / regeneratable fields. */
type CompactEntry = {
  /** version tag */
  v: 1;
  /** saved_at_ms */
  t: number;
  /** scout_profile */
  sp: string;
  /** mode */
  m: 'match' | 'rapid';
  /** event_key */
  ek: string;
  /** match_key */
  mk: string;
  /** match_display */
  md: string;
  /** team_key */
  tk: string;
  /** team_label */
  tl: string;
  /** alliance */
  al: 'red' | 'blue';
  /** station */
  st: string | null;
  /** form — flattened to array of values in canonical order */
  f: (number | boolean | string)[];
  /** rp state — 4 booleans packed as bitmask */
  rp: number;
  /** notes */
  n: string;
};

/* The canonical form field order for array serialization. */
const FORM_KEYS: (keyof ScoutFormState)[] = [
  'auto_mobility',
  'auto_scored',
  'auto_missed',
  'auto_pickups',
  'auto_cycles',
  'auto_path_quality_1_5',
  'teleop_scored',
  'teleop_missed',
  'teleop_under_defense_scored',
  'teleop_under_defense_attempts',
  'teleop_cycles',
  'teleop_drops',
  'intake_failures',
  'foul_count',
  'offense_level_1_5',
  'defense_level_1_5',
  'field_awareness_1_5',
  'decision_quality_1_5',
  'communication_1_5',
  'anti_defense_level_1_5',
  'escape_level_1_5',
  'reroute_level_1_5',
  'hit_recovery_level_1_5',
  'bump_crosses',
  'trench_crosses',
  'climbed_under_pressure',
  'protected_zone_risk',
  'endgame_mode',
];

const RP_KEYS: (keyof RpState)[] = ['energized', 'supercharged', 'traversal', 'coop'];

/* ------------------------------------------------------------------ */
/*  Encode / decode                                                    */
/* ------------------------------------------------------------------ */

function packForm(form: ScoutFormState): (number | boolean | string)[] {
  return FORM_KEYS.map((key) => form[key] as number | boolean | string);
}

function unpackForm(values: (number | boolean | string)[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (let i = 0; i < FORM_KEYS.length && i < values.length; i++) {
    out[FORM_KEYS[i]] = values[i];
  }
  return out;
}

function packRp(rp: RpState): number {
  let bits = 0;
  for (let i = 0; i < RP_KEYS.length; i++) {
    if (rp[RP_KEYS[i]]) bits |= 1 << i;
  }
  return bits;
}

function unpackRp(bits: number): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (let i = 0; i < RP_KEYS.length; i++) {
    out[RP_KEYS[i]] = Boolean(bits & (1 << i));
  }
  return out;
}

function entryToCompact(entry: SavedScoutingEntry): CompactEntry {
  return {
    v: 1,
    t: entry.saved_at_ms,
    sp: entry.scout_profile,
    m: entry.mode,
    ek: entry.event_key,
    mk: entry.match_key,
    md: entry.match_display,
    tk: entry.team_key,
    tl: entry.team_label,
    al: entry.alliance,
    st: entry.station,
    f: packForm(entry.form),
    rp: packRp(entry.rp),
    n: entry.notes,
  };
}

/**
 * Reconstruct a raw entry object from a compact payload.
 * The caller should still run `normalizeEntry()` to ensure all
 * computed fields (points, ratings, etc.) are regenerated.
 */
function compactToRaw(compact: CompactEntry): Record<string, unknown> {
  return {
    saved_at_ms: compact.t,
    scout_profile: compact.sp,
    mode: compact.m,
    event_key: compact.ek,
    match_key: compact.mk,
    match_display: compact.md,
    team_key: compact.tk,
    team_label: compact.tl,
    alliance: compact.al,
    station: compact.st,
    form: unpackForm(compact.f),
    rp: unpackRp(compact.rp),
    notes: compact.n,
    // id and ratings are intentionally omitted —
    // normalizeEntry() will assign new id and recompute ratings
  };
}

/* ------------------------------------------------------------------ */
/*  Compression (browser CompressionStream / DecompressionStream)      */
/* ------------------------------------------------------------------ */

const QR_PREFIX = 'FRCMOB1:'; // version-tagged prefix for entry detection
const ROOM_QR_PREFIX = 'FRCROOM1:'; // version-tagged prefix for room detection

async function compressBytes(data: Uint8Array): Promise<Uint8Array> {
  if (typeof CompressionStream === 'function') {
    const cs = new CompressionStream('deflate');
    const writer = cs.writable.getWriter();
    writer.write(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer);
    writer.close();
    const reader = cs.readable.getReader();
    const chunks: Uint8Array[] = [];
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
    }
    const total = chunks.reduce((a, c) => a + c.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      out.set(chunk, offset);
      offset += chunk.length;
    }
    return out;
  }
  // Fallback: no compression
  return data;
}

async function decompressBytes(data: Uint8Array): Promise<Uint8Array> {
  if (typeof DecompressionStream === 'function') {
    const ds = new DecompressionStream('deflate');
    const writer = ds.writable.getWriter();
    writer.write(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer);
    writer.close();
    const reader = ds.readable.getReader();
    const chunks: Uint8Array[] = [];
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
    }
    const total = chunks.reduce((a, c) => a + c.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      out.set(chunk, offset);
      offset += chunk.length;
    }
    return out;
  }
  // Fallback: data was not compressed
  return data;
}

/* ------------------------------------------------------------------ */
/*  Base64url                                                          */
/* ------------------------------------------------------------------ */

function uint8ToBase64url(bytes: Uint8Array): string {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function base64urlToUint8(str: string): Uint8Array {
  let b64 = str.replace(/-/g, '+').replace(/_/g, '/');
  // Restore padding
  while (b64.length % 4 !== 0) b64 += '=';
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

/* ------------------------------------------------------------------ */
/*  Public API                                                         */
/* ------------------------------------------------------------------ */

/**
 * Encode a scouting entry into a compact string suitable for QR code display.
 * Strips computed fields, compresses, and base64url encodes.
 * Returns a string like "FRCMOB1:eJy0k…".
 */
export async function encodeEntryForQr(entry: SavedScoutingEntry): Promise<string> {
  const compact = entryToCompact(entry);
  const jsonStr = JSON.stringify(compact);
  const raw = new TextEncoder().encode(jsonStr);
  const compressed = await compressBytes(raw);
  return QR_PREFIX + uint8ToBase64url(compressed);
}

/**
 * Decode a QR code string back into a raw entry object.
 * The caller should run normalizeEntry() on the result to
 * regenerate all computed fields.
 * Returns null if the string is not a valid encoded entry.
 */
export async function decodeEntryFromQr(qrString: string): Promise<Record<string, unknown> | null> {
  const str = qrString.trim();
  if (!str.startsWith(QR_PREFIX)) return null;
  try {
    const b64 = str.slice(QR_PREFIX.length);
    const compressed = base64urlToUint8(b64);
    const raw = await decompressBytes(compressed);
    const jsonStr = new TextDecoder().decode(raw);
    const compact = JSON.parse(jsonStr) as CompactEntry;
    if (compact.v !== 1) return null;
    return compactToRaw(compact);
  } catch {
    return null;
  }
}

/** Check if a string is a valid QR-encoded scouting entry. */
export function isQrEntryString(str: string): boolean {
  return str.trim().startsWith(QR_PREFIX);
}

function normalizeRoomKeyToken(raw: string): string {
  return raw.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '').slice(0, 48);
}

function roomKeyFromCandidate(value: unknown): string | null {
  const raw = String(value ?? '').trim();
  if (!/^[a-z0-9_-]{1,48}$/i.test(raw)) return null;
  const normalized = normalizeRoomKeyToken(raw);
  return normalized || null;
}

/** Build a room QR payload string for sharing/join flow. */
export function encodeRoomKeyForQr(roomKey: string): string {
  const normalized = roomKeyFromCandidate(roomKey);
  if (!normalized) return '';
  return `${ROOM_QR_PREFIX}${normalized}`;
}

/**
 * Parse a scouting room key from QR text.
 * Supports:
 * - plain room key (e.g. "room-abc123")
 * - prefixed text (e.g. "FRCROOM1:room-abc123")
 * - JSON objects with room_key/roomKey/room fields
 * - URLs with room_key/room/rk query params
 * - URLs containing /scouting/rooms/{room_key}
 */
export function parseRoomKeyFromQr(text: string): string | null {
  const raw = String(text || '').trim();
  if (!raw || isQrEntryString(raw)) return null;

  if (raw.toUpperCase().startsWith(ROOM_QR_PREFIX)) {
    return roomKeyFromCandidate(raw.slice(ROOM_QR_PREFIX.length));
  }

  const direct = roomKeyFromCandidate(raw);
  if (direct) return direct;

  if (raw.startsWith('{') && raw.endsWith('}')) {
    try {
      const row = JSON.parse(raw) as Record<string, unknown>;
      const fromJson = roomKeyFromCandidate(
        row.room_key ?? row.roomKey ?? row.scouting_room_key ?? row.scoutingRoomKey ?? row.room,
      );
      if (fromJson) return fromJson;
    } catch {
      // no-op
    }
  }

  try {
    const parsed = new URL(raw);
    const fromQuery = roomKeyFromCandidate(
      parsed.searchParams.get('room_key')
      || parsed.searchParams.get('room')
      || parsed.searchParams.get('rk'),
    );
    if (fromQuery) return fromQuery;
    const match = parsed.pathname.match(/\/scouting\/rooms\/([a-z0-9_-]{1,48})(?:\/|$)/i);
    if (match?.[1]) return roomKeyFromCandidate(match[1]);
  } catch {
    // no-op
  }

  return null;
}
