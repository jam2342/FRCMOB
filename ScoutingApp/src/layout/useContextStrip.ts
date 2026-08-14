import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CENTER_CONTEXT_UPDATED_EVENT,
  readCenterContextFromSearch,
  type CenterContextState,
} from './centerContext';

const CONTEXT_PINS_STORAGE = 'scouting_context_pins_v1';
const CONTEXT_RECENTS_STORAGE = 'scouting_context_recents_v1';
const CONTEXT_STRIP_COLLAPSED_STORAGE = 'scouting_context_strip_collapsed_v1';
const CONTEXT_RECENTS_LIMIT = 8;
const CONTEXT_PIN_LIMIT = 10;
const DESKTOP_SIDEBAR_BREAKPOINT_PX = 1120;

type ContextQuickPickKind = 'event' | 'team';

type ContextSnapshot = {
  eventKey: string;
  matchKey: string;
  teamKey: string;
};

type ContextQuickPickState = {
  event: string[];
  team: string[];
};

function normalizedEventKey(raw: string | null | undefined): string {
  return String(raw || '').trim().toLowerCase();
}

function normalizedMatchKey(raw: string | null | undefined): string {
  return String(raw || '').trim().toLowerCase().replace(/\s+/g, '');
}

function normalizedTeamKey(raw: string): string | null {
  const value = raw
    .trim()
    .toLowerCase()
    .replace(/^team\s+/, '')
    .replace(/^#/, '');
  if (!value) return null;
  if (/^frc\d+$/.test(value)) return value;
  if (/^\d{1,5}$/.test(value)) return `frc${String(Number(value))}`;
  return null;
}

function normalizeQuickPick(kind: ContextQuickPickKind, raw: string | null | undefined): string {
  if (kind === 'event') return normalizedEventKey(raw);
  return normalizedTeamKey(String(raw || '')) || '';
}

function normalizeQuickPickList(
  kind: ContextQuickPickKind,
  values: unknown,
  limit: number,
): string[] {
  const rows = Array.isArray(values) ? values : [];
  const seen = new Set<string>();
  const next: string[] = [];
  for (const value of rows) {
    const normalized = normalizeQuickPick(kind, typeof value === 'string' ? value : String(value || ''));
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    next.push(normalized);
    if (next.length >= limit) break;
  }
  return next;
}

function readContextQuickPickState(storageKey: string, limit: number): ContextQuickPickState {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return { event: [], team: [] };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      event: normalizeQuickPickList('event', parsed?.event, limit),
      team: normalizeQuickPickList('team', parsed?.team, limit),
    };
  } catch {
    return { event: [], team: [] };
  }
}

function writeContextQuickPickState(storageKey: string, value: ContextQuickPickState): void {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(value));
  } catch {
    // ignore localStorage failures
  }
}

function buildContextSnapshot(search: string): ContextSnapshot {
  const context = readCenterContextFromSearch(search);
  return {
    eventKey: context.eventKey,
    matchKey: context.matchKey,
    teamKey: normalizeQuickPick('team', context.teamKey),
  };
}

function formatContextMatch(matchKey: string, eventKey: string): string {
  if (!matchKey) return 'No match';
  if (eventKey && matchKey.startsWith(`${eventKey}_`)) {
    return matchKey.slice(eventKey.length + 1).toUpperCase();
  }
  return matchKey.toUpperCase();
}

function pushRecent(
  current: ContextQuickPickState,
  kind: ContextQuickPickKind,
  rawKey: string,
): ContextQuickPickState {
  const key = normalizeQuickPick(kind, rawKey);
  if (!key) return current;
  const existing = current[kind];
  if (existing[0] === key) return current;
  const nextKind = [key, ...existing.filter((v) => v !== key)].slice(0, CONTEXT_RECENTS_LIMIT);
  return { ...current, [kind]: nextKind };
}

export function useContextStrip(locationSearch: string) {
  const navigate = useNavigate();

  const [contextSnapshot, setContextSnapshot] = useState<ContextSnapshot>(
    () => buildContextSnapshot(locationSearch),
  );
  const [contextPins, setContextPins] = useState<ContextQuickPickState>(
    () => readContextQuickPickState(CONTEXT_PINS_STORAGE, CONTEXT_PIN_LIMIT),
  );
  const [contextRecents, setContextRecents] = useState<ContextQuickPickState>(
    () => readContextQuickPickState(CONTEXT_RECENTS_STORAGE, CONTEXT_RECENTS_LIMIT),
  );
  const [contextStripCollapsed, setContextStripCollapsed] = useState<boolean>(() => {
    const stored = window.localStorage.getItem(CONTEXT_STRIP_COLLAPSED_STORAGE);
    if (stored === '1') return true;
    if (stored === '0') return false;
    return window.innerWidth <= DESKTOP_SIDEBAR_BREAKPOINT_PX;
  });

  useEffect(() => {
    window.localStorage.setItem(CONTEXT_STRIP_COLLAPSED_STORAGE, contextStripCollapsed ? '1' : '0');
  }, [contextStripCollapsed]);

  useEffect(() => {
    setContextSnapshot(buildContextSnapshot(locationSearch));
  }, [locationSearch]);

  useEffect(() => {
    function syncContextSnapshot(event?: Event) {
      const detail = (event as CustomEvent<CenterContextState> | undefined)?.detail;
      const nextFromSearch = buildContextSnapshot(locationSearch);
      const next = detail
        ? {
            eventKey: normalizedEventKey(detail.eventKey) || nextFromSearch.eventKey,
            matchKey: normalizedMatchKey(detail.matchKey) || nextFromSearch.matchKey,
            teamKey: normalizeQuickPick('team', detail.teamKey) || nextFromSearch.teamKey,
          }
        : nextFromSearch;
      setContextSnapshot((current) =>
        current.eventKey === next.eventKey &&
        current.matchKey === next.matchKey &&
        current.teamKey === next.teamKey
          ? current
          : next,
      );
    }
    window.addEventListener(CENTER_CONTEXT_UPDATED_EVENT, syncContextSnapshot as EventListener);
    return () => window.removeEventListener(CENTER_CONTEXT_UPDATED_EVENT, syncContextSnapshot as EventListener);
  }, [locationSearch]);

  useEffect(() => {
    writeContextQuickPickState(CONTEXT_PINS_STORAGE, contextPins);
  }, [contextPins]);

  useEffect(() => {
    writeContextQuickPickState(CONTEXT_RECENTS_STORAGE, contextRecents);
  }, [contextRecents]);

  useEffect(() => {
    setContextRecents((current) => {
      let next = current;
      if (contextSnapshot.eventKey) next = pushRecent(next, 'event', contextSnapshot.eventKey);
      if (contextSnapshot.teamKey) next = pushRecent(next, 'team', contextSnapshot.teamKey);
      return next;
    });
  }, [contextSnapshot.eventKey, contextSnapshot.teamKey]);

  const pinnedEventSet = useMemo(() => new Set(contextPins.event), [contextPins.event]);
  const pinnedTeamSet = useMemo(() => new Set(contextPins.team), [contextPins.team]);

  const recentEventQuickPicks = useMemo(
    () => contextRecents.event.filter((k) => !pinnedEventSet.has(k)).slice(0, CONTEXT_RECENTS_LIMIT),
    [contextRecents.event, pinnedEventSet],
  );
  const recentTeamQuickPicks = useMemo(
    () => contextRecents.team.filter((k) => !pinnedTeamSet.has(k)).slice(0, CONTEXT_RECENTS_LIMIT),
    [contextRecents.team, pinnedTeamSet],
  );

  const contextStripSummary = useMemo(() => {
    const eventLabel = contextSnapshot.eventKey || 'none';
    const matchLabel = formatContextMatch(contextSnapshot.matchKey, contextSnapshot.eventKey);
    const teamLabel = contextSnapshot.teamKey ? contextSnapshot.teamKey.toUpperCase() : 'none';
    return `Event ${eventLabel} · Match ${matchLabel} · Team ${teamLabel}`;
  }, [contextSnapshot.eventKey, contextSnapshot.matchKey, contextSnapshot.teamKey]);

  const hasContextStripContent = Boolean(
    contextSnapshot.eventKey ||
    contextSnapshot.matchKey ||
    contextSnapshot.teamKey ||
    contextPins.event.length > 0 ||
    contextPins.team.length > 0 ||
    recentEventQuickPicks.length > 0 ||
    recentTeamQuickPicks.length > 0,
  );

  const contextMatchLabel = useMemo(
    () => formatContextMatch(contextSnapshot.matchKey, contextSnapshot.eventKey),
    [contextSnapshot.eventKey, contextSnapshot.matchKey],
  );

  const eventPinned = contextSnapshot.eventKey ? pinnedEventSet.has(contextSnapshot.eventKey) : false;
  const teamPinned = contextSnapshot.teamKey ? pinnedTeamSet.has(contextSnapshot.teamKey) : false;

  const toggleContextPin = useCallback((kind: ContextQuickPickKind, rawKey: string) => {
    const key = normalizeQuickPick(kind, rawKey);
    if (!key) return;
    setContextPins((current) => {
      const existing = current[kind];
      if (existing.includes(key)) {
        return { ...current, [kind]: existing.filter((v) => v !== key) };
      }
      return { ...current, [kind]: [key, ...existing].slice(0, CONTEXT_PIN_LIMIT) };
    });
  }, []);

  const openEventQuickPick = useCallback((eventKey: string) => {
    const key = normalizedEventKey(eventKey);
    if (!key) return;
    navigate(`/events?event=${key}`);
  }, [navigate]);

  const openTeamQuickPick = useCallback((teamKey: string) => {
    const key = normalizeQuickPick('team', teamKey);
    if (!key) return;
    const params = new URLSearchParams();
    params.set('team', key);
    navigate(`/team-center?${params.toString()}`);
  }, [navigate]);

  return {
    contextSnapshot,
    contextStripCollapsed,
    setContextStripCollapsed,
    hasContextStripContent,
    contextStripSummary,
    contextMatchLabel,
    contextPins,
    contextRecents,
    pinnedEventSet,
    pinnedTeamSet,
    recentEventQuickPicks,
    recentTeamQuickPicks,
    eventPinned,
    teamPinned,
    toggleContextPin,
    openEventQuickPick,
    openTeamQuickPick,
  };
}
