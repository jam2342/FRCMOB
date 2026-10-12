export function scoutingMatchLabel(key: string): string {
  const suffix = key.split('_').pop() || '';
  const qual = /^qm(\d+)$/i.exec(suffix);
  if (qual) return `Qual ${qual[1]}`;
  const playoff = /^(ef|qf|sf|f)(\d+)m(\d+)$/i.exec(suffix);
  if (playoff) {
    const level: Record<string, string> = { ef: 'Eighthfinal', qf: 'Quarterfinal', sf: 'Semifinal', f: 'Final' };
    return `${level[playoff[1].toLowerCase()]} ${playoff[2]} · Match ${playoff[3]}`;
  }
  return 'Recording';
}
