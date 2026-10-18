import { describe, expect, it } from 'vitest';
import type { EventSearchItem } from '../../api';
import { buildPaletteItems, matchKeyFromQuery, matchesWords, type PaletteInput } from './paletteItems';

const events: EventSearchItem[] = [
  { event_key: '2026cmptx', name: 'Einstein Field', year: 2026, city: 'Houston' },
  { event_key: '2026arc', name: 'Archimedes Division', year: 2026, city: 'Houston' },
  { event_key: '2026casd', name: 'San Diego Regional', year: 2026, city: 'San Diego' },
];
const base: PaletteInput = {
  query: '', recentEvents: ['2026arc'], recentTeams: ['frc254'], teams: [], events,
  contextEventKey: '2026arc', pages: [{ to: '/compare/picklist', label: 'Picklist', keywords: 'pick list' }],
  actions: [{ id: 'action-theme', group: 'Actions', label: 'Switch to light theme', action: 'theme', keywords: 'theme dark light' }],
  eventName: (key) => events.find((e) => e.event_key === key)?.name ?? key,
};

describe('palette items', () => {
  it('lists recents, pages and actions before anything is typed', () => {
    const items = buildPaletteItems(base);
    expect(items.map((i) => i.group)).toEqual(['Recent', 'Recent', 'Pages', 'Actions']);
    expect(items[0]).toMatchObject({ label: 'Archimedes Division', to: '/events?event=2026arc' });
    expect(items[1]).toMatchObject({ label: 'Team 254', to: '/team-center?team=frc254' });
  });

  it('turns a typed number into a team at once, with its name once search answers', () => {
    const typed = buildPaletteItems({ ...base, query: '254' });
    expect(typed[0]).toMatchObject({ group: 'Teams', label: 'Team 254', to: '/team-center?team=frc254' });
    const named = buildPaletteItems({ ...base, query: '254', teams: [{ team_key: 'frc254', team_number: 254, nickname: 'The Cheesy Poofs', analyzed_matches: 0 }] });
    expect(named[0].detail).toBe('The Cheesy Poofs');
    expect(named.filter((i) => i.group === 'Teams')).toHaveLength(1);
  });

  it('finds events by the start of any word and matches from short codes in the current event', () => {
    expect(buildPaletteItems({ ...base, query: 'san di' }).find((i) => i.group === 'Events')?.label).toBe('San Diego Regional');
    expect(buildPaletteItems({ ...base, query: 'hou' }).filter((i) => i.group === 'Events')).toHaveLength(2);
    expect(buildPaletteItems({ ...base, query: 'q41' })[0]).toMatchObject({ group: 'Matches', label: 'Qualification 41', to: '/match-center?event=2026arc&match=2026arc_qm41' });
    expect(buildPaletteItems({ ...base, query: 'theme' }).map((i) => i.id)).toContain('action-theme');
    expect(buildPaletteItems({ ...base, query: 'pick' }).map((i) => i.label)).toContain('Picklist');
  });

  it('reads match codes', () => {
    expect(matchKeyFromQuery('qual 7', '2026arc')).toBe('2026arc_qm7');
    expect(matchKeyFromQuery('sf 3-2', '2026arc')).toBe('2026arc_sf3m2');
    expect(matchKeyFromQuery('final 2', '2026arc')).toBe('2026arc_f1m2');
    expect(matchKeyFromQuery('2026casd_qm12', '')).toBe('2026casd_qm12');
    expect(matchKeyFromQuery('q7', '')).toBeNull();
    expect(matchesWords('Einstein Field', 'ein fi')).toBe(true);
    expect(matchesWords('Einstein Field', 'stein')).toBe(false);
  });
});
