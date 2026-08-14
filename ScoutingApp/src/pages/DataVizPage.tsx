import { useCallback, useEffect, useMemo, useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
  getEventRatings,
  getTeamBreakdown,
} from '../api';
import type {
  EventTeamRatingItem,
  TeamBreakdownResponse,
  MetricAverages,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { EVENTS_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { metric, pct, normalizeTeamKeyInput } from './centerUtils';

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'dataviz_event_key';
const MY_TEAM_STORAGE = 'scouting_manual_my_team_v1';

/* radar chart subscore keys & labels */
const SUBSCORE_KEYS = [
  'throughput',
  'shift_productivity',
  'capacity_utilization',
  'endgame',
  'auto_contribution',
  'consistency',
  'defense_presence',
  'rp_contribution',
  'penalty_discipline',
  'results_anchor',
] as const;

const SUBSCORE_LABELS: Record<string, string> = {
  throughput: 'Throughput',
  shift_productivity: 'Shift Prod.',
  capacity_utilization: 'Capacity',
  endgame: 'Endgame',
  auto_contribution: 'Auto',
  consistency: 'Consistency',
  defense_presence: 'Defense',
  rp_contribution: 'RP Contrib.',
  penalty_discipline: 'Penalties',
  results_anchor: 'Results',
};

/* metric display mapping */
const METRIC_LABELS: Record<keyof MetricAverages, string> = {
  fuel_scoring_rate: 'Fuel Rate',
  cycle_time_sec: 'Cycle Time',
  auto_contribution: 'Auto Contrib.',
  climb_success_prob: 'Climb %',
  defensive_engagement_sec: 'Defense Time',
  reliability_score: 'Reliability',
};

const METRIC_KEYS = Object.keys(METRIC_LABELS) as (keyof MetricAverages)[];

/* zone color map */
const ZONE_COLORS: Record<string, string> = {
  red_alliance_scoring_zone: '#ef4444',
  red_loading_depot_zone: '#f97316',
  red_tower_endgame_zone: '#a855f7',
  neutral_transition_zone: '#6b7280',
  blue_alliance_scoring_zone: '#3b82f6',
  blue_loading_depot_zone: '#06b6d4',
  blue_tower_endgame_zone: '#8b5cf6',
};

const ZONE_LABELS: Record<string, string> = {
  red_alliance_scoring_zone: 'Red Scoring',
  red_loading_depot_zone: 'Red Loading',
  red_tower_endgame_zone: 'Red Tower',
  neutral_transition_zone: 'Neutral',
  blue_alliance_scoring_zone: 'Blue Scoring',
  blue_loading_depot_zone: 'Blue Loading',
  blue_tower_endgame_zone: 'Blue Tower',
};

/* ------------------------------------------------------------------ */
/*  SVG chart helpers                                                   */
/* ------------------------------------------------------------------ */

/** Radar / spider chart – pure SVG */
function RadarChart({
  scores,
  maxVal = 100,
  size = 260,
}: {
  scores: { key: string; label: string; value: number }[];
  maxVal?: number;
  size?: number;
}) {
  const n = scores.length;
  if (n < 3) return null;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 30;
  const angleStep = (2 * Math.PI) / n;

  function polarToXY(i: number, val: number): [number, number] {
    const angle = -Math.PI / 2 + i * angleStep;
    const ratio = Math.min(val / maxVal, 1);
    return [cx + r * ratio * Math.cos(angle), cy + r * ratio * Math.sin(angle)];
  }

  const rings = [0.25, 0.5, 0.75, 1.0];
  const dataPath = scores
    .map((s, i) => {
      const [x, y] = polarToXY(i, s.value);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ') + 'Z';

  return (
    <svg
      className="dviz-radar-svg"
      viewBox={`0 0 ${size} ${size}`}
      width={size}
      height={size}
    >
      {/* grid rings */}
      {rings.map((frac) => (
        <polygon
          key={frac}
          className="dviz-radar-ring"
          points={scores
            .map((_, i) => {
              const [x, y] = polarToXY(i, frac * maxVal);
              return `${x.toFixed(1)},${y.toFixed(1)}`;
            })
            .join(' ')}
        />
      ))}
      {/* axes */}
      {scores.map((_, i) => {
        const [x, y] = polarToXY(i, maxVal);
        return (
          <line
            key={i}
            className="dviz-radar-axis"
            x1={cx}
            y1={cy}
            x2={x}
            y2={y}
          />
        );
      })}
      {/* data shape */}
      <polygon className="dviz-radar-shape" points={scores
        .map((s, i) => {
          const [x, y] = polarToXY(i, s.value);
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(' ')} />
      <path className="dviz-radar-outline" d={dataPath} />
      {/* data dots & labels */}
      {scores.map((s, i) => {
        const [x, y] = polarToXY(i, s.value);
        const [lx, ly] = polarToXY(i, maxVal + 12);
        return (
          <g key={s.key}>
            <circle className="dviz-radar-dot" cx={x} cy={y} r={3} />
            <text
              className="dviz-radar-label"
              x={lx}
              y={ly}
              textAnchor="middle"
              dominantBaseline="middle"
            >
              {s.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Horizontal bar chart – pure SVG */
function HBarChart({
  items,
  maxVal,
  height: barH = 22,
  width = 400,
}: {
  items: { label: string; value: number; color?: string }[];
  maxVal: number;
  height?: number;
  width?: number;
}) {
  const rowH = barH + 6;
  const labelW = 60;
  const valW = 40;
  const barW = width - labelW - valW - 12;
  const svgH = items.length * rowH + 4;

  return (
    <svg className="dviz-hbar-svg" viewBox={`0 0 ${width} ${svgH}`} width="100%" preserveAspectRatio="xMinYMin meet">
      {items.map((item, i) => {
        const ratio = maxVal > 0 ? Math.min(item.value / maxVal, 1) : 0;
        const y = i * rowH + 2;
        return (
          <g key={item.label + i}>
            <text
              className="dviz-hbar-label"
              x={labelW - 4}
              y={y + barH / 2}
              textAnchor="end"
              dominantBaseline="middle"
            >
              {item.label}
            </text>
            <rect
              className="dviz-hbar-bg"
              x={labelW}
              y={y}
              width={barW}
              height={barH}
              rx={4}
            />
            <rect
              className="dviz-hbar-fill"
              x={labelW}
              y={y}
              width={Math.max(barW * ratio, 2)}
              height={barH}
              rx={4}
              fill={item.color || 'var(--dviz-accent)'}
            />
            <text
              className="dviz-hbar-value"
              x={labelW + barW + 4}
              y={y + barH / 2}
              dominantBaseline="middle"
            >
              {item.value.toFixed(1)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Sparkline for match-over-match trends */
function Sparkline({
  values,
  label,
  width = 300,
  height = 80,
}: {
  values: (number | null)[];
  label: string;
  width?: number;
  height?: number;
}) {
  const valid = values.map((v, i) => (v != null ? { x: i, y: v } : null)).filter(Boolean) as {
    x: number;
    y: number;
  }[];
  if (valid.length < 2) return <div className="dviz-sparkline-empty">Not enough data</div>;

  const minY = Math.min(...valid.map((v) => v.y));
  const maxY = Math.max(...valid.map((v) => v.y));
  const rangeY = maxY - minY || 1;
  const padX = 24;
  const padY = 14;
  const drawW = width - padX * 2;
  const drawH = height - padY * 2;
  const maxX = values.length - 1 || 1;

  function toSvg(pt: { x: number; y: number }): string {
    const sx = padX + (pt.x / maxX) * drawW;
    const sy = padY + drawH - ((pt.y - minY) / rangeY) * drawH;
    return `${sx.toFixed(1)},${sy.toFixed(1)}`;
  }

  const polyline = valid.map(toSvg).join(' ');

  return (
    <div className="dviz-sparkline-wrap">
      <span className="dviz-sparkline-label">{label}</span>
      <svg className="dviz-sparkline-svg" viewBox={`0 0 ${width} ${height}`} width="100%" preserveAspectRatio="xMidYMid meet">
        <polyline className="dviz-sparkline-line" points={polyline} />
        {valid.map((pt, i) => {
          const [x, y] = toSvg(pt).split(',').map(Number);
          return <circle key={i} className="dviz-sparkline-dot" cx={x} cy={y} r={2.5} />;
        })}
        {/* y axis labels */}
        <text className="dviz-sparkline-axis" x={padX - 4} y={padY} textAnchor="end" dominantBaseline="hanging">
          {maxY.toFixed(1)}
        </text>
        <text className="dviz-sparkline-axis" x={padX - 4} y={padY + drawH} textAnchor="end" dominantBaseline="auto">
          {minY.toFixed(1)}
        </text>
      </svg>
    </div>
  );
}

/** Donut chart for zone time breakdown */
function DonutChart({
  slices,
  size = 180,
}: {
  slices: { key: string; label: string; value: number; color: string }[];
  size?: number;
}) {
  const total = slices.reduce((s, sl) => s + sl.value, 0);
  if (total <= 0) return null;
  const cx = size / 2;
  const cy = size / 2;
  const rOuter = size / 2 - 8;
  const rInner = rOuter * 0.55;

  function arcPath(startAngle: number, endAngle: number, outer: number, inner: number): string {
    const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
    const x1 = cx + outer * Math.cos(startAngle);
    const y1 = cy + outer * Math.sin(startAngle);
    const x2 = cx + outer * Math.cos(endAngle);
    const y2 = cy + outer * Math.sin(endAngle);
    const x3 = cx + inner * Math.cos(endAngle);
    const y3 = cy + inner * Math.sin(endAngle);
    const x4 = cx + inner * Math.cos(startAngle);
    const y4 = cy + inner * Math.sin(startAngle);
    return [
      `M${x1.toFixed(2)},${y1.toFixed(2)}`,
      `A${outer},${outer} 0 ${largeArc} 1 ${x2.toFixed(2)},${y2.toFixed(2)}`,
      `L${x3.toFixed(2)},${y3.toFixed(2)}`,
      `A${inner},${inner} 0 ${largeArc} 0 ${x4.toFixed(2)},${y4.toFixed(2)}`,
      'Z',
    ].join(' ');
  }

  const sliceArcs = slices.reduce<Array<{ key: string; label: string; value: number; color: string; startAngle: number; angle: number }>>(
    (entries, sl) => {
      const previous = entries[entries.length - 1];
      const startAngle = previous ? previous.startAngle + previous.angle : -Math.PI / 2;
      const angle = (sl.value / total) * 2 * Math.PI;
      if (angle < 0.01) return entries;
      return [
        ...entries,
        {
          ...sl,
          startAngle,
          angle,
        },
      ];
    },
    [],
  );

  return (
    <svg className="dviz-donut-svg" viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
      {sliceArcs.map((sl) => {
        return (
          <path
            key={sl.key}
            d={arcPath(sl.startAngle, sl.startAngle + sl.angle, rOuter, rInner)}
            fill={sl.color}
            className="dviz-donut-slice"
          >
            <title>{sl.label}: {sl.value.toFixed(1)}s ({((sl.value / total) * 100).toFixed(0)}%)</title>
          </path>
        );
      })}
      <text className="dviz-donut-center" x={cx} y={cy} textAnchor="middle" dominantBaseline="middle">
        {total.toFixed(0)}s
      </text>
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Page component                                                     */
/* ------------------------------------------------------------------ */

export function DataVizPage() {
  const isMobile = useMobileLayout();
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent } = useEventKeyParam(STORAGE_KEY);
  const [teamInput, setTeamInput] = useState(() => {
    try {
      const saved = localStorage.getItem(MY_TEAM_STORAGE);
      return saved ? JSON.parse(saved) : '';
    } catch {
      return '';
    }
  });

  /* ── data state ─────────────────────────────── */
  const [ratings, setRatings] = useState<EventTeamRatingItem[]>([]);
  const [breakdown, setBreakdown] = useState<TeamBreakdownResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activePanel, setActivePanel] = useState<'overview' | 'team'>('overview');

  /* resolved team key */
  const teamKey = useMemo(() => normalizeTeamKeyInput(teamInput), [teamInput]);

  /* ── fetch event ratings ────────────────────── */
  const loadRatings = useCallback(async () => {
    if (!eventKey) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await getEventRatings(eventKey);
      if (resp.ok) {
        setRatings(resp.ratings.sort((a, b) => b.rating_0_100 - a.rating_0_100));
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load ratings');
    } finally {
      setLoading(false);
    }
  }, [eventKey]);

  /* ── fetch team breakdown ───────────────────── */
  const loadBreakdown = useCallback(async () => {
    if (!teamKey || !eventKey) {
      setBreakdown(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await getTeamBreakdown(teamKey, eventKey);
      if (resp.ok) setBreakdown(resp);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load breakdown');
    } finally {
      setLoading(false);
    }
  }, [teamKey, eventKey]);

  useEffect(() => {
    if (isMobile) return;
    void loadRatings();
  }, [isMobile, loadRatings]);

  useEffect(() => {
    if (isMobile) return;
    void loadBreakdown();
  }, [isMobile, loadBreakdown]);

  /* ── derived data ───────────────────────────── */
  /* team rating from event ratings */
  const teamRating = useMemo(
    () => (teamKey ? ratings.find((r) => r.team_key === teamKey) : null),
    [ratings, teamKey],
  );

  /* subscore data for radar chart */
  const radarScores = useMemo(() => {
    if (!teamRating) return [];
    return SUBSCORE_KEYS.map((key) => ({
      key,
      label: SUBSCORE_LABELS[key] || key,
      value: (teamRating.subscores as Record<string, number | null | undefined>)[key] ?? 0,
    }));
  }, [teamRating]);

  /* top N teams bar chart data */
  const topTeams = useMemo(() => {
    const top = ratings.slice(0, 15);
    return top.map((r) => ({
      label: String(r.team_number ?? r.team_key),
      value: r.rating_0_100,
      color: r.team_key === teamKey ? 'var(--dviz-highlight)' : undefined,
    }));
  }, [ratings, teamKey]);

  /* match trends (sparklines) */
  const matchTrends = useMemo(() => {
    if (!breakdown?.recent_matches?.length) return [];
    const matches = [...breakdown.recent_matches].sort((a, b) => {
      const ta = a.match_time ?? 0;
      const tb = b.match_time ?? 0;
      return ta - tb;
    });
    return METRIC_KEYS.map((key) => ({
      key,
      label: METRIC_LABELS[key],
      values: matches.map((m) => m[key] ?? null),
    }));
  }, [breakdown]);

  /* zone time donut */
  const zoneSlices = useMemo(() => {
    if (!breakdown?.zone_time_sec) return [];
    return Object.entries(breakdown.zone_time_sec)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => ({
        key: k,
        label: ZONE_LABELS[k] || k,
        value: v,
        color: ZONE_COLORS[k] || '#6b7280',
      }))
      .sort((a, b) => b.value - a.value);
  }, [breakdown]);

  /* event type breakdown */
  const eventTypeCounts = useMemo(() => {
    if (!breakdown?.event_type_counts?.length) return [];
    const sorted = [...breakdown.event_type_counts].sort((a, b) => b.count - a.count);
    return sorted.slice(0, 10).map((e) => ({
      label: e.event_type.replace(/_/g, ' '),
      value: e.count,
      color: undefined as string | undefined,
    }));
  }, [breakdown]);

  const maxRating = useMemo(() => {
    if (!ratings.length) return 100;
    return Math.max(...ratings.map((r) => r.rating_0_100), 100);
  }, [ratings]);

  const surfaceGroupId = 'dataviz-main';

  if (isMobile) return <Navigate to="/events" replace />;

  /* ── render ─────────────────────────────────── */
  return (
    <div className="center-page-container dataviz-page">
      <PageViewBar items={EVENTS_VIEWS} />

      <div className="center-page-header">
        <h1 className="center-page-title">Data Dashboard</h1>
        <p className="center-page-subtitle">
          Visual analytics for event and team data
        </p>
      </div>

      {/* controls */}
      <EventPicker
        value={eventKey}
        onSelect={selectEvent}
        inputValue={eventInput}
        onInputChange={setEventInput}
        onSubmit={commitInput}
      />

      <div className="center-input-row" style={{ marginTop: '0.5rem' }}>
        <input
          id="dviz-team"
          className="center-input"
          type="text"
          inputMode="numeric"
          placeholder="Team number — e.g. 254"
          value={teamInput}
          onChange={(e) => setTeamInput(e.target.value)}
          style={{ maxWidth: 220 }}
        />
      </div>

      {/* panel toggle */}
      <div className="dviz-panel-toggle">
        <button
          className={`dviz-toggle-btn ${activePanel === 'overview' ? 'active' : ''}`}
          onClick={() => setActivePanel('overview')}
        >
          Event Overview
        </button>
        <button
          className={`dviz-toggle-btn ${activePanel === 'team' ? 'active' : ''}`}
          onClick={() => setActivePanel('team')}
          disabled={!teamKey}
        >
          Team Deep Dive
        </button>
      </div>

      {loading && <div className="center-status-banner">Loading data...</div>}
      {error && <div className="center-status-banner center-error-banner">{error}</div>}

      <SurfaceCardGroup groupId={surfaceGroupId}>
        {/* ── EVENT OVERVIEW PANEL ─────────────────── */}
        {activePanel === 'overview' && (
          <>
            {/* Rating Distribution */}
            <SurfaceCard
              title="Team Rating Distribution"
              subtitle={`Top ${topTeams.length} of ${ratings.length} teams`}
            >
              <div className="dviz-chart-container">
                {topTeams.length > 0 ? (
                  <HBarChart items={topTeams} maxVal={maxRating} width={isMobile ? 340 : 500} />
                ) : (
                  <p className="dviz-empty">Select an event to view ratings</p>
                )}
              </div>
            </SurfaceCard>

            {/* KPI Summary */}
            <SurfaceCard
              title="Event Summary"
              subtitle={`${ratings.length} teams`}
            >
              <div className="center-kpi-grid dviz-kpi-grid">
                <div className="center-kpi-card">
                  <span className="center-kpi-value">{ratings.length}</span>
                  <span className="center-kpi-label">Teams</span>
                </div>
                <div className="center-kpi-card">
                  <span className="center-kpi-value">
                    {ratings.length > 0 ? ratings[0].rating_0_100.toFixed(1) : '--'}
                  </span>
                  <span className="center-kpi-label">Top Rating</span>
                </div>
                <div className="center-kpi-card">
                  <span className="center-kpi-value">
                    {ratings.length > 0
                      ? (ratings.reduce((s, r) => s + r.rating_0_100, 0) / ratings.length).toFixed(1)
                      : '--'}
                  </span>
                  <span className="center-kpi-label">Avg Rating</span>
                </div>
                <div className="center-kpi-card">
                  <span className="center-kpi-value">
                    {ratings.length > 0
                      ? (ratings.reduce((s, r) => s + r.confidence_0_1, 0) / ratings.length * 100).toFixed(0) + '%'
                      : '--'}
                  </span>
                  <span className="center-kpi-label">Avg Confidence</span>
                </div>
              </div>
            </SurfaceCard>

            {/* Full Rankings Table */}
            {ratings.length > 0 && (
              <SurfaceCard
                title="Full Rankings"
                subtitle={`All ${ratings.length} teams`}
                collapsible
              >
                <div className={isMobile ? 'dviz-rankings-mobile' : 'dviz-rankings-table-wrap'}>
                  {isMobile ? (
                    ratings.map((r, i) => (
                      <div
                        key={r.team_key}
                        className={`dviz-rank-card ${r.team_key === teamKey ? 'dviz-rank-highlight' : ''}`}
                      >
                        <span className="dviz-rank-pos">#{i + 1}</span>
                        <span className="dviz-rank-team">{r.team_number ?? r.team_key}</span>
                        <span className="dviz-rank-name">{r.nickname || ''}</span>
                        <span className="dviz-rank-rating">{r.rating_0_100.toFixed(1)}</span>
                        <div className="dviz-rank-bar-bg">
                          <div
                            className="dviz-rank-bar-fill"
                            style={{ width: `${(r.rating_0_100 / maxRating) * 100}%` }}
                          />
                        </div>
                      </div>
                    ))
                  ) : (
                    <table className="dviz-rankings-table">
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>Team</th>
                          <th>Rating</th>
                          <th>Confidence</th>
                          <th>Throughput</th>
                          <th>Endgame</th>
                          <th>Auto</th>
                          <th>Consistency</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ratings.map((r, i) => (
                          <tr
                            key={r.team_key}
                            className={r.team_key === teamKey ? 'dviz-row-highlight' : ''}
                          >
                            <td>{i + 1}</td>
                            <td>
                              <strong>{r.team_number ?? r.team_key}</strong>
                              {r.nickname ? <span className="dviz-nick"> {r.nickname}</span> : null}
                            </td>
                            <td>
                              <span className="dviz-rating-pill">{r.rating_0_100.toFixed(1)}</span>
                            </td>
                            <td>{pct(r.confidence_0_1)}</td>
                            <td>{r.subscores.throughput.toFixed(0)}</td>
                            <td>{r.subscores.endgame.toFixed(0)}</td>
                            <td>{(r.subscores.auto_contribution ?? 0).toFixed(0)}</td>
                            <td>{r.subscores.consistency.toFixed(0)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </SurfaceCard>
            )}
          </>
        )}

        {/* ── TEAM DEEP DIVE PANEL ─────────────────── */}
        {activePanel === 'team' && teamKey && (
          <>
            {/* Radar Chart */}
            {teamRating && (
              <SurfaceCard
                title={`Team ${teamRating.team_number ?? teamKey} - Subscore Profile`}
                subtitle={`Overall: ${teamRating.rating_0_100.toFixed(1)} / 100`}
              >
                <div className="dviz-radar-container">
                  <RadarChart scores={radarScores} />
                  <div className="dviz-radar-legend">
                    {radarScores.map((s) => (
                      <div key={s.key} className="dviz-radar-legend-item">
                        <span className="dviz-radar-legend-val">{s.value.toFixed(0)}</span>
                        <span className="dviz-radar-legend-label">{s.label}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </SurfaceCard>
            )}

            {/* Match-by-Match Trends */}
            {matchTrends.length > 0 && (
              <SurfaceCard
                title="Match-by-Match Trends"
                subtitle={`${breakdown?.recent_matches?.length ?? 0} matches`}
              >
                <div className={`dviz-trends-grid ${isMobile ? 'dviz-trends-mobile' : ''}`}>
                  {matchTrends.map((t) => (
                    <Sparkline key={t.key} values={t.values} label={t.label} width={isMobile ? 280 : 300} />
                  ))}
                </div>
              </SurfaceCard>
            )}

            {/* Zone Time Breakdown */}
            {zoneSlices.length > 0 && (
              <SurfaceCard
                title="Zone Time Distribution"
                subtitle="Average seconds per zone"
              >
                <div className="dviz-zone-container">
                  <DonutChart slices={zoneSlices} size={isMobile ? 160 : 200} />
                  <div className="dviz-zone-legend">
                    {zoneSlices.map((sl) => (
                      <div key={sl.key} className="dviz-zone-legend-item">
                        <span className="dviz-zone-swatch" style={{ background: sl.color }} />
                        <span className="dviz-zone-legend-label">{sl.label}</span>
                        <span className="dviz-zone-legend-val">{sl.value.toFixed(1)}s</span>
                      </div>
                    ))}
                  </div>
                </div>
              </SurfaceCard>
            )}

            {/* Performance Metrics */}
            {breakdown?.averages && (
              <SurfaceCard
                title="Performance Averages"
                subtitle="Across analyzed matches"
              >
                <div className="center-kpi-grid dviz-kpi-grid">
                  {METRIC_KEYS.map((key) => {
                    const val = breakdown.averages?.[key];
                    const isPercent = key === 'climb_success_prob';
                    return (
                      <div key={key} className="center-kpi-card">
                        <span className="center-kpi-value">
                          {val != null ? (isPercent ? pct(val) : metric(val)) : '--'}
                        </span>
                        <span className="center-kpi-label">{METRIC_LABELS[key]}</span>
                      </div>
                    );
                  })}
                </div>
              </SurfaceCard>
            )}

            {/* Event Type Breakdown */}
            {eventTypeCounts.length > 0 && (
              <SurfaceCard
                title="Event Type Breakdown"
                subtitle="Detected event counts"
                collapsible
              >
                <div className="dviz-chart-container">
                  <HBarChart
                    items={eventTypeCounts}
                    maxVal={eventTypeCounts[0]?.value ?? 1}
                    width={isMobile ? 340 : 500}
                  />
                </div>
              </SurfaceCard>
            )}

            {/* Strengths & Weaknesses */}
            {teamRating && (teamRating.pros.length > 0 || teamRating.cons.length > 0) && (
              <SurfaceCard
                title="Strengths & Weaknesses"
              >
                <div className="dviz-proscons">
                  {teamRating.pros.length > 0 && (
                    <div className="dviz-proscons-col">
                      <h4 className="dviz-proscons-heading dviz-pro-heading">Strengths</h4>
                      {teamRating.pros.map((p, i) => (
                        <div key={i} className="dviz-proscons-item dviz-pro-item">
                          <span className="dviz-proscons-label">{p.label}</span>
                          <span className="dviz-proscons-val">
                            {p.metric_value.toFixed(1)} (P{(p.percentile * 100).toFixed(0)})
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  {teamRating.cons.length > 0 && (
                    <div className="dviz-proscons-col">
                      <h4 className="dviz-proscons-heading dviz-con-heading">Weaknesses</h4>
                      {teamRating.cons.map((c, i) => (
                        <div key={i} className="dviz-proscons-item dviz-con-item">
                          <span className="dviz-proscons-label">{c.label}</span>
                          <span className="dviz-proscons-val">
                            {c.metric_value.toFixed(1)} (P{(c.percentile * 100).toFixed(0)})
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </SurfaceCard>
            )}
          </>
        )}
      </SurfaceCardGroup>
    </div>
  );
}
