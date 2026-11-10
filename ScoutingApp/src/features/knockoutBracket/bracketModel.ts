import type { EventScheduleItem, EventScheduleTeam } from '../../api';
import { inferMatchCompleted, matchHasScores } from '../../pages/matchStatus';

// The 8-alliance double-elimination bracket FRC has used since 2023. TBA reports its thirteen
// matches as "sf" sets 1-13 and the best-of-3 final as "f" set 1, so grouping by comp level put
// every one of them under "Semifinals". This places each set where it sits in the bracket.

export type BracketAlliance = { number: number; name: string; teams: number[] };

export type BracketSide = {
  seed: number | null;
  name: string;
  teams: string;
  score: number | null;
  won: boolean;
  // No teams yet: `name` says where they come from ("Winner M7").
  placeholder: boolean;
};

export type BracketMatch = {
  set: number;
  label: string;
  matchKey: string | null;
  red: BracketSide;
  blue: BracketSide;
  done: boolean;
};

export type BracketFinals = {
  matchKey: string | null;
  red: BracketSide;
  blue: BracketSide;
  redWins: number;
  blueWins: number;
  games: { label: string; red: number; blue: number; winner: 'red' | 'blue' | 'tie' | null }[];
  champion: 'red' | 'blue' | null;
};

export type DoubleElimBracket = {
  matches: Record<number, BracketMatch>;
  finals: BracketFinals;
  champion: BracketSide | null;
};

// Where each set's two alliances come from, for sets not yet filled in.
const FEEDERS: Record<number, [string, string]> = {
  1: ['Alliance 1', 'Alliance 8'],
  2: ['Alliance 4', 'Alliance 5'],
  3: ['Alliance 2', 'Alliance 7'],
  4: ['Alliance 3', 'Alliance 6'],
  5: ['Loser M1', 'Loser M2'],
  6: ['Loser M3', 'Loser M4'],
  7: ['Winner M1', 'Winner M2'],
  8: ['Winner M3', 'Winner M4'],
  9: ['Loser M7', 'Winner M6'],
  10: ['Loser M8', 'Winner M5'],
  11: ['Winner M7', 'Winner M8'],
  12: ['Winner M10', 'Winner M9'],
  13: ['Loser M11', 'Winner M12'],
};
const FINALS_FEEDERS: [string, string] = ['Winner M11', 'Winner M13'];

function level(match: EventScheduleItem): string {
  return String(match.comp_level || '').trim().toLowerCase();
}

// Only the 8-alliance format gets the tree; older single-elimination events and the smaller
// offseason formats keep the list. A 4-alliance bracket also uses low "sf" set numbers, so
// the alliance count decides when it's known.
export function isEightAllianceDoubleElim(matches: readonly EventScheduleItem[], allianceCount: number): boolean {
  const knockout = matches.filter((match) => level(match) !== 'qm' && level(match) !== 'pr');
  if (knockout.length === 0) return false;
  if (knockout.some((match) => !['sf', 'f'].includes(level(match)))) return false;
  const sets = knockout.filter((match) => level(match) === 'sf').map((match) => Number(match.set_number) || 0);
  if (sets.some((set) => set < 1 || set > 13)) return false;
  if (allianceCount > 0) return allianceCount === 8;
  return sets.some((set) => set > 6);
}

function winnerOf(match: EventScheduleItem, nowMs: number): 'red' | 'blue' | 'tie' | null {
  if (match.winner_alliance === 'red' || match.winner_alliance === 'blue' || match.winner_alliance === 'tie') {
    return match.winner_alliance;
  }
  if (!inferMatchCompleted(match, nowMs) || !matchHasScores(match)) return null;
  if ((match.red_score ?? 0) > (match.blue_score ?? 0)) return 'red';
  if ((match.blue_score ?? 0) > (match.red_score ?? 0)) return 'blue';
  return 'tie';
}

function allianceFor(lineup: readonly EventScheduleTeam[], alliances: readonly BracketAlliance[]): BracketAlliance | null {
  const numbers = new Set(lineup.map((team) => team.team_number));
  let best: BracketAlliance | null = null;
  let bestHits = 0;
  for (const alliance of alliances) {
    const hits = alliance.teams.filter((team) => numbers.has(team)).length;
    if (hits > bestHits) {
      best = alliance;
      bestHits = hits;
    }
  }
  // A backup robot can stand in for one pick; two of three is still the same alliance.
  return bestHits >= 2 ? best : null;
}

// Champs divisions name their alliances ("Newton"); at a regular event every name is
// "Alliance N", which the seed already says, so the captain names it instead.
function sideName(lineup: readonly EventScheduleTeam[], alliance: BracketAlliance | null): string {
  if (alliance && !/^alliance\s*\d+$/i.test(alliance.name.trim())) return alliance.name.trim();
  const captainNumber = alliance?.teams[0] ?? lineup[0]?.team_number;
  const captain = lineup.find((team) => team.team_number === captainNumber) ?? lineup[0];
  if (!captain) return 'TBD';
  return captain.nickname ? `${captain.team_number} ${captain.nickname}` : `${captain.team_number}`;
}

function side(
  lineup: readonly EventScheduleTeam[],
  alliances: readonly BracketAlliance[],
  feeder: string,
  score: number | null,
  won: boolean,
): BracketSide {
  if (lineup.length === 0) {
    return { seed: null, name: feeder, teams: '', score: null, won: false, placeholder: true };
  }
  const alliance = allianceFor(lineup, alliances);
  return {
    seed: alliance?.number ?? null,
    name: sideName(lineup, alliance),
    teams: lineup.map((team) => team.team_number).join(' · '),
    score,
    won,
    placeholder: false,
  };
}

function bySet(matches: readonly EventScheduleItem[], comp: string): Map<number, EventScheduleItem[]> {
  const sets = new Map<number, EventScheduleItem[]>();
  for (const match of matches) {
    if (level(match) !== comp) continue;
    const set = Number(match.set_number) || 0;
    sets.set(set, [...(sets.get(set) ?? []), match]);
  }
  for (const games of sets.values()) games.sort((a, b) => a.match_number - b.match_number);
  return sets;
}

export function buildDoubleElimBracket(
  schedule: readonly EventScheduleItem[],
  alliances: readonly BracketAlliance[],
  nowMs: number,
): DoubleElimBracket {
  const sfSets = bySet(schedule, 'sf');
  const matches: Record<number, BracketMatch> = {};
  for (let set = 1; set <= 13; set += 1) {
    const games = sfSets.get(set) ?? [];
    // A tied match is replayed; the replay decides it.
    const decided = [...games].reverse().find((game) => {
      const winner = winnerOf(game, nowMs);
      return winner === 'red' || winner === 'blue';
    });
    const shown = decided ?? games[games.length - 1] ?? null;
    const winner = decided ? winnerOf(decided, nowMs) : null;
    const scored = shown && matchHasScores(shown);
    const [redFeeder, blueFeeder] = FEEDERS[set];
    matches[set] = {
      set,
      label: `M${set}`,
      matchKey: shown?.match_key ?? null,
      red: side(shown?.red ?? [], alliances, redFeeder, scored ? shown.red_score ?? null : null, winner === 'red'),
      blue: side(shown?.blue ?? [], alliances, blueFeeder, scored ? shown.blue_score ?? null : null, winner === 'blue'),
      done: Boolean(decided),
    };
  }

  const finalGames = bySet(schedule, 'f').get(1) ?? [];
  let redWins = 0;
  let blueWins = 0;
  const games = finalGames
    .filter((game) => matchHasScores(game))
    .map((game, index) => {
      const winner = winnerOf(game, nowMs);
      if (winner === 'red') redWins += 1;
      if (winner === 'blue') blueWins += 1;
      return { label: `Game ${index + 1}`, red: game.red_score ?? 0, blue: game.blue_score ?? 0, winner };
    });
  const champion = redWins >= 2 ? 'red' : blueWins >= 2 ? 'blue' : null;
  const first = finalGames[0] ?? null;
  const latest = finalGames[finalGames.length - 1] ?? null;
  const finals: BracketFinals = {
    matchKey: latest?.match_key ?? null,
    red: side(first?.red ?? [], alliances, FINALS_FEEDERS[0], first ? redWins : null, champion === 'red'),
    blue: side(first?.blue ?? [], alliances, FINALS_FEEDERS[1], first ? blueWins : null, champion === 'blue'),
    redWins,
    blueWins,
    games,
    champion,
  };

  return {
    matches,
    finals,
    champion: champion === 'red' ? finals.red : champion === 'blue' ? finals.blue : null,
  };
}
