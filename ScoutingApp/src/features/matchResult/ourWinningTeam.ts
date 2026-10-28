type Lineup = { red?: Array<{ team_key: string }>; blue?: Array<{ team_key: string }> } | null | undefined;

// Our team's number when it was on the alliance that just won; null for a loss, a tie, a match
// it wasn't in, or a device with no team.
export function ourWinningTeam(match: Lineup, winner: 'red' | 'blue' | 'tie' | null, teamNumber: number | null | undefined): number | null {
  if (!match || !teamNumber || (winner !== 'red' && winner !== 'blue')) return null;
  const key = `frc${teamNumber}`;
  return (match[winner] ?? []).some((team) => team.team_key.toLowerCase() === key) ? teamNumber : null;
}
