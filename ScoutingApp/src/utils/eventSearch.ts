import { getSuggestedEvents, searchEvents, type EventSearchItem } from '../api';
import { getEventSearchIndexSnapshot, mergeEventsIntoSearchIndex } from './eventSearchIndex';

const TOKEN_ALIASES: Record<string, string> = {
  house: 'houston',
  houson: 'houston',
  houstn: 'houston',
  hston: 'houston',
  toronot: 'toronto',
  tornto: 'toronto',
  toronro: 'toronto',
  austrilian: 'australian',
  austrailian: 'australian',
  australiane: 'australian',
  austrlia: 'australia',
  aus: 'australia',
  champs: 'championship',
  cmp: 'championship',
};

const STOP_TOKENS = new Set([
  'the',
  'a',
  'an',
  'for',
  'of',
  'to',
  'please',
  'open',
  'show',
  'find',
  'search',
  'competition',
  'tournament',
]);

const BOOST_TOKENS = new Set(['district', 'regional', 'league', 'championship', 'week', 'event']);

function normalizeText(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/[^\w\s-]+/g, ' ')
    .replace(/[_-]+/g, ' ')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, ' ');
}

function tokenize(value: string): string[] {
  const normalized = normalizeText(value);
  if (!normalized) return [];
  return normalized.split(' ').filter(Boolean);
}

function applyTokenAliases(tokens: string[]): string[] {
  return tokens.map((token) => TOKEN_ALIASES[token] || token);
}

function extractYear(tokens: string[]): number | null {
  for (const token of tokens) {
    if (!/^\d{4}$/.test(token)) continue;
    const year = Number(token);
    if (Number.isFinite(year) && year >= 1992 && year <= 2100) return year;
  }
  return null;
}

function buildQueryVariants(rawQuery: string): {
  correctedQuery: string;
  year: number | null;
  semanticTokens: string[];
  variants: string[];
} {
  const rawTokens = tokenize(rawQuery);
  const correctedTokens = applyTokenAliases(rawTokens);
  const correctedQuery = correctedTokens.join(' ').trim();
  const year = extractYear(correctedTokens);
  const semanticTokens = correctedTokens.filter((token) => !STOP_TOKENS.has(token));
  const nonYearTokens = semanticTokens.filter((token) => !/^\d{4}$/.test(token));

  const variants = new Set<string>();
  const add = (value: string) => {
    const cleaned = normalizeText(value);
    if (!cleaned) return;
    variants.add(cleaned);
  };

  add(rawQuery);
  add(correctedQuery);
  add(semanticTokens.join(' '));
  add(nonYearTokens.join(' '));
  if (year) add(String(year));
  if (nonYearTokens.length > 0) add(nonYearTokens.slice(0, 3).join(' '));
  if (year && nonYearTokens.length > 0) add(`${year} ${nonYearTokens.join(' ')}`);
  if (year && nonYearTokens.length > 1) add(`${nonYearTokens.join(' ')} ${year}`);
  if (nonYearTokens.length > 0) {
    const longestToken = [...nonYearTokens].sort((a, b) => b.length - a.length)[0];
    add(longestToken);
  }

  return {
    correctedQuery,
    year,
    semanticTokens,
    variants: [...variants].slice(0, 7),
  };
}

function minEditDistanceWithin(token: string, candidates: string[]): number {
  let best = Number.POSITIVE_INFINITY;
  for (const candidate of candidates) {
    const delta = Math.abs(candidate.length - token.length);
    if (delta > 2) continue;
    const distance = editDistance(token, candidate);
    if (distance < best) best = distance;
    if (best <= 1) return best;
  }
  return best;
}

function editDistance(a: string, b: string): number {
  if (a === b) return 0;
  if (!a) return b.length;
  if (!b) return a.length;
  const dp = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = 0; i <= a.length; i += 1) dp[i][0] = i;
  for (let j = 0; j <= b.length; j += 1) dp[0][j] = j;
  for (let i = 1; i <= a.length; i += 1) {
    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost);
    }
  }
  return dp[a.length][b.length];
}

function scoreEventCandidate(
  event: EventSearchItem,
  context: {
    normalizedQuery: string;
    correctedQuery: string;
    semanticTokens: string[];
    year: number | null;
  },
): number {
  const eventKey = normalizeText(event.event_key || '');
  const name = normalizeText(event.name || '');
  const city = normalizeText(event.city || '');
  const state = normalizeText(event.state_prov || '');
  const country = normalizeText(event.country || '');
  const haystack = [eventKey, name, city, state, country].filter(Boolean).join(' ');
  const hayTokens = tokenize(haystack);

  let score = 0;

  if (context.year !== null) {
    if (event.year === context.year) score += 30;
    else if (Math.abs(event.year - context.year) <= 1) score += 8;
    else score -= 4;
  }

  if (context.normalizedQuery && eventKey === context.normalizedQuery) score += 120;
  if (context.normalizedQuery && eventKey.startsWith(context.normalizedQuery)) score += 76;
  if (context.correctedQuery && haystack.includes(context.correctedQuery)) score += 32;
  if (context.normalizedQuery && context.normalizedQuery !== context.correctedQuery && haystack.includes(context.normalizedQuery)) {
    score += 16;
  }

  for (const token of context.semanticTokens) {
    if (!token || STOP_TOKENS.has(token)) continue;
    let tokenScore = 0;
    if (hayTokens.includes(token)) {
      tokenScore = 16;
    } else if (hayTokens.some((candidate) => candidate.startsWith(token) || token.startsWith(candidate))) {
      tokenScore = 12;
    } else {
      const distance = minEditDistanceWithin(token, hayTokens);
      if (Number.isFinite(distance) && distance <= 1) tokenScore = 9;
      else if (Number.isFinite(distance) && distance <= 2) tokenScore = 5;
    }

    if (BOOST_TOKENS.has(token) && name.includes(token)) tokenScore += 3;
    score += tokenScore;
  }

  if (context.semanticTokens.includes('houston') && eventKey.includes('hou')) score += 6;
  if (context.semanticTokens.includes('toronto') && (name.includes('toronto') || city.includes('toronto'))) score += 8;
  if (context.semanticTokens.includes('australia') || context.semanticTokens.includes('australian')) {
    if (country.includes('australia')) score += 10;
    if (name.includes('australia') || name.includes('australian')) score += 6;
  }

  return score;
}

export type SmartEventSearchResult = {
  events: EventSearchItem[];
  bestMatch: EventSearchItem | null;
  correctedQuery: string;
  queryVariants: string[];
};

export async function smartSearchEvents(
  rawQuery: string,
  options?: {
    preferredYear?: number;
    fallbackYear?: number;
    maxResults?: number;
    seedEvents?: EventSearchItem[];
    maxNetworkVariants?: number;
    includeSuggestedFallback?: boolean;
    fastMode?: boolean;
    localOnly?: boolean;
  },
): Promise<SmartEventSearchResult> {
  const normalizedQuery = normalizeText(rawQuery);
  if (!normalizedQuery) {
    return {
      events: [],
      bestMatch: null,
      correctedQuery: '',
      queryVariants: [],
    };
  }

  const preferredYear = options?.preferredYear ?? 2026;
  const fallbackYear = options?.fallbackYear ?? 2025;
  const fastMode = options?.fastMode === true;
  const localOnly = options?.localOnly === true;
  const maxResults = Math.max(1, Math.min(options?.maxResults ?? 60, 240));
  const maxNetworkVariants = Math.max(
    0,
    Math.min(options?.maxNetworkVariants ?? (fastMode ? 3 : 7), 7),
  );
  const includeSuggestedFallback = options?.includeSuggestedFallback ?? (!fastMode && !localOnly);

  const context = buildQueryVariants(rawQuery);
  const candidatesByKey = new Map<string, EventSearchItem>();

  for (const indexed of getEventSearchIndexSnapshot()) {
    const key = String(indexed.event_key || '').toLowerCase();
    if (key && !candidatesByKey.has(key)) candidatesByKey.set(key, indexed);
  }

  for (const seed of options?.seedEvents || []) {
    const key = String(seed.event_key || '').toLowerCase();
    if (!key) continue;
    candidatesByKey.set(key, seed);
  }

  const variantQueries = context.variants.slice(0, maxNetworkVariants);
  if (!localOnly && variantQueries.length > 0) {
    const variantResponses = await Promise.allSettled(variantQueries.map((query) => searchEvents(query)));
    for (const response of variantResponses) {
      if (response.status !== 'fulfilled') continue;
      for (const event of response.value.events || []) {
        const key = String(event.event_key || '').toLowerCase();
        if (!key) continue;
        if (!candidatesByKey.has(key)) candidatesByKey.set(key, event);
      }
      mergeEventsIntoSearchIndex(response.value.events || []);
    }
  }

  if (!localOnly && includeSuggestedFallback && candidatesByKey.size < 20) {
    try {
      const suggested = await getSuggestedEvents(preferredYear, fallbackYear, 120);
      mergeEventsIntoSearchIndex(suggested.events || []);
      for (const event of suggested.events || []) {
        const key = String(event.event_key || '').toLowerCase();
        if (!key) continue;
        if (!candidatesByKey.has(key)) candidatesByKey.set(key, event);
      }
    } catch {
      // Keep search responsive even if suggested-events fetch fails.
    }
  }

  const scored = [...candidatesByKey.values()]
    .map((event) => ({
      event,
      score: scoreEventCandidate(event, {
        normalizedQuery,
        correctedQuery: context.correctedQuery,
        semanticTokens: context.semanticTokens,
        year: context.year,
      }),
    }))
    .sort((a, b) => {
      if (a.score !== b.score) return b.score - a.score;
      if (a.event.year !== b.event.year) return b.event.year - a.event.year;
      return a.event.event_key.localeCompare(b.event.event_key);
    });

  const events = scored
    .filter((row) => row.score > 0)
    .slice(0, maxResults)
    .map((row) => row.event);
  const bestMatch = events[0] || null;

  return {
    events,
    bestMatch,
    correctedQuery: context.correctedQuery,
    queryVariants: context.variants,
  };
}
