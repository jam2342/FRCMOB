import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { SEASON } from './season';
import { FIELD_ZONES } from '../features/onDevice/fieldZones';

type TemplateZone = { key: string; kind: string; polygon: { x: number; y: number }[] };

const config = JSON.parse(
  readFileSync(resolve(__dirname, '../../../backend/app/game_config/season_template.json'), 'utf8'),
);

describe('frontend season snapshot', () => {
  it('matches the backend game config', () => {
    expect(SEASON.year).toBe(config.season_year);
    expect(SEASON.fallbackYear).toBe(config.season_year - 1);
    expect(SEASON.name).toBe(config.season_name);
    expect(SEASON.matchSec).toBe(config.phases.total_sec);
    expect(SEASON.autoSec).toBe(config.phases.auto_sec);
    expect(SEASON.endgameSec).toBe(config.phases.endgame_sec);
    expect(SEASON.fieldLengthM).toBe(config.field.length_m);
    expect(SEASON.fieldWidthM).toBe(config.field.width_m);
  });

  it('tags on-device points with the backend zones', () => {
    // Offline runs sync as the same zone keys the server computes; a mismatch here
    // would split one season's heat maps into two vocabularies.
    const expected = (config.zones as TemplateZone[]).map((zone) => ({
      key: zone.key,
      kind: zone.kind,
      polygon: zone.polygon.map((point) => [point.x, point.y]),
    }));
    expect(FIELD_ZONES).toEqual(expected);
  });
});
