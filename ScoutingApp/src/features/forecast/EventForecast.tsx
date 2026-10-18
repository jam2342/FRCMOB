import { useEffect, useState } from 'react';
import { getEventForecast, type EventForecastResponse } from '../../api';
import { EmptyState } from '../../components/ui/EmptyState';
import { SkeletonBlock } from '../../components/ui/SkeletonBlock';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Table } from '../../components/ui/primitives';
import { metric, pct, teamNumberFromTeamKey } from '../../pages/centerUtils';
import styles from './EventForecast.module.css';

type Props = {
  eventKey: string;
  onOpenTeam: (teamKey: string) => void;
};

function chance(value: number | undefined): string {
  if (value == null) return '—';
  if (value > 0 && value < 0.005) return '<1%';
  if (value < 1 && value > 0.995) return '>99%';
  return pct(value, 0);
}

// The rest of the event, simulated: where each team is likely to finish. Probabilities,
// not promises: the model doesn't know about breakdowns still to come or picks made
// on scouting.
export function EventForecast({ eventKey, onOpenTeam }: Props) {
  const [data, setData] = useState<EventForecastResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setData(null);
    setFailed(false);
    getEventForecast(eventKey)
      .then((result) => { if (live) setData(result); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [eventKey]);

  const runs = (data?.runs ?? 0).toLocaleString();
  const subtitle = !data
    ? 'The rest of the event, simulated'
    : data.unavailable === 'season_not_rated'
      ? 'No FRCMOB ratings for this season'
      : data.finished
        ? 'The event is over: how it finished'
        : (data.quals_remaining ?? 0) > 0
          ? `${data.quals_remaining} qualification matches left, played ${runs} times`
          : data.playoffs_started
            ? 'Qualifications done; playoff chances re-run selection from the final rankings'
            : `Qualifications done, playoffs played ${runs} times`;
  const teamLabel = (teamKey: string) => `#${teamNumberFromTeamKey(teamKey)}`;

  if (data?.unavailable === 'season_not_rated') {
    return (
      <SurfaceCard title="Forecast" subtitle={subtitle} compactable>
        <EmptyState compact title="No forecast for this season" description="FRCMOB ratings start in 2026, so earlier seasons have results but no forecasts." />
      </SurfaceCard>
    );
  }

  if (data?.finished) {
    return (
      <SurfaceCard title="Final result" subtitle={subtitle} compactable>
        {data.winners?.length ? (
          <p className={styles.result}>
            <strong>Winners</strong> {data.winners.map(teamLabel).join(' · ')}
            {data.finalists?.length ? <><br /><strong>Finalists</strong> {data.finalists.map(teamLabel).join(' · ')}</> : null}
          </p>
        ) : (
          <p className={styles.result}>No finals result on record for this event.</p>
        )}
        <Table
          columns={[
            { key: 'current_rank', label: 'Rank', numeric: true, width: '70px', render: (row) => row.current_rank ?? '—' },
            {
              key: 'team_key',
              label: 'Team',
              render: (row) => (
                <button type="button" className="center-inline-link" data-team-peek={row.team_key} data-team-peek-event={eventKey} onClick={() => onOpenTeam(row.team_key)}>
                  {teamLabel(row.team_key)}
                </button>
              ),
            },
            { key: 'ranking_score', label: 'Ranking score', numeric: true, render: (row) => metric(row.ranking_score ?? null, 2) },
            { key: 'matches_played', label: 'Quals', numeric: true, render: (row) => row.matches_played },
            { key: 'points_per_match', label: 'Pts / match', numeric: true, render: (row) => metric(row.points_per_match ?? null, 0) },
            { key: 'result', label: 'Playoffs', render: (row) => (row.won_event ? 'Won' : row.finalist ? 'Finalist' : '') },
          ]}
          rows={data.teams}
          rowKey={(row) => row.team_key}
          stickyHeader
          empty="No results yet."
          renderCards={(rows) => (
            <div className="fm-standings-card fm-standings-card--final">
              <div className="fm-standings-header">
                <span>#</span>
                <span>Team</span>
                <span>RS</span>
                <span>Playoffs</span>
              </div>
              {rows.map((row) => (
                <button key={`final-${row.team_key}`} type="button" className="fm-standings-row" onClick={() => onOpenTeam(row.team_key)}>
                  <span className="fm-standings-rank">{row.current_rank ?? '—'}</span>
                  <div className="fm-standings-team">
                    <strong>{teamLabel(row.team_key)}</strong>
                    <small>{metric(row.points_per_match ?? null, 0)} pts/match</small>
                  </div>
                  <span className="fm-standings-stat">{metric(row.ranking_score ?? null, 2)}</span>
                  <span className="fm-standings-pts">{row.won_event ? 'Won' : row.finalist ? 'Finalist' : ''}</span>
                </button>
              ))}
            </div>
          )}
        />
      </SurfaceCard>
    );
  }

  return (
    <SurfaceCard title="Forecast" subtitle={subtitle} compactable>
      {!data && !failed ? <SkeletonBlock rows={6} compact /> : null}
      {failed ? <EmptyState compact type="offline" title="Couldn't load the forecast" description="It retries when you come back to this tab." /> : null}
      {data && data.teams.length === 0 ? <EmptyState compact title="No teams yet" description="The forecast starts once the team list is out." /> : null}
      {data && data.teams.length > 0 && !data.simulated ? (
        <EmptyState compact title="No qualification matches here" description="This event is playoffs only, so there are no rankings to forecast." />
      ) : null}
      {data && data.teams.length > 0 && data.simulated ? (
        <Table
          columns={[
            { key: 'expected_rank', label: 'Exp. rank', numeric: true, sortable: true, width: '90px', render: (row) => metric(row.expected_rank ?? null, 1) },
            {
              key: 'team_key',
              label: 'Team',
              render: (row) => (
                <button type="button" className="center-inline-link" data-team-peek={row.team_key} data-team-peek-event={eventKey} onClick={() => onOpenTeam(row.team_key)}>
                  #{teamNumberFromTeamKey(row.team_key)}
                </button>
              ),
            },
            { key: 'current_rank', label: 'Now', numeric: true, sortable: true, width: '70px', render: (row) => row.current_rank ?? '—' },
            { key: 'top8', label: 'Top 8', numeric: true, sortable: true, render: (row) => chance(row.top8) },
            { key: 'captain', label: 'Captain', numeric: true, sortable: true, render: (row) => chance(row.captain) },
            { key: 'on_alliance', label: 'Playoffs', numeric: true, sortable: true, render: (row) => chance(row.on_alliance) },
            { key: 'win_event', label: 'Win', numeric: true, sortable: true, render: (row) => chance(row.win_event) },
            { key: 'points_per_match', label: 'Pts / match', numeric: true, sortable: true, render: (row) => metric(row.points_per_match ?? null, 0) },
          ]}
          rows={data.teams}
          rowKey={(row) => row.team_key}
          stickyHeader
          empty="No forecast yet."
          renderCards={(rows) => (
            <div className="fm-standings-card fm-standings-card--forecast">
              <div className="fm-standings-header">
                <span>Exp</span>
                <span>Team</span>
                <span>Top 8</span>
                <span>Playoffs</span>
                <span>Win</span>
                <span>Now</span>
              </div>
              {rows.map((row) => (
                <button key={`forecast-${row.team_key}`} type="button" className="fm-standings-row" onClick={() => onOpenTeam(row.team_key)}>
                  <span className="fm-standings-rank">{metric(row.expected_rank ?? null, 0)}</span>
                  <div className="fm-standings-team">
                    <strong>#{teamNumberFromTeamKey(row.team_key)}</strong>
                    <small>{metric(row.points_per_match ?? null, 0)} pts/match</small>
                  </div>
                  <span className="fm-standings-stat">{chance(row.top8)}</span>
                  <span className="fm-standings-stat">{chance(row.on_alliance)}</span>
                  <span className="fm-standings-pts">{chance(row.win_event)}</span>
                  <span className="fm-standings-stat">{row.current_rank ?? '—'}</span>
                </button>
              ))}
            </div>
          )}
        />
      ) : null}
    </SurfaceCard>
  );
}
