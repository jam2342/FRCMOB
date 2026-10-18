import type { EventScheduleItem } from '../../api';
import { matchStartTime } from '../../pages/centerUtils';

// Your team's next match, for the pill above the tab bar. Shown from an hour out until a few
// minutes after the start (the score takes a while to post; a match that started long ago and
// still isn't marked final is almost always over).

const SHOW_WITHIN_MS = 60 * 60_000;
const SOON_MS = 10 * 60_000;
const ON_FIELD_MS = 4 * 60_000;

export type NextMatchState = 'later' | 'soon' | 'now';

export type NextMatchInfo = {
  match: EventScheduleItem;
  startMs: number;
  alliance: 'red' | 'blue';
  stationLabel: string;
  partners: number[];
  opponents: number[];
  state: NextMatchState;
};

function stationLabel(alliance: 'red' | 'blue', station: string | null, index: number): string {
  const digit = String(station || '').match(/\d/)?.[0] ?? String(index + 1);
  return `${alliance === 'red' ? 'Red' : 'Blue'} ${digit}`;
}

export function findNextMatch(matches: readonly EventScheduleItem[], teamKey: string, nowMs: number): NextMatchInfo | null {
  const team = teamKey.trim().toLowerCase();
  if (!team) return null;
  let best: NextMatchInfo | null = null;
  for (const match of matches) {
    if (match.is_completed) continue;
    const startSec = matchStartTime(match);
    if (!startSec) continue;
    const startMs = startSec * 1000;
    if (startMs - nowMs > SHOW_WITHIN_MS || nowMs - startMs > ON_FIELD_MS) continue;
    const redIndex = (match.red || []).findIndex((slot) => slot.team_key === team);
    const blueIndex = (match.blue || []).findIndex((slot) => slot.team_key === team);
    if (redIndex < 0 && blueIndex < 0) continue;
    if (best && best.startMs <= startMs) continue;
    const alliance = redIndex >= 0 ? 'red' : 'blue';
    const own = alliance === 'red' ? match.red : match.blue;
    const other = alliance === 'red' ? match.blue : match.red;
    const index = alliance === 'red' ? redIndex : blueIndex;
    best = {
      match,
      startMs,
      alliance,
      stationLabel: stationLabel(alliance, own[index]?.station ?? null, index),
      partners: own.filter((slot) => slot.team_key !== team).map((slot) => slot.team_number),
      opponents: (other || []).map((slot) => slot.team_number),
      state: nowMs >= startMs ? 'now' : startMs - nowMs <= SOON_MS ? 'soon' : 'later',
    };
  }
  return best;
}

// "in 24 min" far out, a ticking "6:42" close in, "On the field" once it starts.
export function countdownLabel(startMs: number, nowMs: number): string {
  const left = startMs - nowMs;
  if (left <= 0) return 'On the field';
  if (left > SOON_MS) return `in ${Math.round(left / 60_000)} min`;
  const totalSec = Math.ceil(left / 1000);
  return `${Math.floor(totalSec / 60)}:${String(totalSec % 60).padStart(2, '0')}`;
}

export function shortMatchLabel(match: EventScheduleItem): string {
  return match.comp_level === 'qm' ? `Q${match.match_number}` : match.display_name;
}
