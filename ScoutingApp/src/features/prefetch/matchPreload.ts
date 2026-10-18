import { getEventSchedule, type EventScheduleItem } from '../../api';
import { readStoredCenterContext } from '../../layout/centerContext';
import { matchStartTime } from '../../pages/centerUtils';
import { shouldPrefetchRoutes } from '../../routePrefetch';
import { readMyTeamKey } from '../workspace/myTeam';
import { loadMatchCenterPredictions, loadTeamCenterIntel } from './pageRequests';

// With the app open, fetch what a team looks at before its match (Match Center and
// the six teams' pages) once that match is under 15 minutes away, so opening them
// in the queue is instant. Once per match; only while the app is visible.

const CHECK_EVERY_MS = 60_000;
const LEAD_MS = 15 * 60_000;
const warmed = new Set<string>();

export function nextMatchToWarm(matches: EventScheduleItem[], teamKey: string, nowMs: number): EventScheduleItem | null {
  const team = teamKey.trim().toLowerCase();
  let best: EventScheduleItem | null = null;
  let bestStart = Infinity;
  for (const match of matches) {
    if (match.is_completed) continue;
    const plays = [...(match.red || []), ...(match.blue || [])].some((slot) => slot.team_key === team);
    if (!plays) continue;
    const startSec = matchStartTime(match);
    if (!startSec) continue;
    const startMs = startSec * 1000;
    if (startMs <= nowMs || startMs - nowMs > LEAD_MS) continue;
    if (startMs < bestStart) {
      best = match;
      bestStart = startMs;
    }
  }
  return best;
}

async function warmUpcomingMatch(nowMs = Date.now()): Promise<string | null> {
  const eventKey = readStoredCenterContext().eventKey;
  const teamKey = readMyTeamKey();
  if (!eventKey || !teamKey) return null;
  const schedule = await getEventSchedule(eventKey, false);
  const match = nextMatchToWarm(schedule.matches || [], teamKey, nowMs);
  if (!match || warmed.has(match.match_key)) return null;
  warmed.add(match.match_key);
  const teams = [...(match.red || []), ...(match.blue || [])].map((slot) => slot.team_key).filter(Boolean);
  const pending: Array<Promise<unknown>> = [loadMatchCenterPredictions(eventKey), ...teams.map((team) => loadTeamCenterIntel(team, eventKey))];
  for (const request of pending) request.catch(() => undefined);
  return match.match_key;
}

let started = false;

export function startMatchPreload(): void {
  if (started || typeof window === 'undefined') return;
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
  if (!shouldPrefetchRoutes(connection)) return;
  started = true;
  const check = () => {
    if (document.visibilityState !== 'visible') return;
    void warmUpcomingMatch().catch(() => undefined);
  };
  window.setTimeout(check, 10_000);
  window.setInterval(check, CHECK_EVERY_MS);
  document.addEventListener('visibilitychange', check);
}
