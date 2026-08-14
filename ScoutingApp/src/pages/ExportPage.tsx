import { useCallback, useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
  getEventRatings,
  getEventSchedule,
  getEventRankings,
  exportEventScoutingEntries,
  listPitEntries,
} from '../api';
import type {
  EventTeamRatingItem,
  EventScheduleItem,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { EVENTS_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { downloadCsv } from '../utils/csvExport';
import { metric, pct } from './centerUtils';

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'scouting_center_event_key';

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function ExportPage() {
  const isMobile = useMobileLayout();

  const { eventKey, eventInput, setEventInput, commitInput, selectEvent, fetchTrigger } = useEventKeyParam(STORAGE_KEY);

  /* Data state */
  const [ratings, setRatings] = useState<EventTeamRatingItem[] | null>(null);
  const [schedule, setSchedule] = useState<EventScheduleItem[] | null>(null);
  const [rankings, setRankings] = useState<Array<Record<string, unknown>> | null>(null);
  const [eventName, setEventName] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [statusText, setStatusText] = useState('');

  /* Fetch all data */
  const fetchData = useCallback(async (key: string) => {
    if (!key) return;
    setLoading(true);
    setErrorText('');
    setStatusText('');
    setRatings(null);
    setSchedule(null);
    setRankings(null);
    setEventName('');

    const results = await Promise.allSettled([
      getEventRatings(key),
      getEventSchedule(key),
      getEventRankings(key),
    ]);

    if (results[0].status === 'fulfilled') {
      setRatings(results[0].value.ratings ?? []);
      setEventName(results[0].value.event_name || key);
    }

    if (results[1].status === 'fulfilled') {
      const scheduleResult = results[1].value;
      setSchedule(scheduleResult.matches ?? []);
      setEventName((current) => current || scheduleResult.event_name || key);
    }

    if (results[2].status === 'fulfilled') {
      const payload = results[2].value as Record<string, unknown>;
      const rankRows = Array.isArray(payload.rankings) ? payload.rankings as Record<string, unknown>[] : [];
      setRankings(rankRows);
    }

    const failedCount = results.filter((r) => r.status === 'rejected').length;
    if (failedCount === results.length) {
      setErrorText('Failed to load event data. Check the event key.');
    } else if (failedCount > 0) {
      setStatusText(`${results.length - failedCount} of ${results.length} data sources loaded.`);
    } else {
      setStatusText('All data loaded and ready for export.');
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    if (isMobile || !eventKey) return;
    const timer = window.setTimeout(() => {
      void fetchData(eventKey);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [eventKey, fetchData, fetchTrigger, isMobile]);

  /* ---- Export functions ---- */

  function exportRatings() {
    if (!ratings?.length) return;
    const headers = [
      'Team', 'Number', 'Nickname', 'Rating', 'Confidence',
      'Robot Level', 'Driver Skill',
      'Results Anchor', 'Throughput', 'Shift Productivity',
      'Capacity Utilization', 'Endgame', 'Auto Contribution',
      'Manual Points', 'RP Contribution', 'Defense', 'Consistency', 'Penalty Discipline',
      'Pros', 'Cons', 'Model Version', 'Updated At',
    ];
    const rows = ratings.map((r) => [
      r.team_key,
      r.team_number,
      r.nickname || '',
      r.rating_0_100,
      r.confidence_0_1,
      r.robot_level_0_100,
      r.driver_skill_0_100,
      r.subscores?.results_anchor,
      r.subscores?.throughput,
      r.subscores?.shift_productivity,
      r.subscores?.capacity_utilization,
      r.subscores?.endgame,
      r.subscores?.auto_contribution,
      r.subscores?.manual_points_impact,
      r.subscores?.rp_contribution,
      r.subscores?.defense_presence,
      r.subscores?.consistency,
      r.subscores?.penalty_discipline,
      (r.pros || []).map((p) => p.label || '').filter(Boolean).join('; '),
      (r.cons || []).map((c) => c.label || '').filter(Boolean).join('; '),
      r.model_version,
      r.updated_at || '',
    ]);
    downloadCsv(`${eventKey}_team_ratings.csv`, headers, rows);
    setStatusText(`Exported ${rows.length} team ratings.`);
  }

  function exportSchedule() {
    if (!schedule?.length) return;
    const headers = [
      'Match Key', 'Display Name', 'Comp Level', 'Set', 'Match',
      'Red 1', 'Red 2', 'Red 3', 'Blue 1', 'Blue 2', 'Blue 3',
      'Red Score', 'Blue Score', 'Winner', 'Completed',
    ];
    const rows = schedule.map((m) => [
      m.match_key,
      m.display_name || '',
      m.comp_level,
      m.set_number,
      m.match_number,
      m.red?.[0]?.team_key || '',
      m.red?.[1]?.team_key || '',
      m.red?.[2]?.team_key || '',
      m.blue?.[0]?.team_key || '',
      m.blue?.[1]?.team_key || '',
      m.blue?.[2]?.team_key || '',
      m.red_score ?? '',
      m.blue_score ?? '',
      m.winner_alliance || '',
      m.is_completed ? 'Yes' : 'No',
    ]);
    downloadCsv(`${eventKey}_match_schedule.csv`, headers, rows);
    setStatusText(`Exported ${rows.length} matches.`);
  }

  function exportRankings() {
    if (!rankings?.length) return;
    // TBA rankings format: each row has team_key, rank, record, etc.
    const firstRow = rankings[0];
    const headers = Object.keys(firstRow).filter(
      (k) => typeof firstRow[k] !== 'object' || firstRow[k] == null,
    );
    // Handle nested record
    const record = firstRow.record as Record<string, unknown> | undefined;
    if (record) {
      for (const k of Object.keys(record)) {
        headers.push(`record.${k}`);
      }
    }
    const rows = rankings.map((row) => {
      const values: unknown[] = [];
      for (const h of headers) {
        if (h.startsWith('record.')) {
          const subKey = h.slice(7);
          const rec = row.record as Record<string, unknown> | undefined;
          values.push(rec?.[subKey] ?? '');
        } else {
          const v = row[h];
          values.push(v != null && typeof v !== 'object' ? v : '');
        }
      }
      return values;
    });
    downloadCsv(`${eventKey}_rankings.csv`, headers, rows);
    setStatusText(`Exported ${rows.length} rankings.`);
  }

  async function exportRawScoutingEntries() {
    if (!eventKey) return;
    setStatusText('Fetching scouting entries…');
    try {
      const result = await exportEventScoutingEntries(eventKey);
      const entries = result.entries ?? [];
      if (entries.length === 0) {
        setStatusText('No scouting entries found for this event.');
        return;
      }
      // Union of all form field keys across entries so every metric gets a column.
      const formKeys = new Set<string>();
      const pointKeys = new Set<string>();
      for (const item of entries) {
        const form = item.entry?.form;
        if (form && typeof form === 'object' && !Array.isArray(form)) {
          for (const key of Object.keys(form as Record<string, unknown>)) formKeys.add(key);
        }
        const points = item.entry?.points;
        if (points && typeof points === 'object' && !Array.isArray(points)) {
          for (const key of Object.keys(points as Record<string, unknown>)) pointKeys.add(key);
        }
      }
      const sortedFormKeys = Array.from(formKeys).sort();
      const sortedPointKeys = Array.from(pointKeys).sort();
      const headers = [
        'room_key', 'scout_profile', 'event_key', 'match_key', 'team_key',
        'created_at', 'entry_source', 'notes',
        'total_points', 'driver_score_0_100', 'manual_rating_0_100',
        'scouting_api_rating_0_100', 'overall_scout_rating_0_100',
        ...sortedPointKeys.map((key) => `points_${key}`),
        ...sortedFormKeys.map((key) => `form_${key}`),
      ];
      const rows = entries.map((item) => {
        const entry = item.entry ?? {};
        const form = (entry.form && typeof entry.form === 'object' && !Array.isArray(entry.form)
          ? entry.form
          : {}) as Record<string, unknown>;
        const points = (entry.points && typeof entry.points === 'object' && !Array.isArray(entry.points)
          ? entry.points
          : {}) as Record<string, unknown>;
        return [
          item.room_key,
          item.scout_profile,
          item.event_key ?? '',
          item.match_key ?? '',
          item.team_key ?? '',
          item.created_at ?? '',
          (entry.entry_source as string) ?? 'manual',
          (entry.notes as string) ?? '',
          item.scores?.total_points ?? '',
          item.scores?.driver_score_0_100 ?? '',
          item.scores?.manual_rating_0_100 ?? '',
          item.scores?.scouting_api_rating_0_100 ?? '',
          item.scores?.overall_scout_rating_0_100 ?? '',
          ...sortedPointKeys.map((key) => points[key] ?? ''),
          ...sortedFormKeys.map((key) => form[key] ?? ''),
        ];
      });
      downloadCsv(`${eventKey}_scouting_entries.csv`, headers, rows);
      setStatusText(`Exported ${rows.length} raw scouting entries.`);
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : 'Failed to export scouting entries.');
    }
  }

  async function exportPitScoutingCsv() {
    if (!eventKey) return;
    setStatusText('Fetching pit scouting data…');
    try {
      const result = await listPitEntries(eventKey);
      const entries = result.entries ?? [];
      if (entries.length === 0) {
        setStatusText('No pit scouting entries found for this event.');
        return;
      }
      const payloadKeys = new Set<string>();
      for (const item of entries) {
        for (const key of Object.keys(item.payload ?? {})) payloadKeys.add(key);
      }
      const sortedKeys = Array.from(payloadKeys).sort();
      const headers = ['team_key', 'scout_profile', 'updated_at', 'photo_count', ...sortedKeys];
      const rows = entries.map((item) => [
        item.team_key,
        item.scout_profile ?? '',
        item.updated_at ?? '',
        item.photos?.length ?? 0,
        ...sortedKeys.map((key) => {
          const value = item.payload?.[key];
          return Array.isArray(value) ? value.join('; ') : value ?? '';
        }),
      ]);
      downloadCsv(`${eventKey}_pit_scouting.csv`, headers, rows);
      setStatusText(`Exported ${rows.length} pit scouting entries.`);
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : 'Failed to export pit scouting data.');
    }
  }

  const surfaceGroupId = 'data-export';
  const hasAnyData = (ratings?.length ?? 0) > 0 || (schedule?.length ?? 0) > 0 || (rankings?.length ?? 0) > 0;

  if (isMobile) return <Navigate to="/events" replace />;

  return (
    <>
    <PageViewBar items={EVENTS_VIEWS} />
    <div className="center-page-container narrow">
      <SurfaceCardGroup groupId={surfaceGroupId}>
        {/* ---- Event Selection ---- */}
        <SurfaceCard
          title="Data Export"
          subtitle="Export event data to CSV."
        >
          <EventPicker
            value={eventKey}
            onSelect={selectEvent}
            inputValue={eventInput}
            onInputChange={setEventInput}
            onSubmit={commitInput}
            loading={loading}
          />

          {eventName ? (
            <p className="center-event-status">
              <strong>{eventName}</strong>
            </p>
          ) : null}

          {errorText ? <p className="center-callout warning">{errorText}</p> : null}
          {statusText ? <p className="center-success-text">{statusText}</p> : null}
        </SurfaceCard>

        {/* ---- Export Actions ---- */}
        {hasAnyData ? (
          <SurfaceCard
            title="Available Exports"
            subtitle="Download CSV files."
          >
            <div className="export-card-grid">
              {/* Team Ratings */}
              <ExportCard
                title="Team Ratings"
                description={`${ratings?.length ?? 0} teams with rating data, subscores, and scouting signals.`}
                available={Boolean(ratings?.length)}
                onExport={exportRatings}
                icon="[#]"
              />

              {/* Match Schedule */}
              <ExportCard
                title="Match Schedule"
                description={`${schedule?.length ?? 0} matches with scores, alliances, and results.`}
                available={Boolean(schedule?.length)}
                onExport={exportSchedule}
                icon="[L]"
              />

              {/* Rankings */}
              <ExportCard
                title="Rankings"
                description={`${rankings?.length ?? 0} team rankings from TBA / FIRST.`}
                available={Boolean(rankings?.length)}
                onExport={exportRankings}
                icon="[W]"
              />

              {/* Raw scouting entries */}
              <ExportCard
                title="Scouting Entries (raw)"
                description="Every scouting entry across all rooms, with all form metrics flattened into columns."
                available={Boolean(eventKey)}
                onExport={() => { void exportRawScoutingEntries(); }}
                icon="[S]"
              />

              {/* Pit scouting */}
              <ExportCard
                title="Pit Scouting"
                description="Pit form answers per team (drivetrain, capabilities, photos count)."
                available={Boolean(eventKey)}
                onExport={() => { void exportPitScoutingCsv(); }}
                icon="[P]"
              />
            </div>
          </SurfaceCard>
        ) : null}

        {/* ---- Ratings Preview ---- */}
        {ratings && ratings.length > 0 ? (
          <SurfaceCard
            title="Ratings Preview"
            subtitle={`Top teams by rating at ${eventName || eventKey}.`}
            collapsible
          >
            <div className="center-table-wrap">
              <table className="center-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Team</th>
                    <th>Rating</th>
                    <th>Confidence</th>
                    <th>Robot</th>
                    <th>Driver</th>
                    {!isMobile ? <th>Top Strength</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {ratings.slice(0, 20).map((r, idx) => (
                    <tr key={r.team_key}>
                      <td className="text-muted">{idx + 1}</td>
                      <td style={{ fontWeight: 600 }}>
                        #{r.team_number} {r.nickname || ''}
                      </td>
                      <td style={{ fontWeight: 600 }}>{metric(r.rating_0_100, 1)}</td>
                      <td>{pct(r.confidence_0_1, 0)}</td>
                      <td>{metric(r.robot_level_0_100, 0)}</td>
                      <td>{metric(r.driver_skill_0_100, 0)}</td>
                      {!isMobile ? (
                        <td className="text-sm text-muted">
                          {r.pros?.[0]?.label || '—'}
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </SurfaceCard>
        ) : null}
      </SurfaceCardGroup>
    </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Export Card sub-component                                           */
/* ------------------------------------------------------------------ */

function ExportCard({
  title,
  description,
  available,
  onExport,
  icon,
}: {
  title: string;
  description: string;
  available: boolean;
  onExport: () => void;
  icon: string;
}) {
  return (
    <div className={`export-card${available ? '' : ' unavailable'}`}>
      <div className="export-card-head">
        <span className="export-card-icon">{icon}</span>
        <strong>{title}</strong>
      </div>
      <p>{description}</p>
      <button
        type="button"
        className="center-btn"
        onClick={onExport}
        disabled={!available}
      >
        Download CSV
      </button>
    </div>
  );
}
