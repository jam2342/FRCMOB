import { useSyncExternalStore } from 'react';
import { FieldHeatmap } from '../../components/cv/FieldHeatmap';
import type { StoredSession } from './offlineStore';
import { positionHeatmap, rawGridToHeatmap } from './resultHeatmaps';
import { getWorkspaceSession, subscribeWorkspaceSession } from '../workspace/workspaceSession';
import './OnDeviceRun.css';

// The server sends a reason code; a scout needs to know what it means for them.
function shiftPlayMissingText(reason: string): string {
  if (reason === 'shift1_active_alliance_unavailable' || reason === 'missing_shift1_active_alliance') {
    return "Offense and defense need the match's official results to know which hub was active first, and they aren't posted yet. The heatmaps are ready now.";
  }
  if (reason === 'analysis_failed') {
    return "Offense and defense couldn't be worked out for this run. The heatmaps are still ready.";
  }
  return "Offense and defense aren't available for this run. The heatmaps are still ready.";
}

// 1–5 segmented level bar for offense/defense.
function LevelMeter({
  label,
  level,
  confidence,
  assessable = true,
  variant,
}: {
  label: string;
  level: number;
  confidence?: number;
  assessable?: boolean;
  variant: 'offense' | 'defense';
}) {
  return (
    <div className={`odr-meter odr-meter--${variant}`}>
      <span className="odr-meter__label">
        <span>{label}</span>
        {assessable ? (
          <span className="odr-meter__value">
            {level}/5{confidence != null ? ` · ${Math.round(confidence * 100)}%` : ''}
          </span>
        ) : (
          <span className="odr-meter__na">n/a</span>
        )}
      </span>
      <div className="odr-meter__bar" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((n) => (
          <span key={n} className={`odr-meter__seg${assessable && n <= level ? ' is-on' : ''}`} />
        ))}
      </div>
    </div>
  );
}

function clock(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

export function RunResults({ session, syncing = false }: { session: StoredSession; syncing?: boolean }) {
  const workspaceId = useSyncExternalStore(subscribeWorkspaceSession, () => getWorkspaceSession()?.workspace.id ?? null);
  if (session.workspaceId != null && session.workspaceId !== workspaceId) {
    return <p className="odr-hint">Join the workspace that recorded this run to view its results.</p>;
  }
  const result = session.syncResult;
  const teams = Object.entries(session.payload.pointsByTeam).filter(([, points]) => points.length > 0);
  const pointCount = teams.reduce((sum, [, points]) => sum + points.length, 0);
  return (
    <section className="odr-section" aria-label="Run results">
      <h3 className="odr-results-title" tabIndex={-1}>Run results</h3>
      <p className="odr-hint">{session.matchKey || 'Recording'} · {new Date(session.createdAt).toLocaleString()}</p>
      <p className="odr-summary"><strong>{teams.length}</strong> robot{teams.length === 1 ? '' : 's'} · <strong>{pointCount}</strong> saved positions · <strong>{session.payload.sampledFrameCount}</strong> analyzed frames</p>
      <p className="odr-hint" role="status">
        {syncing ? 'Saved on this device. Syncing to your team…' : session.synced ? 'Synced to your team.' : 'Saved on this device. Waiting to sync.'}
      </p>
      <p className="odr-hint">Heatmaps show where the robots you identified were observed. Offense and defense are estimates from their movement during each shift. This run does not measure fuel scored or cycle times.</p>
      {result ? (
        <>
          <p className="odr-hint">Session {result.status} · quality {Math.round(result.quality_score * 100)}%{result.quality.eligible_for_review ? ' · ready for review' : ' · needs a closer look before it counts'} (at last sync)</p>
          {result.status === 'provisional' ? <p className="odr-hint">Awaiting operator review. This run does not count toward team scouting until accepted.</p> : null}
          {result.status === 'rejected' ? <p className="odr-error">This run was rejected at last sync and does not count toward team scouting.</p> : null}
          {result.reused_run ? <p className="odr-hint">The server confirmed the same saved run; no duplicate was created.</p> : null}
          {result.skipped_unknown_teams.length > 0 ? <p className="odr-error">Not uploaded: {result.skipped_unknown_teams.join(', ')}. Their saved positions remain on this device.</p> : null}
        </>
      ) : null}
      {!result?.shift_play || Object.keys(result.shift_play).length === 0 ? (
        <p className="odr-hint">{!session.synced ? 'Offense and defense estimates will appear after sync, when shift timing and enough track data are available.' : result?.shift_play_missing_reason ? shiftPlayMissingText(result.shift_play_missing_reason) : 'No saved offense or defense analysis for this run. The position heatmaps are still available.'}</p>
      ) : null}
      <div className="odr-robots">
        {teams.map(([teamKey, points]) => {
          const shift = result?.shift_play?.[teamKey];
          let start = Infinity;
          let end = -Infinity;
          for (const point of points) {
            if (Number.isFinite(point.timeSec)) { start = Math.min(start, point.timeSec); end = Math.max(end, point.timeSec); }
          }
          return (
            <div key={teamKey} className="odr-robot">
              <div className="odr-robot__head">
                <span className="odr-robot__team">{teamKey.replace(/^frc/i, 'Team ')}</span>
                {shift ? <span className={`odr-badge odr-badge--${shift.alliance}`}>{shift.alliance}</span> : null}
              </div>
              <p className="odr-hint">{points.length} saved positions{Number.isFinite(start) ? ` · observed ${clock(start)}–${clock(end)} on the match clock; gaps may exist` : ''}</p>
              <div>
                <p className="odr-heatmap__title">Observed positions</p>
                <FieldHeatmap data={positionHeatmap(points, teamKey)} />
              </div>
              {shift ? (
                <>
                  <div className="odr-meters">
                    <LevelMeter variant="offense" label="Offense" level={shift.offense.level_1_5} confidence={shift.offense.confidence_0_1} />
                    <LevelMeter variant="defense" label="Defense" level={shift.defense.level_1_5} confidence={shift.defense.confidence_0_1} assessable={shift.defense.assessable} />
                  </div>
                  {shift.heatmaps ? (
                    <div className="odr-heatmaps">
                      <div>
                        <p className="odr-heatmap__title">Attack (own shifts)</p>
                        {shift.heatmaps.attack.some((row) => row.some((value) => value > 0)) ? <FieldHeatmap data={rawGridToHeatmap(shift.heatmaps.attack, teamKey)} /> : <p className="odr-hint">No observed positions during own shifts.</p>}
                      </div>
                      <div>
                        <p className="odr-heatmap__title">Defense (opponent shifts)</p>
                        {shift.heatmaps.defense.some((row) => row.some((value) => value > 0)) ? <FieldHeatmap data={rawGridToHeatmap(shift.heatmaps.defense, teamKey)} /> : <p className="odr-hint">No observed positions during opponent shifts.</p>}
                      </div>
                    </div>
                  ) : null}
                </>
              ) : result?.shift_play ? <p className="odr-hint">Not enough data for offense or defense estimates for this robot.</p> : null}
            </div>
          );
        })}
      </div>
      {teams.length === 0 ? <p className="odr-hint">No robot positions were saved in this run.</p> : null}
    </section>
  );
}
