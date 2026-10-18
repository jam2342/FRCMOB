import type { EventSearchItem, TeamSearchResponse } from '../../api';

type TeamSearchItem = TeamSearchResponse['teams'][number];

// What the ⌘K palette lists for what's been typed. Pure, so the ranking is testable; the
// component supplies team search results as they arrive.

export type PaletteGroup = 'Recent' | 'Teams' | 'Events' | 'Matches' | 'Pages' | 'Actions';

export type PaletteItem = {
  id: string;
  group: PaletteGroup;
  label: string;
  detail?: string;
  // A route to open, or an action id the component runs.
  to?: string;
  action?: PaletteActionId;
  keywords?: string;
};

export type PaletteActionId = 'theme' | 'copy-link' | 'my-team' | 'tour';

export type PalettePage = { to: string; label: string; keywords?: string };

export type PaletteInput = {
  query: string;
  recentEvents: readonly string[];
  recentTeams: readonly string[];
  teams: readonly TeamSearchItem[];
  events: readonly EventSearchItem[];
  contextEventKey: string;
  pages: readonly PalettePage[];
  actions: readonly PaletteItem[];
  eventName: (eventKey: string) => string;
};

const MAX_PER_GROUP = 5;

function teamNumberOf(teamKey: string): string {
  return teamKey.replace(/^frc/i, '');
}

function words(value: string): string[] {
  return value.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

// Every typed word has to start a word of the text ("hou cmp" finds "Houston Championship").
export function matchesWords(text: string, query: string): boolean {
  const haystack = words(text);
  const needles = words(query);
  if (needles.length === 0) return false;
  return needles.every((needle) => haystack.some((word) => word.startsWith(needle)));
}

const MATCH_PATTERNS: Array<[RegExp, (m: RegExpMatchArray) => string]> = [
  [/^(?:q|qm|qual|quals|qualification)\s*(\d{1,3})$/, (m) => `qm${Number(m[1])}`],
  [/^(?:sf|semi|semis)\s*(\d{1,2})(?:\s*[-m]\s*(\d))?$/, (m) => `sf${Number(m[1])}m${m[2] ? Number(m[2]) : 1}`],
  [/^(?:f|final|finals)\s*(\d)$/, (m) => `f1m${Number(m[1])}`],
];

export function matchKeyFromQuery(query: string, contextEventKey: string): string | null {
  const value = query.trim().toLowerCase();
  if (/^\d{4}[a-z0-9]+_(qm|sf|f)\d+(m\d+)?$/.test(value)) return value;
  if (!contextEventKey) return null;
  for (const [pattern, build] of MATCH_PATTERNS) {
    const found = value.match(pattern);
    if (found) return `${contextEventKey}_${build(found)}`;
  }
  return null;
}

function matchLabel(matchKey: string): string {
  const code = matchKey.split('_')[1] || matchKey;
  const qual = code.match(/^qm(\d+)$/);
  if (qual) return `Qualification ${qual[1]}`;
  const playoff = code.match(/^(sf|f)(\d+)m(\d+)$/);
  if (playoff) return playoff[1] === 'f' ? `Final ${playoff[3]}` : `Playoff ${playoff[2]}-${playoff[3]}`;
  return code.toUpperCase();
}

export function buildPaletteItems(input: PaletteInput): PaletteItem[] {
  const query = input.query.trim();
  const lower = query.toLowerCase();
  const items: PaletteItem[] = [];
  const teamRoute = (teamKey: string) => `/team-center?team=${teamKey}`;
  const eventRoute = (eventKey: string) => `/events?event=${eventKey}`;

  if (!query) {
    for (const eventKey of input.recentEvents.slice(0, 3)) {
      items.push({ id: `recent-event-${eventKey}`, group: 'Recent', label: input.eventName(eventKey), detail: 'Event', to: eventRoute(eventKey) });
    }
    for (const teamKey of input.recentTeams.slice(0, 3)) {
      items.push({ id: `recent-team-${teamKey}`, group: 'Recent', label: `Team ${teamNumberOf(teamKey)}`, detail: 'Team', to: teamRoute(teamKey) });
    }
    for (const page of input.pages) {
      items.push({ id: `page-${page.to}`, group: 'Pages', label: page.label, to: page.to });
    }
    items.push(...input.actions);
    return items;
  }

  const matchKey = matchKeyFromQuery(query, input.contextEventKey);
  if (matchKey) {
    const eventKey = matchKey.split('_')[0];
    items.push({
      id: `match-${matchKey}`, group: 'Matches', label: matchLabel(matchKey), detail: input.eventName(eventKey),
      to: `/match-center?event=${eventKey}&match=${matchKey}`,
    });
  }

  // A typed number is a team at once, before the search answers.
  const typedNumber = lower.replace(/^(team|frc|#)\s*/, '').match(/^\d{1,5}$/)?.[0];
  const seenTeams = new Set<string>();
  if (typedNumber) {
    const teamKey = `frc${Number(typedNumber)}`;
    const known = input.teams.find((team) => team.team_key.toLowerCase() === teamKey);
    seenTeams.add(teamKey);
    items.push({ id: `team-${teamKey}`, group: 'Teams', label: `Team ${Number(typedNumber)}`, detail: known?.nickname || undefined, to: teamRoute(teamKey) });
  }
  // Pages and actions whose names match go above the server's team and event results: "light"
  // means the theme switch, not a team that happens to be called Light-something.
  for (const page of input.pages) {
    if (matchesWords(`${page.label} ${page.keywords || ''}`, query)) {
      items.push({ id: `page-${page.to}`, group: 'Pages', label: page.label, to: page.to });
    }
  }
  for (const action of input.actions) {
    if (matchesWords(`${action.label} ${action.keywords || ''}`, query)) items.push(action);
  }

  for (const team of input.teams) {
    const teamKey = team.team_key.toLowerCase();
    if (seenTeams.has(teamKey) || seenTeams.size >= MAX_PER_GROUP) continue;
    seenTeams.add(teamKey);
    items.push({ id: `team-${teamKey}`, group: 'Teams', label: `Team ${team.team_number}`, detail: team.nickname || undefined, to: teamRoute(teamKey) });
  }

  let eventCount = 0;
  for (const event of input.events) {
    if (eventCount >= MAX_PER_GROUP) break;
    const text = `${event.name || ''} ${event.event_key} ${event.city || ''} ${event.state_prov || ''} ${event.year}`;
    if (!matchesWords(text, query) && event.event_key !== lower) continue;
    eventCount += 1;
    items.push({
      id: `event-${event.event_key}`, group: 'Events', label: event.name || input.eventName(event.event_key),
      detail: [event.city, event.year].filter(Boolean).join(' · ') || undefined, to: eventRoute(event.event_key),
    });
  }

  return items;
}
