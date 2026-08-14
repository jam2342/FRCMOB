import { asRecord, parseNumber, teamNumberFromTeamKey } from './centerUtils';

export type BreakdownStage = 'qualifying' | 'knockout';

export function resolveBreakdownStage(
  current: BreakdownStage,
  hasQualifying: boolean,
  hasKnockout: boolean,
  userLocked = false,
): BreakdownStage {
  if (userLocked) return current;
  if (current === 'qualifying' && hasQualifying) return current;
  if (current === 'knockout' && hasKnockout) return current;
  if (hasQualifying) return 'qualifying';
  if (hasKnockout) return 'knockout';
  return 'qualifying';
}

function normalizeLookupToken(value: string): string {
  return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]/g, '');
}

export function readRecordFieldLoose(record: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(record, key)) return record[key];
  }
  const normalizedTargets = new Set(keys.map(normalizeLookupToken).filter((value) => value.length > 0));
  if (normalizedTargets.size === 0) return undefined;
  for (const [rawKey, rawValue] of Object.entries(record)) {
    if (normalizedTargets.has(normalizeLookupToken(rawKey))) return rawValue;
  }
  return undefined;
}

export function parseAllianceSlotTeam(value: unknown): { teamNumber: number; name: string } | null {
  if (typeof value === 'number' || typeof value === 'string') {
    const teamNumber =
      parseNumber(value) ??
      (typeof value === 'string' ? teamNumberFromTeamKey(value) : null);
    if (teamNumber === null || teamNumber <= 0) return null;
    return {
      teamNumber,
      name: '',
    };
  }

  const payload = asRecord(value);
  if (!payload) return null;
  const rawTeamIdentifier = readRecordFieldLoose(payload, [
    'teamNumber',
    'team_number',
    'number',
    'team',
    'teamnum',
    'teamKey',
    'team_key',
  ]);
  const teamNumber =
    parseNumber(rawTeamIdentifier) ??
    (typeof rawTeamIdentifier === 'string' ? teamNumberFromTeamKey(rawTeamIdentifier) : null);
  if (teamNumber === null || teamNumber <= 0) return null;
  const rawName = readRecordFieldLoose(payload, ['name', 'teamName', 'team_name', 'nickname', 'teamNickname']);
  const name = typeof rawName === 'string' ? rawName.trim() : '';
  return {
    teamNumber,
    name,
  };
}
