export const CURRENT_SEASON_YEAR = 2026;
export const FALLBACK_SEASON_YEAR = 2025;
const MATCH_DURATION_SEC = 150;

export type FreshnessSummary = {
  state: 'fresh' | 'stale' | 'unknown';
  label: string;
  detail: string | null;
};

export function metric(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'N/A';
  return value.toFixed(digits);
}

/** Like metric(), but appends a unit only when there is a value ("12.5s", never "N/As"). */
export function metricUnit(value: number | null | undefined, digits: number, unit: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'N/A';
  return `${value.toFixed(digits)}${unit}`;
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'N/A';
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtUnix(seconds: number | null): string {
  if (!seconds) return 'Unknown';
  return new Date(seconds * 1000).toLocaleString();
}

export function fmtDateShort(seconds: number | null): string {
  if (!seconds) return 'TBD';
  return new Date(seconds * 1000).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function fmtTimer(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safeSeconds / 60);
  const seconds = safeSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function liveTimerLabel(
  scheduledUnixSec: number | null,
  nowMs: number,
): {
  state: 'unknown' | 'upcoming' | 'live' | 'ended';
  label: string;
  value: string;
} {
  if (!scheduledUnixSec) {
    return {
      state: 'unknown',
      label: 'Timer',
      value: 'Pending publish',
    };
  }
  const nowSec = Math.floor(nowMs / 1000);
  const startSec = Math.floor(scheduledUnixSec);
  const endSec = startSec + MATCH_DURATION_SEC;
  if (nowSec < startSec) {
    return {
      state: 'upcoming',
      label: 'Starts In',
      value: fmtTimer(startSec - nowSec),
    };
  }
  if (nowSec < endSec) {
    return {
      state: 'live',
      label: 'Time Left',
      value: fmtTimer(endSec - nowSec),
    };
  }
  return {
    state: 'ended',
    label: 'Status',
    value: 'Likely complete',
  };
}

export function titleizeKey(raw: string): string {
  return raw
    .replace(/[_-]+/g, ' ')
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function teamNumberFromTeamKey(teamKey: string): number | null {
  const match = /^frc(\d+)$/i.exec(teamKey.trim());
  if (!match) return null;
  return Number(match[1]);
}

export function normalizeTeamKeyInput(raw: string): string | null {
  const value = raw.trim().toLowerCase();
  if (!value) return null;
  if (/^frc\d+$/.test(value)) return value;
  if (/^\d+$/.test(value)) return `frc${String(Number(value))}`;
  return null;
}

export function normalizeEventKey(raw: string | null | undefined): string {
  return String(raw || '').trim().toLowerCase();
}

export function normalizeMatchKey(
  rawMatchKey: string | null | undefined,
  rawEventKey?: string | null,
): string {
  const matchKey = String(rawMatchKey || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '');
  if (!matchKey) return '';
  if (matchKey.includes('_')) return matchKey;
  const eventKey = normalizeEventKey(rawEventKey);
  if (!eventKey) return matchKey;
  return `${eventKey}_${matchKey}`;
}

export function eventKeyFromMatchKey(matchKey: string | null | undefined): string | null {
  const normalized = normalizeMatchKey(matchKey);
  if (!normalized.includes('_')) return null;
  const [eventKey] = normalized.split('_');
  if (!/^\d{4}[a-z0-9]+$/.test(eventKey)) return null;
  return eventKey;
}

export function buildMatchCenterPath(
  rawEventKey: string | null | undefined,
  rawMatchKey: string | null | undefined,
): string {
  const providedEventKey = normalizeEventKey(rawEventKey);
  const normalizedRawMatchKey = normalizeMatchKey(rawMatchKey, providedEventKey);
  const inferredEventKey = eventKeyFromMatchKey(normalizedRawMatchKey);
  // If the match key already encodes an event, trust it to avoid
  // event/match mismatches that can reset selection to the first row.
  const eventKey = inferredEventKey || providedEventKey;
  const matchKey = normalizeMatchKey(rawMatchKey, eventKey);
  const params = new URLSearchParams();
  if (eventKey) params.set('event', eventKey);
  if (matchKey) params.set('match', matchKey);
  const query = params.toString();
  return query ? `/match-center?${query}` : '/match-center';
}

export async function copyTextToClipboard(value: string): Promise<boolean> {
  const text = String(value || '').trim();
  if (!text) return false;

  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fallback below
  }

  if (typeof document === 'undefined') return false;
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const copied = document.execCommand('copy');
  document.body.removeChild(textarea);
  return copied;
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function parseNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

export function clampNumber(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function meanNumber(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function relativeFromTimestamp(timestampMs: number | null): string {
  if (!timestampMs) return 'never';
  const deltaSec = Math.max(0, Math.floor((Date.now() - timestampMs) / 1000));
  if (deltaSec < 60) return `${deltaSec}s ago`;
  const deltaMin = Math.floor(deltaSec / 60);
  if (deltaMin < 60) return `${deltaMin}m ago`;
  const deltaHr = Math.floor(deltaMin / 60);
  if (deltaHr < 24) return `${deltaHr}h ago`;
  const deltaDay = Math.floor(deltaHr / 24);
  return `${deltaDay}d ago`;
}

export function isTransientAbortLikeError(error: unknown): boolean {
  const message =
    typeof error === 'string'
      ? error
      : error instanceof Error
        ? error.message
        : '';
  const normalized = String(message || '').trim().toLowerCase();
  if (!normalized) return false;
  return (
    normalized.includes('aborted') ||
    normalized.includes('aborterror') ||
    normalized.includes('the user aborted a request') ||
    normalized.includes('request was aborted') ||
    normalized.includes('timed out') ||
    normalized.includes('timeout')
  );
}

export function summarizeFreshness(payload: unknown): FreshnessSummary {
  const row = asRecord(payload);
  if (!row) return { state: 'unknown', label: 'Freshness: N/A', detail: null };

  const isOutdated = Boolean(row.is_outdated);
  const ageDays = parseNumber(row.latest_match_age_days);
  const warnings = Array.isArray(row.warnings)
    ? row.warnings.map((value) => String(value || '').trim()).filter(Boolean)
    : [];

  if (isOutdated) {
    const detail = warnings[0] || (ageDays !== null ? `Latest match is ${ageDays.toFixed(1)} day(s) old.` : null);
    return {
      state: 'stale',
      label: ageDays !== null ? `Stale (${ageDays.toFixed(1)}d old)` : 'Stale',
      detail,
    };
  }

  if (ageDays !== null) {
    return {
      state: 'fresh',
      label: `Fresh (${ageDays.toFixed(1)}d old)`,
      detail: warnings[0] || null,
    };
  }

  return {
    state: warnings.length > 0 ? 'stale' : 'unknown',
    label: warnings.length > 0 ? 'Potentially stale' : 'Freshness: N/A',
    detail: warnings[0] || null,
  };
}
