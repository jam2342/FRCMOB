import { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getEventScheduleWithSynergy,
  getEventPredictions,
  getEventSchedule,
  isClientAdminModeEnabled,
} from '../api';
import type {
  EventScheduleWithSynergyResponse,
  EventScheduleItem,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { MATCH_HUB_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { buildMatchCenterPath, metric, pct, teamNumberFromTeamKey } from './centerUtils';
import { MOBILE_LAYOUT_BREAKPOINT } from '../hooks/useMobileLayout';
import { Stat, Table, type TableColumn } from '../components/ui/primitives';
import styles from './MatchPredictionPage.module.css';
import { matchWinProbability, type WinProbabilitySource } from '../features/predictions/winProbability';

/* ------------------------------------------------------------------ */
/*  Constants & helpers                                                */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'scouting_center_event_key';

const COMP_LEVEL_ORDER: Record<string, number> = { qm: 0, ef: 1, qf: 2, sf: 3, f: 4 };

function matchSortKey(m: { comp_level: string; set_number: number; match_number: number }): number {
  const level = COMP_LEVEL_ORDER[m.comp_level] ?? 9;
  return level * 1_000_000 + m.set_number * 1_000 + m.match_number;
}

function matchDisplayName(m: { comp_level: string; set_number: number; match_number: number }): string {
  const level = m.comp_level.toUpperCase();
  if (m.comp_level === 'qm') return `Qual ${m.match_number}`;
  if (m.comp_level === 'f') return `Final ${m.match_number}`;
  return `${level} ${m.set_number}-${m.match_number}`;
}

function teamNum(teamKey: string): string {
  const n = teamNumberFromTeamKey(teamKey);
  return n ? String(n) : teamKey;
}

/* The favoured alliance and its probability. Both the table row and the phone
   card derived this — identically, twenty lines apart — which is precisely how
   a formatting change lands in one view and not the other. */
function favouredAlliance(pred: MatchPrediction): { red: boolean; label: string } {
  const red = (pred.red_prob ?? 0.5) > 0.5;
  if (pred.red_prob == null) return { red, label: '—' };
  return {
    red,
    label: red
      ? `Red ${(pred.red_prob * 100).toFixed(0)}%`
      : `Blue ${((pred.blue_prob ?? 0.5) * 100).toFixed(0)}%`,
  };
}

/* null when there is nothing to score yet — an upcoming match, a tie, or a
   prediction we never made. Only true/false mark the row. */
function wasPredictionCorrect(pred: MatchPrediction): boolean | null {
  if (!pred.is_completed || !pred.winner || pred.winner === 'tie' || pred.red_prob == null) return null;
  const { red } = favouredAlliance(pred);
  return (red && pred.winner === 'red') || (!red && pred.winner === 'blue');
}

// "(Y)"/"(N)" after the score asked the reader to guess what was being
// answered. Say it, with a mark so the row can be scanned.
function ResultVerdict({ correct }: { correct: boolean }) {
  return (
    <span className={correct ? styles.verdictRight : styles.verdictWrong}>
      {correct ? ' ✓ Called it' : ' ✗ Missed'}
    </span>
  );
}

// Phones: one short card per match. The table's stacked fallback gave every
// match six labelled lines (~230px), so an event's 141 matches ran to 40,000px.
// Synergy and edge stay on the wider table; the call and the result are what
// someone checking from the stands wants.
function renderPredictionCards(rows: MatchPrediction[], onOpenMatch: (matchKey: string) => void) {
  return (
    <div className={styles.cards}>
      {rows.map((pred) => {
        const favoured = favouredAlliance(pred);
        const correct = wasPredictionCorrect(pred);
        const cardTone = correct === true ? styles.rowCorrect : correct === false ? styles.rowWrong : '';
        return (
          <button
            key={pred.match_key}
            type="button"
            className={`${styles.card} ${cardTone}`.trim()}
            onClick={() => onOpenMatch(pred.match_key)}
          >
            <span className={styles.cardHead}>
              <strong>{pred.display_name}</strong>
              <span className={`prediction-label ${favoured.red ? 'text-red' : 'text-blue'}`}>{favoured.label}</span>
            </span>
            <span className={styles.cardLine}>
              <span className="text-red">{pred.red_teams.map((team) => teamNum(team)).join(' · ')}</span>
              <strong className="text-red">{pred.is_completed ? pred.red_score : ''}</strong>
            </span>
            <span className={styles.cardLine}>
              <span className="text-blue">{pred.blue_teams.map((team) => teamNum(team)).join(' · ')}</span>
              <strong className="text-blue">{pred.is_completed ? pred.blue_score : ''}</strong>
            </span>
            <span className={styles.cardFoot}>
              {!pred.is_completed ? 'Upcoming' : correct !== null ? <ResultVerdict correct={correct} /> : 'Tie'}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function predictionColumns(onOpenMatch: (matchKey: string) => void): TableColumn<MatchPrediction>[] {
  return [
  {
    key: 'match',
    label: 'Match',
    render: (pred) => (
      <button type="button" className={`center-link-btn ${styles.matchLink}`} onClick={() => onOpenMatch(pred.match_key)}>
        {pred.display_name}
      </button>
    ),
  },
  {
    key: 'red',
    label: 'Red Alliance',
    render: (pred) => pred.red_teams.map((team) => teamNum(team)).join(', '),
  },
  {
    key: 'blue',
    label: 'Blue Alliance',
    render: (pred) => pred.blue_teams.map((team) => teamNum(team)).join(', '),
  },
  {
    key: 'prediction',
    label: 'Prediction',
    render: (pred) => {
      const favoured = favouredAlliance(pred);
      return (
        <>
          <span className={`prediction-label ${favoured.red ? 'text-red' : 'text-blue'}`}>{favoured.label}</span>
          {pred.prediction_source === 'ml_model' ? <span className={styles.source}>ML</span> : null}
        </>
      );
    },
  },
  {
    key: 'synergy',
    label: 'Synergy',
    render: (pred) => (
      <span className={styles.nowrap}>
        <span className="text-red">{metric(pred.red_synergy, 0)}</span>
        {' vs '}
        <span className="text-blue">{metric(pred.blue_synergy, 0)}</span>
      </span>
    ),
  },
  {
    key: 'confidence',
    label: 'Edge',
    numeric: true,
    // How far the call is from a coin flip. This showed the synergy projection's
    // confidence, which is 0 for every match, so the column always read 0%.
    render: (pred) => (pred.red_prob == null ? '—' : pct(Math.abs(pred.red_prob - 0.5) * 2, 0)),
  },
  {
    key: 'result',
    label: 'Result',
    render: (pred) => {
      if (!pred.is_completed) return <span className="text-muted">Upcoming</span>;
      const correct = wasPredictionCorrect(pred);
      const tone = pred.winner === 'red' ? 'text-red' : pred.winner === 'blue' ? 'text-blue' : '';
      return (
        <>
        <span className={`prediction-result ${tone}`.trim()}>
          {pred.red_score}–{pred.blue_score}
        </span>
        {correct !== null ? <ResultVerdict correct={correct} /> : null}
        </>
      );
    },
  },
  ];
}

type PredictionSource = WinProbabilitySource;

type MatchPrediction = {
  match_key: string;
  display_name: string;
  comp_level: string;
  set_number: number;
  match_number: number;
  scheduled_time: number | null;
  red_teams: string[];
  blue_teams: string[];
  red_synergy: number | null;
  blue_synergy: number | null;
  red_synergy_points: number;
  blue_synergy_points: number;
  red_confidence: number;
  blue_confidence: number;
  red_throughput: number | null;
  blue_throughput: number | null;
  red_prob: number | null;
  blue_prob: number | null;
  prediction_source: PredictionSource | null;
  ml_edge_confidence: number | null;
  // Actual results (from schedule)
  red_score: number | null;
  blue_score: number | null;
  winner: 'red' | 'blue' | 'tie' | null;
  is_completed: boolean;
};

type FilterMode = 'all' | 'upcoming' | 'completed';

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function MatchPredictionPage() {
  const navigate = useNavigate();

  const { eventKey, eventInput, setEventInput, commitInput, selectEvent, fetchTrigger } = useEventKeyParam(STORAGE_KEY);
  const [synergyData, setSynergyData] = useState<EventScheduleWithSynergyResponse | null>(null);
  const [scheduleData, setScheduleData] = useState<EventScheduleItem[] | null>(null);
  const [tbaPredictions, setTbaPredictions] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');

  /* --- Fetch data --- */
  // Only the latest request may write: switching events on a slow connection let
  // the old event's late response replace the new one's predictions.
  const requestGeneration = useRef(0);
  const fetchData = useCallback(async (key: string) => {
    if (!key) return;
    const generation = ++requestGeneration.current;
    setLoading(true);
    setErrorText('');
    setSynergyData(null);
    setScheduleData(null);
    setTbaPredictions(null);

    const results = await Promise.allSettled([
      getEventScheduleWithSynergy(key, { include_pair_breakdown: false }),
      getEventSchedule(key),
      getEventPredictions(key),
    ]);
    if (generation !== requestGeneration.current) return;

    if (results[0].status === 'fulfilled') {
      setSynergyData(results[0].value);
    } else {
      setErrorText((results[0].reason as Error)?.message || 'Failed to load synergy data.');
    }

    if (results[1].status === 'fulfilled') {
      setScheduleData(results[1].value.matches);
    }

    if (results[2].status === 'fulfilled') {
      setTbaPredictions(results[2].value.predictions);
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    if (!eventKey) return;
    const timer = window.setTimeout(() => {
      void fetchData(eventKey);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [eventKey, fetchData, fetchTrigger]);

  /* --- Merge synergy + schedule into predictions --- */
  const predictions = useMemo<MatchPrediction[]>(() => {
    if (!synergyData?.matches) return [];

    const scheduleMap = new Map<string, EventScheduleItem>();
    for (const m of scheduleData ?? []) {
      scheduleMap.set(m.match_key, m);
    }

    return synergyData.matches
      .map((m): MatchPrediction => {
        const sched = scheduleMap.get(m.match_key);
        const rSyn = m.red.synergy.alliance_synergy_score_0_100;
        const bSyn = m.blue.synergy.alliance_synergy_score_0_100;

        const win = matchWinProbability(m);
        const redProb = win?.red ?? null;
        const blueProb = win?.blue ?? null;
        const source: PredictionSource | null = win?.source ?? null;
        const mlEdge = win?.edgeConfidence ?? null;

        return {
          match_key: m.match_key,
          display_name: matchDisplayName(m),
          comp_level: m.comp_level,
          set_number: m.set_number,
          match_number: m.match_number,
          scheduled_time: m.scheduled_time,
          red_teams: m.red.teams.map((t) => t.team_key),
          blue_teams: m.blue.teams.map((t) => t.team_key),
          red_synergy: rSyn,
          blue_synergy: bSyn,
          red_synergy_points: m.red.synergy.alliance_synergy_points,
          blue_synergy_points: m.blue.synergy.alliance_synergy_points,
          red_confidence: m.red.synergy.confidence_0_1,
          blue_confidence: m.blue.synergy.confidence_0_1,
          red_throughput: m.red.synergy.projected_throughput ?? m.red.synergy.expected_throughput,
          blue_throughput: m.blue.synergy.projected_throughput ?? m.blue.synergy.expected_throughput,
          red_prob: redProb,
          blue_prob: blueProb,
          prediction_source: source,
          ml_edge_confidence: mlEdge,
          red_score: sched?.red_score ?? null,
          blue_score: sched?.blue_score ?? null,
          winner: sched?.winner_alliance ?? null,
          is_completed: sched?.is_completed ?? false,
        };
      })
      .sort((a, b) => matchSortKey(a) - matchSortKey(b));
  }, [synergyData, scheduleData]);

  const filteredPredictions = useMemo(() => {
    if (filterMode === 'upcoming') return predictions.filter((p) => !p.is_completed);
    if (filterMode === 'completed') return predictions.filter((p) => p.is_completed);
    return predictions;
  }, [predictions, filterMode]);

  /* --- Stats --- */
  const stats = useMemo(() => {
    const completed = predictions.filter((p) => p.is_completed && p.red_prob != null && p.winner);
    let correct = 0;
    for (const p of completed) {
      const predictedRed = (p.red_prob ?? 0.5) > 0.5;
      const actualRed = p.winner === 'red';
      if (predictedRed === actualRed && p.winner !== 'tie') correct++;
    }
    const nonTie = completed.filter((p) => p.winner !== 'tie');
    const mlCount = predictions.filter((p) => p.prediction_source === 'ml_model').length;
    return {
      total: predictions.length,
      completed: completed.length,
      // Fuel-rate predictions use only results from before each match: real forecasts.
      forecast: completed.filter((p) => p.prediction_source === 'fuel_model').length,
      correct,
      accuracy: nonTie.length > 0 ? correct / nonTie.length : null,
      ml_powered: mlCount,
    };
  }, [predictions]);

  function openMatchCenter(matchKey: string) {
    navigate(buildMatchCenterPath('', matchKey));
  }

  const surfaceGroupId = 'match-predictions';

  return (
    <>
    <PageViewBar items={MATCH_HUB_VIEWS} />
    <div className="center-page-container">
      <SurfaceCardGroup groupId={surfaceGroupId}>
        {/* ---- Event Selection ---- */}
        {/* Named for what it holds. Both this card and the list below it were
            titled "Match Predictions", so the page had two identically named
            cards and two identical "Open Match Predictions fullscreen"
            buttons. This one is a picker. */}
        <SurfaceCard
          title="Event Finder"
        >
          <EventPicker
            value={eventKey}
            onSelect={selectEvent}
            inputValue={eventInput}
            onInputChange={setEventInput}
            onSubmit={commitInput}
            loading={loading}
          />

          {synergyData ? (
            <div>
              <p className="center-event-status">
                <strong>{synergyData.event_name || eventKey}</strong> — {predictions.length} match{predictions.length === 1 ? '' : 'es'}
              </p>
            </div>
          ) : null}

          {errorText ? <p className="center-callout warning">{errorText}</p> : null}
        </SurfaceCard>

        {/* ---- Model Accuracy ---- */}
        {stats.completed > 0 ? (
          <SurfaceCard
            title={stats.forecast > 0 ? 'Forecast check' : 'Hindsight check'}
            subtitle={
              stats.forecast === stats.completed
                ? 'Each match was predicted only from results before it, so this is how well matches were forecast.'
                : stats.forecast > 0
                  ? `${stats.forecast} of ${stats.completed} played matches were predicted from results before them. The rest use today's ratings, which already include their results.`
                  : "Today's ratings already include these results, so this measures fit, not how well matches were forecast."
            }
          >
            <div className="page-hero">
              <Stat
                size="display"
                label="Agrees with result"
                value={stats.accuracy != null ? `${(stats.accuracy * 100).toFixed(1)}%` : '—'}
                sub={`${stats.correct} of ${stats.completed} completed matches`}
                tone={
                  stats.accuracy == null
                    ? 'default'
                    : stats.accuracy >= 0.6
                      ? 'success'
                      : 'warning'
                }
              />
              <div className="page-hero-stats">
                <Stat size="sm" label="Matches" value={stats.total} sub={`${stats.completed} played`} />
                {stats.ml_powered > 0 ? (
                  <Stat size="sm" label="ML powered" value={stats.ml_powered} />
                ) : null}
              </div>
            </div>
          </SurfaceCard>
        ) : null}

        {/* ---- Filter + Match List ---- */}
        {predictions.length > 0 ? (
          <SurfaceCard
            title="Match Predictions"
            subtitle={`${filteredPredictions.length} match${filteredPredictions.length === 1 ? '' : 'es'}`}
            right={
              <div className="center-filter-chips">
                {(['all', 'upcoming', 'completed'] as FilterMode[]).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    className={`center-chip clickable ${filterMode === mode ? 'active' : ''}`}
                    onClick={() => setFilterMode(mode)}
                  >
                    {mode.charAt(0).toUpperCase() + mode.slice(1)}
                  </button>
                ))}
              </div>
            }
          >
            <Table
              columns={predictionColumns(openMatchCenter)}
              rows={filteredPredictions}
              rowKey={(pred) => pred.match_key}
              cardBreakpoint={MOBILE_LAYOUT_BREAKPOINT}
              renderCards={(narrowRows) => renderPredictionCards(narrowRows, openMatchCenter)}
              rowClassName={(pred) => {
                const correct = wasPredictionCorrect(pred);
                if (correct === null) return undefined;
                return correct ? styles.rowCorrect : styles.rowWrong;
              }}
            />
          </SurfaceCard>
        ) : null}

        {/* ---- TBA Predictions (reference) ---- */}
        {/* Raw JSON: useful when checking our numbers against TBA's, but it
            is a debugging view, so only admins see it. */}
        {tbaPredictions && isClientAdminModeEnabled() ? (
          <SurfaceCard
            title="TBA Predictions"
            collapsible
          >
            <pre className="center-pre-block" style={{ maxHeight: 320, overflow: 'auto', fontSize: '0.75rem' }}>
              {JSON.stringify(tbaPredictions, null, 2)}
            </pre>
          </SurfaceCard>
        ) : null}
      </SurfaceCardGroup>
    </div>
    </>
  );
}
