import type { HeadToHeadRecord } from '../../api';

// "3 wins, 1 loss in 4 matches"; "No played matches" before they have a result.
export function recordLine(record: HeadToHeadRecord): string {
  if (!record.played) return 'No played matches';
  const parts = [`${record.wins} win${record.wins === 1 ? '' : 's'}`, `${record.losses} loss${record.losses === 1 ? '' : 'es'}`];
  if (record.ties) parts.push(`${record.ties} tie${record.ties === 1 ? '' : 's'}`);
  return `${parts.join(', ')} in ${record.played} match${record.played === 1 ? '' : 'es'}`;
}
