// Teams with a saved pit entry, counted among the teams at the event (an entry for a team
// that left the event doesn't count).
export function pitProgress(teamKeys: string[], entryKeys: string[]): { done: number; total: number } {
  const teams = new Set(teamKeys.map((key) => key.toLowerCase()));
  const done = new Set(entryKeys.map((key) => key.toLowerCase()).filter((key) => teams.has(key)));
  return { done: done.size, total: teams.size };
}
