import { describe, expect, it } from 'vitest';
import {
  EMPTY_FORM,
  applyAutoScoutDraftPayload,
  buildSavedScoutingEntry,
  driverCompetency,
  manualScoutingRating,
  normalizeEntry,
  overallScoutRating,
  pointsFromForm,
} from './scoutingPage.helpers';

function buildRatings(form = EMPTY_FORM) {
  const points = pointsFromForm(form);
  const driver = driverCompetency(form);
  const manual = manualScoutingRating(form, driver);
  const overall = overallScoutRating(points, manual, driver);
  return { points, driver, manual, overall };
}

describe('scoutingPage helpers', () => {
  it('merges auto-scout draft patches into the live form without clobbering unrelated fields', () => {
    const baseForm = {
      ...EMPTY_FORM,
      teleop_scored: 7,
      offense_level_1_5: 2 as const,
    };

    const next = applyAutoScoutDraftPayload(baseForm, {
      form_patch: {
        auto_mobility: true,
        auto_scored: 3,
        endgame_mode: 'parked',
      },
      notes_seed: 'Auto seed',
      derived_insights: {},
    });

    expect(next.auto_mobility).toBe(true);
    expect(next.auto_scored).toBe(3);
    expect(next.endgame_mode).toBe('parked');
    expect(next.teleop_scored).toBe(7);
    expect(next.offense_level_1_5).toBe(2);
  });

  it('preserves reviewed-auto metadata and field overrides through normalizeEntry', () => {
    const form = applyAutoScoutDraftPayload(EMPTY_FORM, {
      form_patch: {
        auto_mobility: true,
        auto_scored: 2,
        teleop_scored: 9,
      },
      notes_seed: 'Robot crossed the mobility line cleanly.',
      derived_insights: {
        cycle_pace_summary: {
          est_cycles: 8,
          cycle_time_sec: 12.5,
        },
      },
    });
    const { points, driver, manual, overall } = buildRatings(form);

    const entry = buildSavedScoutingEntry({
      saved_at_ms: 1_710_000_000_000,
      scout_profile: 'Jamal',
      room_key: 'room-alpha',
      mode: 'match',
      event_key: '2026txhou',
      match_key: '2026txhou_qm1',
      match_display: 'Qualification 1',
      team_key: 'frc118',
      team_label: '#118 · Robonauts',
      alliance: 'red',
      station: 'r1',
      points,
      rp: {
        energized: false,
        supercharged: false,
        traversal: false,
        coop: false,
      },
      form,
      driver_competency: driver,
      manual_rating: manual,
      overall_scout_rating: overall,
      scouting_api_rating: null,
      api_snapshot: null,
      notes: 'Robot crossed the mobility line cleanly.\nHuman note.',
      entry_source: 'reviewed_auto',
      auto_scout_meta: {
        draft_id: 42,
        mapper_version: '2026_v1',
        analysis_version: 'video_v3_tracks',
        approved_at_ms: 1_710_000_005_000,
      },
      field_overrides: {
        auto_mobility: {
          from: true,
          to: false,
        },
      },
    });

    const normalized = normalizeEntry(entry);

    expect(normalized).not.toBeNull();
    expect(normalized?.entry_source).toBe('reviewed_auto');
    expect(normalized?.auto_scout_meta).toEqual({
      draft_id: 42,
      mapper_version: '2026_v1',
      analysis_version: 'video_v3_tracks',
      approved_at_ms: 1_710_000_005_000,
    });
    expect(normalized?.field_overrides).toEqual({
      auto_mobility: {
        from: true,
        to: false,
      },
    });
  });

  it('drops auto-scout metadata when saving a manual entry', () => {
    const form = {
      ...EMPTY_FORM,
      teleop_scored: 5,
    };
    const { points, driver, manual, overall } = buildRatings(form);

    const entry = buildSavedScoutingEntry({
      saved_at_ms: 1_710_000_010_000,
      scout_profile: 'Jamal',
      room_key: null,
      mode: 'match',
      event_key: '2026txhou',
      match_key: '2026txhou_qm2',
      match_display: 'Qualification 2',
      team_key: 'frc148',
      team_label: '#148 · Robowranglers',
      alliance: 'blue',
      station: 'b2',
      points,
      rp: {
        energized: false,
        supercharged: false,
        traversal: false,
        coop: false,
      },
      form,
      driver_competency: driver,
      manual_rating: manual,
      overall_scout_rating: overall,
      scouting_api_rating: null,
      api_snapshot: null,
      notes: 'Manual scout only.',
      entry_source: 'manual',
      auto_scout_meta: {
        draft_id: 99,
        mapper_version: '2026_v1',
        analysis_version: 'video_v3_tracks',
        approved_at_ms: 1_710_000_010_500,
      },
      field_overrides: {
        auto_mobility: {
          from: true,
          to: false,
        },
      },
    });

    expect(entry.entry_source).toBe('manual');
    expect(entry.auto_scout_meta).toBeNull();
    expect(entry.field_overrides).toBeNull();
  });
});
