import { matchLabelFromKey } from '../utils/matchLabel';

// Saved recordings and drafts only carry a key; "Recording" covers one with no usable match.
export function scoutingMatchLabel(key: string): string {
  return matchLabelFromKey(key) ?? 'Recording';
}
