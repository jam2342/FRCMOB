// One way to write a match everywhere: the schedule's own style ("QM 12", "SF 3-1", "F 1-2"),
// which is what the server sends as display_name. Pages that only had a key used to invent their
// own ("Qual 12", "Semifinal 3 · Match 1", "Final 2"), so the same match read three ways.
const LEVELS: Record<string, string> = { qm: 'QM', ef: 'EF', qf: 'QF', sf: 'SF', f: 'F' };

export function matchLabel(compLevel: string, setNumber: number, matchNumber: number): string {
  const level = compLevel.toLowerCase();
  const name = LEVELS[level] ?? level.toUpperCase();
  return level === 'qm' ? `${name} ${matchNumber}` : `${name} ${setNumber}-${matchNumber}`;
}

// "2026arc_qm12" → "QM 12", "2026arc_sf3m1" → "SF 3-1". Null when the key isn't a match key.
export function matchLabelFromKey(matchKey: string): string | null {
  const code = (matchKey.split('_').pop() || '').toLowerCase();
  const qual = /^qm(\d+)$/.exec(code);
  if (qual) return matchLabel('qm', 1, Number(qual[1]));
  const playoff = /^(ef|qf|sf|f)(\d+)m(\d+)$/.exec(code);
  if (playoff) return matchLabel(playoff[1], Number(playoff[2]), Number(playoff[3]));
  return null;
}
