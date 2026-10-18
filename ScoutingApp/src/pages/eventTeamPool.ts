import { asRecord, parseNumber } from './centerUtils';

// An event's teams as the alliance tools use them: key, number, name and rating, best first.
// Alliance Advisor and Picklist each built this list with the same twelve lines.
export type PoolTeam = {
  team_key: string;
  team_number: number;
  nickname: string | null;
  rating_0_100: number | null;
};

export function rankedTeamPool(teams: unknown): PoolTeam[] {
  return (Array.isArray(teams) ? teams : [])
    .map((entry) => {
      const row = asRecord(entry);
      const rating = asRecord(row?.rating);
      return {
        team_key: String(row?.team_key || '').toLowerCase(),
        team_number: parseNumber(row?.team_number) ?? 0,
        nickname: typeof row?.nickname === 'string' ? row.nickname : null,
        rating_0_100: parseNumber(rating?.rating_0_100),
      };
    })
    .filter((row) => row.team_key.length > 0)
    .sort((a, b) => (b.rating_0_100 ?? 0) - (a.rating_0_100 ?? 0));
}
