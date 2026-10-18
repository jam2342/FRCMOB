import { useEffect, useState } from 'react';
import { getPickValues, type PickValuesResponse } from '../../api';
import { EmptyState } from '../../components/ui/EmptyState';
import { SkeletonBlock } from '../../components/ui/SkeletonBlock';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Table } from '../../components/ui/primitives';
import { metric, pct, teamNumberFromTeamKey } from '../../pages/centerUtils';

type Props = {
  eventKey: string;
  // The alliance so far: captain first, then the first pick if chosen.
  partners: string[];
  onOpenTeam: (teamKey: string) => void;
};

// Who raises this alliance's chance to beat a strong one here the most. A robot that
// breaks down now and then counts for less than its average says.
export function BestNextPick({ eventKey, partners, onOpenTeam }: Props) {
  const [data, setData] = useState<PickValuesResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const key = partners.join(',');

  useEffect(() => {
    if (!partners.length) return;
    let live = true;
    setData(null);
    setFailed(false);
    getPickValues(eventKey, partners, 12)
      .then((result) => { if (live) setData(result); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
    // `key` stands for `partners` (a new array every render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventKey, key]);

  const who = partners.map((t) => `#${teamNumberFromTeamKey(t)}`).join(' + ');
  const subtitle = partners.length
    ? `With ${who}: chance to beat a strong alliance here${data?.assumed_third ? ` (third robot: a typical one, #${teamNumberFromTeamKey(data.assumed_third)})` : ''}`
    : 'Pick a captain above to see who fits best.';

  return (
    <SurfaceCard title={partners.length > 1 ? 'Best Third Pick' : 'Best Next Pick'} subtitle={subtitle}>
      {!partners.length ? <EmptyState compact title="No captain yet" description="Choose the captain (and first pick) in the builder." /> : null}
      {partners.length && !data && !failed ? <SkeletonBlock rows={5} compact /> : null}
      {failed ? <EmptyState compact type="offline" title="Couldn't load pick values" description="Try again in a moment." /> : null}
      {data?.unavailable ? (
        <EmptyState
          compact
          title="No pick values for a past season"
          description={data.unavailable === 'season_not_rated'
            ? 'FRCMOB ratings start in 2026, so earlier seasons have no pick values.'
            : 'This season is finished and kept as it ended; pick values are for the current season.'}
        />
      ) : null}
      {data && data.candidates.length ? (
        <Table
          columns={[
            {
              key: 'team_key',
              label: 'Team',
              render: (row) => (
                <button type="button" className="center-inline-link" data-team-peek={row.team_key} data-team-peek-event={eventKey} onClick={() => onOpenTeam(row.team_key)}>
                  #{teamNumberFromTeamKey(row.team_key)}
                </button>
              ),
            },
            { key: 'win_vs_strong_alliance', label: 'Win chance', numeric: true, render: (row) => pct(row.win_vs_strong_alliance, 0) },
            { key: 'points_per_match', label: 'Pts / match', numeric: true, render: (row) => metric(row.points_per_match, 0) },
            { key: 'rating_0_100', label: 'Rating', numeric: true, render: (row) => metric(row.rating_0_100, 1) },
            { key: 'climb_points', label: 'Climb pts', numeric: true, render: (row) => metric(row.climb_points, 1) },
          ]}
          rows={data.candidates}
          rowKey={(row) => row.team_key}
          empty="No candidates left."
        />
      ) : null}
    </SurfaceCard>
  );
}
