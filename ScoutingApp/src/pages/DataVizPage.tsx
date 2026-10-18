import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { plainSignalLabel, signalRankLabel } from './teamCenterOfficial';
import { Navigate, useLocation } from 'react-router-dom';
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
import { EVENTS_VIEWS, viewTitle } from '../components/pageViewBarConfig';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { Stat, Table, type TableColumn } from '../components/ui/primitives';

type RankingRow = EventTeamRatingItem;

const RANKING_COLUMNS: TableColumn<RankingRow>[] = [
  { key: 'rank', label: '#', numeric: true, render: (_row, index) => index + 1 },
  {
    key: 'team',
    label: 'Team',
    render: (row) => (
      <>
        <strong>#{row.team_number ?? row.team_key.replace(/^frc/, '')}</strong>
        {row.nickname ? <span className="dviz-nick"> {row.nickname}</span> : null}
      </>
    ),
  },
  {
    key: 'rating',
    label: 'Rating',
    numeric: true,
    render: (row) => <span className="dviz-rating-pill">{row.rating_0_100.toFixed(1)}</span>,
  },
  { key: 'confidence', label: 'Confidence', numeric: true, render: (row) => pct(row.confidence_0_1) },
  { key: 'throughput', label: 'Throughput', numeric: true, render: (row) => metric(row.subscores.throughput, 0) },
  { key: 'endgame', label: 'Endgame', numeric: true, render: (row) => metric(row.subscores.endgame, 0) },
  { key: 'auto', label: 'Auto', numeric: true, render: (row) => row.subscores.auto_contribution == null ? '—' : metric(row.subscores.auto_contribution, 0) },
  { key: 'consistency', label: 'Consistency', numeric: true, render: (row) => metric(row.subscores.consistency, 0) },
];
import { metric, pct, normalizeTeamKeyInput, TEAM_NUMBER_PLACEHOLDER } from './centerUtils';
import { readMyTeamKey } from '../features/workspace/myTeam';

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

// The shared center key, like every other page: a private one meant the event
// picked everywhere else didn't carry over here.
const STORAGE_KEY = 'scouting_center_event_key';

/* radar chart subscore keys & labels: the parts of the rating official results measure */
const SUBSCORE_KEYS = [
  'throughput',
  'auto_contribution',
  'endgame',
  'consistency',
] as const;

const SUBSCORE_LABELS: Record<string, string> = {
  throughput: 'Teleop',
  endgame: 'Climb',
  auto_contribution: 'Auto',
  consistency: 'Consistency',
};

/* metric display mapping: what the official per-match record holds (cycle time,
   defense and reliability had only ever come from broadcast video). */
const METRIC_KEYS = ['fuel_scoring_rate', 'auto_contribution', 'climb_success_prob'] as const satisfies readonly (keyof MetricAverages)[];
const METRIC_LABELS: Record<(typeof METRIC_KEYS)[number], string> = {
  fuel_scoring_rate: 'Fuel Rate (fuel/active min)',
  auto_contribution: 'Auto Contrib.',
  climb_success_prob: 'Climb %',
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
  scores: { key: string; label: string; value: number | null }[];
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
  const complete = scores.every((s) => s.value != null);
  const dataPath = scores
    .map((s, i) => {
      if (s.value == null) return '';
      const [x, y] = polarToXY(i, s.value);
      return `${i === 0 || scores[i - 1].value == null ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ') + (complete ? 'Z' : '');
  const first = scores[0].value;
  const last = scores[n - 1].value;
  const closingEdge = !complete && first != null && last != null
    ? `M${polarToXY(n - 1, last).join(',')} L${polarToXY(0, first).join(',')}`
    : '';

  return (
    <svg
      className="dviz-radar-svg"
      viewBox={`0 0 ${size} ${size}`}
      width={size}
      height={size}
      style={{ maxWidth: '100%', height: 'auto' }}
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
      {complete ? <polygon className="dviz-radar-shape" points={scores
        .map((s, i) => {
          const [x, y] = polarToXY(i, s.value!);
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(' ')} /> : null}
      <path className="dviz-radar-outline" d={`${dataPath} ${closingEdge}`} style={{ fill: 'none' }} />
      {/* data dots & labels */}
      {scores.map((s, i) => {
        const point = s.value == null ? null : polarToXY(i, s.value);
        const [lx, ly] = polarToXY(i, maxVal + 12);
        return (
          <g key={s.key}>
            {point ? <circle className="dviz-radar-dot" cx={point[0]} cy={point[1]} r={3} /> : null}
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
  suffix = '',
  width = 300,
  height = 80,
}: {
  values: (number | null)[];
  label: string;
  suffix?: string;
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
          {maxY.toFixed(1)}{suffix}
        </text>
        <text className="dviz-sparkline-axis" x={padX - 4} y={padY + drawH} textAnchor="end" dominantBaseline="auto">
          {minY.toFixed(1)}{suffix}
        </text>
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Page component                                                     */
/* ------------------------------------------------------------------ */

// Desktop-only page. The redirect lives out here so the page's hooks never
// run on a phone: rendered inline, useEventKeyParam wrote ?event= back onto
// this route in the same pass and cancelled the redirect, leaving a blank
// screen.
export function DataVizPage() {
  const isMobile = useMobileLayout();
  const { search } = useLocation();
  // A desktop link shared to a phone keeps its event.
  const event = new URLSearchParams(search).get('event');
  if (isMobile) return <Navigate to={event ? `/events?event=${encodeURIComponent(event)}` : '/events'} replace />;
  return <DataVizPageContent />;
}

function DataVizPageContent() {
  const { eventKey, fetchTrigger, eventInput, setEventInput, commitInput, selectEvent } = useEventKeyParam(STORAGE_KEY);
  const [teamInput, setTeamInput] = useState(() => readMyTeamKey().replace(/^frc/, ''));

  /* ── data state ─────────────────────────────── */
  const [ratings, setRatings] = useState<EventTeamRatingItem[]>([]);
  const [breakdown, setBreakdown] = useState<TeamBreakdownResponse | null>(null);
  const [loadingRatings, setLoadingRatings] = useState(false);
  const [loadingBreakdown, setLoadingBreakdown] = useState(false);
  const loading = loadingRatings || loadingBreakdown;
  const [error, setError] = useState<string | null>(null);
  const [activePanel, setActivePanel] = useState<'overview' | 'team'>('overview');

  /* resolved team key */
  const teamKey = useMemo(() => normalizeTeamKeyInput(teamInput), [teamInput]);

  const ratingsGeneration = useRef(0);
  const breakdownGeneration = useRef(0);

  const fieldAverageRating = useMemo(
    () => (ratings.length ? ratings.reduce((sum, row) => sum + row.rating_0_100, 0) / ratings.length : 0),
    [ratings],
  );

  /* ── fetch event ratings ────────────────────── */
  const loadRatings = useCallback(async () => {
    const generation = ++ratingsGeneration.current;
    setRatings([]);
    setLoadingRatings(Boolean(eventKey));
    if (!eventKey) return;
    setError(null);
    try {
      const resp = await getEventRatings(eventKey);
      if (generation !== ratingsGeneration.current) return;
      if (resp.ok) {
        setRatings(resp.ratings.sort((a, b) => b.rating_0_100 - a.rating_0_100));
      }
    } catch {
      if (generation === ratingsGeneration.current) setError('Could not load ratings. Try loading the event again.');
    } finally {
      if (generation === ratingsGeneration.current) setLoadingRatings(false);
    }
  }, [eventKey]);

  /* ── fetch team breakdown ───────────────────── */
  const loadBreakdown = useCallback(async () => {
    const generation = ++breakdownGeneration.current;
    setBreakdown(null);
    setLoadingBreakdown(Boolean(teamKey && eventKey));
    if (!teamKey || !eventKey) {
      setBreakdown(null);
      return;
    }
    setError(null);
    try {
      const resp = await getTeamBreakdown(teamKey, eventKey);
      if (generation === breakdownGeneration.current && resp.ok) setBreakdown(resp);
    } catch {
      if (generation === breakdownGeneration.current) setError('Could not load team data. Try loading the event again.');
    } finally {
      if (generation === breakdownGeneration.current) setLoadingBreakdown(false);
    }
  }, [teamKey, eventKey]);

  useEffect(() => {
    void loadRatings();
    return () => { ratingsGeneration.current += 1; };
  }, [loadRatings, fetchTrigger]);

  useEffect(() => {
    void loadBreakdown();
    return () => { breakdownGeneration.current += 1; };
  }, [loadBreakdown, fetchTrigger]);

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
      value: (teamRating.subscores as Record<string, number | null | undefined>)[key] ?? null,
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
      values: matches.map((m) => m[key] == null ? null : m[key]! * (key === 'climb_success_prob' ? 100 : 1)),
      suffix: key === 'climb_success_prob' ? '%' : '',
    }));
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


  /* ── render ─────────────────────────────────── */
  return (
    <>
    <PageViewBar items={EVENTS_VIEWS} />
    <div className="center-page-container dataviz-page">

      <SurfaceCard
        title={viewTitle(EVENTS_VIEWS, '/events/dashboard')}
        subtitle="Charts of how the teams at this event rate, and one team up close."
        expandable={false}
        mobileCollapsible={false}
      >
        <EventPicker
          label="Event"
          value={eventKey}
          onSelect={selectEvent}
          inputValue={eventInput}
          onInputChange={setEventInput}
          onSubmit={commitInput}
        />
        <label className="center-label" htmlFor="dviz-team">Team</label>
        <input
          id="dviz-team"
          className="center-input"
          type="text"
          inputMode="numeric"
          placeholder={TEAM_NUMBER_PLACEHOLDER}
          value={teamInput}
          onChange={(e) => setTeamInput(e.target.value)}
          style={{ maxWidth: 220 }}
        />
        <SegmentedTabs
          className="center-tabs"
          ariaLabel="Dashboard view"
          value={activePanel}
          onChange={(next) => setActivePanel(next)}
          items={[
            { value: 'overview', label: 'Event Overview' },
            { value: 'team', label: 'Team Deep Dive', disabled: !teamKey },
          ]}
        />
      </SurfaceCard>

      {loading && <div className="center-status-banner">Loading data…</div>}
      {error && <div className="center-status-banner center-error-banner">{error}</div>}


        {/* ── EVENT OVERVIEW PANEL ─────────────────── */}
        {activePanel === 'overview' && (
          <>
            {/* Rating Distribution */}
            <SurfaceCard
              title="Team Rating Distribution"
              subtitle={`Top ${topTeams.length} of ${ratings.length} team${ratings.length === 1 ? '' : 's'}`}
            >
              <div className="dviz-chart-container">
                {topTeams.length > 0 ? (
                  <HBarChart items={topTeams} maxVal={maxRating} width={500} />
                ) : (
                  <p className="dviz-empty">
                    {!eventKey ? 'Select an event to view ratings' : loadingRatings ? 'Loading ratings…' : 'No ratings for this event yet.'}
                  </p>
                )}
              </div>
            </SurfaceCard>

            {/* KPI Summary */}
            <SurfaceCard title="Event Summary">
              {/* The page's own question is "how strong is this field", and the
                  answer is the top rating read against the average. Four equal
                  boxes made you compute that yourself.

                  Rendered only once ratings exist. An em-dash at 36px is a
                  placeholder shouting, and guard-hierarchy is right to call
                  that a hero that is a label rather than a value. */}
              {ratings.length > 0 ? (
                <div className="page-hero">
                  <Stat
                    size="display"
                    label="Top rating"
                    value={ratings[0].rating_0_100.toFixed(1)}
                    sub={`${ratings[0].nickname || `Team ${ratings[0].team_number ?? ratings[0].team_key.replace(/^frc/, '')}`} · field average ${fieldAverageRating.toFixed(1)}`}
                    confidence={
                      ratings.reduce((sum, row) => sum + row.confidence_0_1, 0) / ratings.length
                    }
                  />
                  <div className="page-hero-stats">
                    <Stat size="sm" label="Teams rated" value={ratings.length} />
                    <Stat size="sm" label="Field average" value={fieldAverageRating.toFixed(1)} />
                  </div>
                </div>
              ) : (
                <p className="center-callout muted">
                  {!eventKey ? 'Load an event to see how its field compares.' : loadingRatings ? 'Loading ratings…' : 'Nothing to compare yet.'}
                </p>
              )}
            </SurfaceCard>

            {/* Full Rankings Table */}
            {ratings.length > 0 && (
              <SurfaceCard
                title="Full Rankings"
                subtitle={`All ${ratings.length} team${ratings.length === 1 ? '' : 's'}`}
                collapsible
              >
                <Table
                  columns={RANKING_COLUMNS}
                  rows={ratings}
                  rowKey={(row) => row.team_key}
                  rowClassName={(row) => row.team_key === teamKey && 'dviz-row-highlight'}
                />
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
                title={`Team ${teamRating.team_number ?? teamKey.replace(/^frc/, '')} - Subscore Profile`}
                subtitle={`Overall: ${teamRating.rating_0_100.toFixed(1)} / 100`}
              >
                <div className="dviz-radar-container">
                  <RadarChart scores={radarScores} />
                  <div className="dviz-radar-legend">
                    {radarScores.map((s) => (
                      <div key={s.key} className="dviz-radar-legend-item">
                        <span className="dviz-radar-legend-val">{s.value == null ? '—' : metric(s.value, 0)}</span>
                        <span className="dviz-radar-legend-label">{s.label}</span>
                      </div>
                    ))}
                  </div>
                </div>
                {radarScores.some((s) => s.value == null) ? <p className="dviz-empty">Missing scores are shown as gaps.</p> : null}
              </SurfaceCard>
            )}

            {/* Match-by-Match Trends */}
            {matchTrends.length > 0 && (
              <SurfaceCard
                title="Match-by-Match Trends"
                subtitle={`${breakdown?.recent_matches?.length ?? 0} match${breakdown?.recent_matches?.length === 1 ? '' : 'es'}`}
              >
                <div className="dviz-trends-grid">
                  {matchTrends.map((t) => (
                    <Sparkline key={t.key} values={t.values} label={t.label} suffix={t.suffix} width={300} />
                  ))}
                </div>
              </SurfaceCard>
            )}

            {/* Performance Metrics */}
            {breakdown?.averages && (
              <SurfaceCard
                title="Performance Averages"
                subtitle="Across official matches"
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
                collapsible
              >
                <div className="dviz-chart-container">
                  <HBarChart
                    items={eventTypeCounts}
                    maxVal={eventTypeCounts[0]?.value ?? 1}
                    width={500}
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
                          <span className="dviz-proscons-label">{plainSignalLabel(p.label)}</span>
                          <span className="dviz-proscons-val">
                            {p.rationale || signalRankLabel(p.percentile, 'strength')}
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
                          <span className="dviz-proscons-label">{plainSignalLabel(c.label)}</span>
                          <span className="dviz-proscons-val">
                            {c.rationale || signalRankLabel(c.percentile, 'risk')}
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

    </div>
    </>
  );
}
