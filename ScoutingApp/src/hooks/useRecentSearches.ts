import { useCallback, useState } from 'react';

const RECENT_SEARCHES_KEY = 'scouting_recent_searches';

function normalizeRecentSearch(raw: unknown): string {
  return String(raw || '').trim();
}

function readRecentSearches(limit: number): string[] {
  try {
    const raw = localStorage.getItem(RECENT_SEARCHES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(normalizeRecentSearch)
      .filter((value, index, values) => Boolean(value) && values.indexOf(value) === index)
      .slice(0, limit);
  } catch {
    return [];
  }
}

function writeRecentSearch(query: string, limit: number): string[] {
  const trimmed = normalizeRecentSearch(query);
  if (!trimmed) return readRecentSearches(limit);
  const recent = readRecentSearches(limit).filter((value) => value !== trimmed);
  recent.unshift(trimmed);
  const next = recent.slice(0, limit);
  localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(next));
  return next;
}

export function useRecentSearches(limit: number) {
  const [recentSearches, setRecentSearches] = useState(() => readRecentSearches(limit));

  const refreshRecentSearches = useCallback(() => {
    setRecentSearches(readRecentSearches(limit));
  }, [limit]);

  const addRecentSearch = useCallback((query: string) => {
    const next = writeRecentSearch(query, limit);
    setRecentSearches(next);
    return next;
  }, [limit]);

  const clearRecentSearches = useCallback(() => {
    try {
      localStorage.removeItem(RECENT_SEARCHES_KEY);
    } catch {
      // ignore
    }
    setRecentSearches([]);
  }, []);

  return {
    recentSearches,
    refreshRecentSearches,
    addRecentSearch,
    clearRecentSearches,
  };
}
