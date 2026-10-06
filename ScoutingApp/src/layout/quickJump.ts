// "cheesy poofs" → "The Cheesy Poofs". Only when the typed words cover most of the name, so a
// place like "houston" still searches events instead of jumping to a team that mentions it.
export function typedTeamName(typed: string, nickname: string | null): boolean {
  const name = (nickname || '').trim().toLowerCase().replace(/^(the|team)\s+/, '');
  const query = typed.trim().toLowerCase().replace(/^(the|team)\s+/, '');
  if (!name || query.length < 3) return false;
  return name === query || (name.includes(query) && query.length >= name.length * 0.6);
}
