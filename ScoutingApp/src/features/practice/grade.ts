import type { MatchPracticeResponse } from '../../api';
import { ENDGAME_LABELS } from '../../pages/scoutingPage.helpers';
import type { EndgameMode } from '../../pages/scoutingPage.types';

export type PracticeAnswers = {
  autoFuel: number;
  teleopFuel: number;
  autoClimb: boolean;
  endgame: EndgameMode;
};

export type Verdict = 'right' | 'off' | 'plausible' | 'unknown';
export type PracticeCheck = { id: string; label: string; yours: string; official: string; verdict: Verdict; note: string };

// Tower points per climbed level in the official breakdown (10, 20, 30).
const LEVEL_POINTS = 10;

function climbedLevels(points: number): number {
  return Math.round(points / LEVEL_POINTS);
}

function levelsText(levels: number): string {
  return levels <= 0 ? 'No climb' : `Climbed ${levels} level${levels === 1 ? '' : 's'}`;
}

// A practice report checked against the official result. Climbs are recorded per robot, so
// they're right or wrong; fuel is only official for the whole alliance, so one robot's count
// can be shown to be impossible but not proven exact.
export function gradePractice(answers: PracticeAnswers, practice: MatchPracticeResponse): PracticeCheck[] {
  const official = practice.official;
  const checks: PracticeCheck[] = [];

  const autoOfficial = official.auto_tower_points;
  checks.push({
    id: 'auto-climb',
    label: 'Climb in auto',
    yours: answers.autoClimb ? 'Climbed' : 'No climb',
    official: autoOfficial == null ? '—' : autoOfficial > 0 ? 'Climbed' : 'No climb',
    verdict: autoOfficial == null ? 'unknown' : (autoOfficial > 0) === answers.autoClimb ? 'right' : 'off',
    note: autoOfficial == null ? 'The official result has no auto climb for this robot.' : '',
  });

  const endOfficial = official.endgame_tower_points;
  const yourLevels = answers.endgame.startsWith('climb_level_') ? Number(answers.endgame.slice(-1)) : 0;
  const officialLevels = endOfficial == null ? null : climbedLevels(endOfficial);
  checks.push({
    id: 'endgame',
    label: 'Endgame climb',
    yours: ENDGAME_LABELS[answers.endgame],
    official: officialLevels == null ? '—' : levelsText(officialLevels),
    verdict: officialLevels == null ? 'unknown' : officialLevels === yourLevels ? 'right' : 'off',
    note: officialLevels == null
      ? 'The official result has no endgame climb for this robot.'
      : yourLevels === 0 && answers.endgame === 'parked'
        ? 'Parking isn’t in the official climb points, so only whether it climbed is checked.'
        : '',
  });

  const fuel = answers.autoFuel + answers.teleopFuel;
  const allianceFuel = official.alliance_fuel;
  const share = allianceFuel ? Math.round((fuel / allianceFuel) * 100) : null;
  checks.push({
    id: 'fuel',
    label: 'Fuel scored',
    yours: String(fuel),
    official: allianceFuel == null ? '—' : `${allianceFuel} for the whole alliance`,
    verdict: allianceFuel == null ? 'unknown' : fuel > allianceFuel ? 'off' : 'plausible',
    note: allianceFuel == null
      ? 'No official fuel count for this match.'
      : fuel > allianceFuel
        ? 'More than the whole alliance scored: some of these were misses or another robot’s.'
        : `That’s ${share}% of the alliance’s fuel. Only the alliance total is official, so this can’t be checked exactly.`,
  });
  return checks;
}

export function practiceScore(checks: readonly PracticeCheck[]): { right: number; checked: number } {
  const exact = checks.filter((check) => check.verdict === 'right' || check.verdict === 'off');
  return { right: exact.filter((check) => check.verdict === 'right').length, checked: exact.length };
}
