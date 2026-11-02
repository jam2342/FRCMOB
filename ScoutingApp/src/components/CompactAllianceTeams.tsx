import type { EventScheduleItem } from '../api';
import { teamDisplayNumber } from '../pages/centerUtils';

// One alliance on a compact match row, a nowrap span per team. As one joined string
// ("#9427 · #971 · #1912") it either ended in "…" while the other side had room, or
// wrapped with a "·" left dangling at each line end. Separate spans wrap between teams.
// Every robot is listed: "+1" hid the third one, and a fan looking for their team had to open the match.
export function CompactAllianceTeams({ teams, side }: { teams: EventScheduleItem['red']; side: 'red' | 'blue' }) {
  const numbers = Array.isArray(teams) ? teams.map((team) => `#${teamDisplayNumber(team)}`) : [];
  return (
    <span className={`home-match-compact-side ${side}`} title={numbers.join(' · ') || undefined}>
      {numbers.length === 0
        ? 'TBD'
        : numbers.map((number) => <span key={number} className="home-match-compact-team">{number}</span>)}
    </span>
  );
}
