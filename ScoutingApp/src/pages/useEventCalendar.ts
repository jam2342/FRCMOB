import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import type { EventSearchItem } from '../api';
import { resolveEventDateRange, type EventDateRange } from './eventCalendar';

const EMPTY_RANGES: Record<string, EventDateRange> = {};

function monthTokensForRange(startMs: number | null, endMs: number | null): string[] {
  const firstMs = startMs ?? endMs;
  const lastMs = endMs ?? startMs;
  if (!firstMs || !lastMs) return [];
  const tokens: string[] = [];
  const current = new Date(Date.UTC(new Date(firstMs).getUTCFullYear(), new Date(firstMs).getUTCMonth(), 1));
  const terminal = new Date(Date.UTC(new Date(lastMs).getUTCFullYear(), new Date(lastMs).getUTCMonth(), 1));
  while (current <= terminal) {
    tokens.push(`${current.getUTCFullYear()}-${String(current.getUTCMonth() + 1).padStart(2, '0')}`);
    current.setUTCMonth(current.getUTCMonth() + 1);
  }
  return tokens;
}

function formatMonthLabel(token: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(token);
  if (!match) return token;
  const d = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' });
}

export function useEventCalendar(
  calendarSourceEvents: EventSearchItem[],
  calendarMonth: string,
  scheduleDateRangeByEvent: Record<string, EventDateRange> = EMPTY_RANGES,
) {
  const calendarEventRows = useMemo(() => {
    return calendarSourceEvents
      .map((event) => {
        const key = String(event.event_key || '')
          .trim()
          .toLowerCase();
        const resolved = resolveEventDateRange(event, scheduleDateRangeByEvent[key]);
        if (!resolved.startMs && !resolved.endMs) return null;
        return {
          event,
          startMs: resolved.startMs,
          endMs: resolved.endMs,
        };
      })
      .filter((row): row is { event: EventSearchItem; startMs: number | null; endMs: number | null } => Boolean(row))
      .sort((a, b) => {
        const aMs = a.startMs ?? a.endMs ?? Number.MAX_SAFE_INTEGER;
        const bMs = b.startMs ?? b.endMs ?? Number.MAX_SAFE_INTEGER;
        if (aMs !== bMs) return aMs - bMs;
        return a.event.event_key.localeCompare(b.event.event_key);
      });
  }, [calendarSourceEvents, scheduleDateRangeByEvent]);

  const modalDayEvents = useMemo(() => {
    const map = new Map<string, EventSearchItem[]>();
    for (const row of calendarEventRows) {
      if (!row.startMs) continue;
      const start = new Date(row.startMs);
      const end = row.endMs ? new Date(row.endMs) : start;
      const cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
      const endUtc = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate()));
      while (cursor.getTime() <= endUtc.getTime()) {
        const token = `${cursor.getUTCFullYear()}-${String(cursor.getUTCMonth() + 1).padStart(2, '0')}-${String(cursor.getUTCDate()).padStart(2, '0')}`;
        const existing = map.get(token);
        if (existing) {
          existing.push(row.event);
        } else {
          map.set(token, [row.event]);
        }
        cursor.setUTCDate(cursor.getUTCDate() + 1);
      }
    }
    return map;
  }, [calendarEventRows]);

  const modalGridDays = useMemo(() => {
    const [yearStr, monthStr] = calendarMonth.split('-');
    const year = parseInt(yearStr, 10);
    const month = parseInt(monthStr, 10) - 1;
    if (!Number.isFinite(year) || !Number.isFinite(month)) return [];
    const firstOfMonth = new Date(Date.UTC(year, month, 1));
    const firstDay = firstOfMonth.getUTCDay();
    const gridStart = new Date(firstOfMonth);
    gridStart.setUTCDate(1 - firstDay);
    const days: { date: Date; token: string; inMonth: boolean; dayNum: number }[] = [];
    for (let i = 0; i < 42; i++) {
      const d = new Date(gridStart);
      d.setUTCDate(gridStart.getUTCDate() + i);
      const token = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
      days.push({ date: d, token, inMonth: d.getUTCMonth() === month, dayNum: d.getUTCDate() });
    }
    return days;
  }, [calendarMonth]);

  const calendarAvailableMonths = useMemo(() => {
    const tokens = new Set<string>();
    for (const row of calendarEventRows) {
      for (const token of monthTokensForRange(row.startMs, row.endMs)) {
        tokens.add(token);
      }
    }
    return Array.from(tokens).sort();
  }, [calendarEventRows]);

  const visibleCalendarEvents = useMemo(
    () =>
      calendarEventRows
        .filter((row) => monthTokensForRange(row.startMs, row.endMs).includes(calendarMonth))
        .map((row) => row.event),
    [calendarEventRows, calendarMonth],
  );

  const dateTbaCalendarEvents = useMemo(
    () =>
      calendarSourceEvents
        .filter((event) => {
          const key = String(event.event_key || '')
            .trim()
            .toLowerCase();
          const resolved = resolveEventDateRange(event, scheduleDateRangeByEvent[key]);
          return !resolved.startMs && !resolved.endMs;
        })
        .sort((a, b) => a.event_key.localeCompare(b.event_key)),
    [calendarSourceEvents, scheduleDateRangeByEvent],
  );

  return {
    calendarEventRows,
    modalDayEvents,
    modalGridDays,
    calendarAvailableMonths,
    visibleCalendarEvents,
    dateTbaCalendarEvents,
    calendarMonthLabel: formatMonthLabel(calendarMonth),
  };
}

export function useCalendarExpansion(
  calendarModalOpen: boolean,
  calendarMonth: string,
  setCalendarModalOpen: Dispatch<SetStateAction<boolean>>,
) {
  const [expandedCalendarDays, setExpandedCalendarDays] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!calendarModalOpen) {
      setExpandedCalendarDays(new Set());
      return;
    }
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCalendarModalOpen(false);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [calendarModalOpen, setCalendarModalOpen]);

  useEffect(() => {
    setExpandedCalendarDays(new Set());
  }, [calendarMonth]);

  const toggleCalendarDayExpanded = useCallback((token: string) => {
    setExpandedCalendarDays((prev) => {
      const next = new Set(prev);
      if (next.has(token)) {
        next.delete(token);
      } else {
        next.add(token);
      }
      return next;
    });
  }, []);
  return { expandedCalendarDays, toggleCalendarDayExpanded };
}
