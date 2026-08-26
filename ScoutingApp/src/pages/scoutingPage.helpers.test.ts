import { describe, expect, it } from 'vitest';
import {
  EMPTY_FORM,
  contentAreaTop,
  currentSidebarWidth,
  defaultFloatingTimerPosition,
  floatingTimerBounds,
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

describe('floating timer placement', () => {
  function withSidebar(width: string) {
    const shell = document.createElement('div');
    shell.className = 'product-shell';
    shell.style.setProperty('--ps-sidebar-current-width', width);
    document.body.appendChild(shell);
    return () => shell.remove();
  }

  it('reports no sidebar below the desktop breakpoint', () => {
    const cleanup = withSidebar('260px');
    // The rail is off screen under 1120px, so the timer may use the full width.
    expect(currentSidebarWidth(900)).toBe(0);
    expect(currentSidebarWidth(1120)).toBe(0);
    cleanup();
  });

  it('reads the live sidebar width on desktop', () => {
    const cleanup = withSidebar('260px');
    expect(currentSidebarWidth(1440)).toBe(260);
    cleanup();
  });

  it('follows the sidebar when it collapses', () => {
    const cleanup = withSidebar('74px');
    expect(currentSidebarWidth(1440)).toBe(74);
    cleanup();
  });

  it('defaults clear of the sidebar instead of on top of it', () => {
    const cleanup = withSidebar('260px');
    // The old default was x: 10, which covered the COLLAPSE control and Home.
    expect(defaultFloatingTimerPosition(1440).x).toBeGreaterThan(260);
    cleanup();
  });

  it('defaults below the chrome, measured rather than assumed', () => {
    const cleanup = withSidebar('260px');
    const content = document.createElement('div');
    content.className = 'ps-content';
    document.body.appendChild(content);
    // jsdom gives every element a zero rect, so the helper falls back — the
    // point of the test is that it reads the element rather than hardcoding a
    // constant that a growing context strip would outgrow.
    expect(contentAreaTop()).toBe(74);
    // Anchored to the bottom of the viewport, not to a constant offset from the
    // top — every previous top-anchored default ended up on top of something
    // when the chrome above it changed height.
    expect(defaultFloatingTimerPosition(1440).y).toBeGreaterThan(window.innerHeight / 2);
    content.remove();
    cleanup();
  });

  it('will not let a drag put the timer back over the nav', () => {
    const cleanup = withSidebar('260px');
    // floatingTimerBounds reads the viewport itself; jsdom defaults to 1024,
    // which is below the breakpoint where the rail exists.
    const originalWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { value: 1440, configurable: true });
    expect(floatingTimerBounds().minX).toBeGreaterThan(260);
    Object.defineProperty(window, 'innerWidth', { value: originalWidth, configurable: true });
    cleanup();
  });

  it('keeps the full width on mobile, where there is no rail', () => {
    const cleanup = withSidebar('260px');
    expect(defaultFloatingTimerPosition(390)).toEqual({ x: 8, y: 64 });
    cleanup();
  });
});
