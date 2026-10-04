import { useState } from 'react';

type Alliance = 'red' | 'blue';
type MatchTeam = { teamKey: string; alliance: Alliance };
export type IdentityTrack = {
  trackId: number; pointCount: number; dominantZone: string | null;
  startSec: number; endSec: number; suggestedTeam: string | null; alliance: Alliance | null;
};

// The path's alliance first (by bumper colour), then the rest in case the colour misread.
function teamsForAlliance(teams: MatchTeam[], alliance: Alliance | null): MatchTeam[] {
  if (!alliance) return teams;
  return [...teams.filter((t) => t.alliance === alliance), ...teams.filter((t) => t.alliance !== alliance)];
}

export type PathSuggestion = { teamKey: string; seedId: number; trackIds: number[] };

function teamLabel(teamKey: string): string {
  return teamKey.replace(/^frc/i, '');
}

export function TrackIdentityList({ summaries, identities, photoUrls, teams, onAssign, suggestion, onApplySuggestion, onDismissSuggestion }: {
  summaries: IdentityTrack[]; identities: Record<number, string>; photoUrls: Record<number, string>;
  teams: MatchTeam[]; onAssign: (trackId: number, teamKey: string) => void;
  // "probably the same robot" paths offered after an assignment (pathSuggestions.ts)
  suggestion?: PathSuggestion | null; onApplySuggestion?: () => void; onDismissSuggestion?: () => void;
}) {
  const [onlyUnassigned, setOnlyUnassigned] = useState(false);
  const [alliance, setAlliance] = useState<'all' | Alliance | 'unknown'>('all');
  const ordered = [...summaries].sort((a, b) => b.pointCount - a.pointCount || a.startSec - b.startSec || a.trackId - b.trackId);
  const visible = ordered.filter(t => (!onlyUnassigned || !identities[t.trackId])
    && (alliance === 'all' || (alliance === 'unknown' ? !t.alliance : t.alliance === alliance)));
  const assigned = summaries.filter(t => identities[t.trackId]);
  const totalPoints = summaries.reduce((n, t) => n + t.pointCount, 0);
  const assignedPoints = assigned.reduce((n, t) => n + t.pointCount, 0);
  const percent = totalPoints ? Math.round(100 * assignedPoints / totalPoints) : 0;
  return (
    <>
      <p aria-live="polite">{assigned.length} of {summaries.length} paths assigned · {percent}% of recorded points assigned</p>
      <div className="odr-identity-filters">
        <label><input type="checkbox" checked={onlyUnassigned} onChange={event => setOnlyUnassigned(event.target.checked)} /> Only unassigned paths</label>
        <label>Bumper colour <select className="odr-select" aria-label="Filter paths by bumper colour" value={alliance} onChange={event => setAlliance(event.target.value as typeof alliance)}>
          <option value="all">All colours</option><option value="red">Red</option><option value="blue">Blue</option><option value="unknown">Unknown</option>
        </select></label>
      </div>
      <p className="odr-hint">Longest paths first. Colour helps narrow the list; check the robot photo before assigning a team.</p>
      {suggestion && suggestion.trackIds.length > 0 ? (
        <div className="odr-suggestion" role="region" aria-label="Suggested paths for the same robot">
          <p className="odr-suggestion__title">
            {suggestion.trackIds.length === 1 ? 'This path looks like' : `These ${suggestion.trackIds.length} paths look like`} the same robot continuing. Apply {teamLabel(suggestion.teamKey)}?
          </p>
          <ul className="odr-suggestion__list">
            {suggestion.trackIds.map((id) => {
              const t = summaries.find((s) => s.trackId === id);
              return (
                <li key={id}>
                  {photoUrls[id] ? <img className="odr-suggestion__photo" src={photoUrls[id]} alt={`Robot on track ${id}`} /> : null}
                  <span>Track {id}{t ? ` · ${t.startSec.toFixed(1)}–${t.endSec.toFixed(1)}s` : ''}</span>
                </li>
              );
            })}
          </ul>
          <p className="odr-hint">Check the photos: about 1 suggestion in 10 is a different robot.</p>
          <div className="odr-actions">
            <button type="button" className="center-btn" onClick={onApplySuggestion}>Apply {teamLabel(suggestion.teamKey)} to {suggestion.trackIds.length === 1 ? 'it' : `all ${suggestion.trackIds.length}`}</button>
            <button type="button" className="center-btn ghost" onClick={onDismissSuggestion}>Not the same robot</button>
          </div>
        </div>
      ) : null}
          <div className="odr-tracks">
            {visible.map((t) => (
              <div
                key={t.trackId}
                className={`odr-track${identities[t.trackId] ? ' odr-track--assigned' : ''}`}
              >
                {photoUrls[t.trackId] ? (
                  <img
                    className="odr-track__photo"
                    src={photoUrls[t.trackId]}
                    alt={`Robot on track ${t.trackId}`}
                  />
                ) : null}
                <div className="odr-track__info">
                  <span className="odr-track__title">
                    Track {t.trackId} · {t.pointCount} recorded points
                  </span>
                  <span className="odr-track__meta">
                    {t.dominantZone ? t.dominantZone.replace(/_/g, ' ') : 'no zone'} ·{' '}
                    {t.startSec.toFixed(1)}–{t.endSec.toFixed(1)}s
                    {t.alliance ? ` · ${t.alliance} bumpers` : ''}
                    {t.suggestedTeam ? ` · looks like ${t.suggestedTeam}` : ''}
                  </span>
                </div>
                <select
                  className="odr-select"
                  aria-label={`Assign track ${t.trackId} to a team`}
                  value={identities[t.trackId] ?? ''}
                  onChange={(e) => onAssign(t.trackId, e.target.value)}
                >
                  <option value="">— leave out —</option>
                  {teamsForAlliance(teams, t.alliance).map((team) => (
                    <option key={team.teamKey} value={team.teamKey}>
                      {team.teamKey.replace(/^frc/i, '')} ({team.alliance})
                    </option>
                  ))}
                </select>
              </div>
            ))}
            {summaries.length === 0 ? (
              <p className="odr-error">
                No tracks met the minimum length. Re-record with a steadier, closer view.
              </p>
            ) : null}
          </div>
      {summaries.length > 0 && visible.length === 0 ? <p className="odr-hint">No paths match these filters. Show assigned paths or choose another bumper colour to review them.</p> : null}
    </>
  );
}
