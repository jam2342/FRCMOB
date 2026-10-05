import { describe, expect, it } from 'vitest';
import { isRobotSignal, officialMetricsNote, parseOfficialStats, scoreAgainst, signalRankLabel } from './teamCenterOfficial';
import { SEASON_BENCHMARKS } from '../config/season';

const PAYLOAD_254 = {
  available: true,
  matches: 71,
  copr_matches: 67,
  fuel_per_match: 350.6,
  fuel_per_active_minute: 131.5,
  auto_points_per_match: 68.1,
  events: [
    { event_key: '2026casj', matches: 12, copr: true },
    { event_key: '2026cmptx', matches: 4, copr: false },
    { event_key: '2026cur', matches: 55, copr: true },
  ],
  climb: { matches: 71, climbs: 0, rate: 0 },
};

describe('parseOfficialStats', () => {
  it('reads the backend block', () => {
    const stats = parseOfficialStats(PAYLOAD_254);
    expect(stats?.fuel_per_match).toBe(350.6);
    expect(stats?.climb).toEqual({ matches: 71, climbs: 0, rate: 0 });
    expect(stats?.events).toHaveLength(3);
  });

  it('treats a missing or unavailable block as nothing', () => {
    expect(parseOfficialStats(undefined)).toBeNull();
    expect(parseOfficialStats({ available: false })).toBeNull();
  });
});

describe('officialMetricsNote', () => {
  it('names the source and how much data it rests on', () => {
    expect(officialMetricsNote(parseOfficialStats(PAYLOAD_254))).toBe(
      "Nobody has scouted this team yet. Fuel and auto are TBA's per-team estimates from 67 official matches at 2 events this season, so one event's numbers can differ; climb is its official record.",
    );
  });

  it('says so when there is nothing to fall back on', () => {
    expect(officialMetricsNote(null)).toMatch(/no official results/);
  });
});

describe('scoreAgainst', () => {
  // The bars used last season's scale, so 6 fuel a minute already filled one.
  it('fills the bar only for an elite REBUILT robot', () => {
    expect(scoreAgainst(171, SEASON_BENCHMARKS.eliteFuelPerActiveMin)).toBe(100);
    expect(scoreAgainst(21, SEASON_BENCHMARKS.eliteFuelPerActiveMin)).toBeCloseTo(12.4, 1);
    expect(scoreAgainst(null, SEASON_BENCHMARKS.eliteFuelPerActiveMin)).toBeNull();
  });
});

describe('rating signals', () => {
  it('says where the team sits instead of printing raw metric pairs', () => {
    expect(signalRankLabel(99.8, 'strength')).toBe('Top 1%');
    expect(signalRankLabel(85, 'strength')).toBe('Top 15%');
    expect(signalRankLabel(8.5, 'risk')).toBe('Bottom 9%');
    expect(signalRankLabel(null, 'risk')).toBe('');
  });

  it('drops signals about the retired video pipeline', () => {
    expect(isRobotSignal('No analyzed clips yet')).toBe(false);
    expect(isRobotSignal('Output trend cooling')).toBe(true);
  });
});
