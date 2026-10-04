import type { EventTeamLiveFormEntry } from '../api';

// Last five results as W/L/T pills (Events, Match Center and Team Center each had a copy).
export function TeamFormStrip({ entry }: { entry: EventTeamLiveFormEntry | null }) {
  const form = entry?.recent_form || [];
  if (form.length === 0) {
    return <span className="center-form-empty">No recent form</span>;
  }
  return (
    <span className="center-form-strip" aria-label="Last five matches">
      {form.map((result, idx) => (
        <span
          key={`${entry?.team_key || 'team'}-form-${idx}`}
          className={`center-form-pill ${result === 'W' ? 'win' : result === 'L' ? 'loss' : 'tie'}`.trim()}
          title={`Result ${result}`}
        >
          {result}
        </span>
      ))}
    </span>
  );
}
