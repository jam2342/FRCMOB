import { describe, expect, it } from 'vitest';
import type { EventScheduleItem } from '../../api';
import { buildDoubleElimBracket, isEightAllianceDoubleElim, type BracketAlliance } from './bracketModel';

// Einstein 2026 (2026cmptx), as TBA published it: thirteen "sf" sets and a three-game final.
const LINEUPS: Record<number, number[]> = {
  1: [7407, 5940, 9470], 2: [8613, 6800, 254], 3: [4065, 4414, 1323], 4: [5181, 7769, 1690],
  5: [3506, 2056, 6329], 6: [6324, 4946, 2337], 7: [4915, 2481, 1678], 8: [868, 2910, 2046],
};
const NAMES = ['Archimedes', 'Curie', 'Daly', 'Galileo', 'Hopper', 'Johnson', 'Milstein', 'Newton'];
const ALLIANCES: BracketAlliance[] = NAMES.map((name, i) => ({ number: i + 1, name, teams: LINEUPS[i + 1] }));

function match(comp: string, set: number, game: number, red: number, blue: number, rs: number | null, bs: number | null): EventScheduleItem {
  const lineup = (seed: number, color: string) =>
    LINEUPS[seed].map((n, i) => ({ team_key: `frc${n}`, team_number: n, nickname: null, station: `${color}${i + 1}` }));
  const played = rs !== null && bs !== null;
  return {
    match_key: `2026cmptx_${comp}${set}m${game}`,
    display_name: `${comp.toUpperCase()} ${set}-${game}`,
    comp_level: comp,
    set_number: set,
    match_number: game,
    scheduled_time: null,
    has_time: false,
    red_score: rs,
    blue_score: bs,
    winner_alliance: played ? (rs > bs ? 'red' : bs > rs ? 'blue' : 'tie') : null,
    is_completed: played,
    red: lineup(red, 'r'),
    blue: lineup(blue, 'b'),
  };
}

const EINSTEIN: EventScheduleItem[] = [
  match('sf', 1, 1, 1, 8, 443, 665), match('sf', 2, 1, 4, 5, 618, 468),
  match('sf', 3, 1, 2, 7, 717, 632), match('sf', 4, 1, 3, 6, 842, 484),
  match('sf', 5, 1, 1, 5, 703, 599), match('sf', 6, 1, 7, 6, 688, 691),
  match('sf', 7, 1, 8, 4, 707, 470), match('sf', 8, 1, 2, 3, 627, 713),
  match('sf', 9, 1, 4, 6, 831, 513), match('sf', 10, 1, 2, 1, 769, 638),
  match('sf', 11, 1, 8, 3, 565, 692), match('sf', 12, 1, 2, 4, 699, 720),
  match('sf', 13, 1, 8, 4, 616, 605),
  match('f', 1, 1, 3, 8, 456, 518), match('f', 1, 2, 3, 8, 711, 561), match('f', 1, 3, 3, 8, 712, 406),
];

describe('double-elimination bracket', () => {
  it('recognises the 8-alliance format and leaves others to the list', () => {
    expect(isEightAllianceDoubleElim(EINSTEIN, 8)).toBe(true);
    expect(isEightAllianceDoubleElim(EINSTEIN, 4)).toBe(false);
    expect(isEightAllianceDoubleElim([match('qf', 1, 1, 1, 8, 1, 2)], 8)).toBe(false);
    expect(isEightAllianceDoubleElim([match('sf', 1, 1, 1, 8, 1, 2)], 0)).toBe(false);
  });

  it('places every set, names the alliances and finds the champion', () => {
    const bracket = buildDoubleElimBracket(EINSTEIN, ALLIANCES, Date.now());
    expect(bracket.matches[1].blue).toMatchObject({ seed: 8, name: 'Newton', score: 665, won: true });
    expect(bracket.matches[1].red).toMatchObject({ seed: 1, name: 'Archimedes', won: false });
    expect(bracket.matches[13].red).toMatchObject({ name: 'Newton', score: 616, won: true });
    expect(bracket.finals).toMatchObject({ redWins: 2, blueWins: 1, champion: 'red' });
    expect(bracket.finals.games.map((g) => g.winner)).toEqual(['blue', 'red', 'red']);
    expect(bracket.champion).toMatchObject({ seed: 3, name: 'Daly', teams: '4065 · 4414 · 1323' });
  });

  it('says where the alliances come from before a set is filled in', () => {
    const bracket = buildDoubleElimBracket(EINSTEIN.slice(0, 4), ALLIANCES, Date.now());
    expect(bracket.matches[7].red).toMatchObject({ placeholder: true, name: 'Winner M1' });
    expect(bracket.matches[9].blue).toMatchObject({ placeholder: true, name: 'Winner M6' });
    expect(bracket.finals.red).toMatchObject({ placeholder: true, name: 'Winner M11' });
    expect(bracket.champion).toBeNull();
  });

  it('names a regular event\'s alliances after their captain', () => {
    const generic = ALLIANCES.map((a) => ({ ...a, name: `Alliance ${a.number}` }));
    const schedule = [match('sf', 1, 1, 1, 8, 443, 665)];
    schedule[0].red[0].nickname = 'Wired Boars';
    const bracket = buildDoubleElimBracket(schedule, generic, Date.now());
    expect(bracket.matches[1].red.name).toBe('7407 Wired Boars');
    expect(bracket.matches[1].blue.name).toBe('868');
  });

  it('lets a replay decide a tied set', () => {
    const tied = [match('sf', 1, 1, 1, 8, 500, 500), match('sf', 1, 2, 1, 8, 520, 480)];
    const bracket = buildDoubleElimBracket(tied, ALLIANCES, Date.now());
    expect(bracket.matches[1]).toMatchObject({ done: true, matchKey: '2026cmptx_sf1m2' });
    expect(bracket.matches[1].red).toMatchObject({ won: true, score: 520 });
  });
});
