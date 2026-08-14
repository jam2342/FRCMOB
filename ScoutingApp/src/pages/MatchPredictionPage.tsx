import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getEventScheduleWithSynergy,
  getEventPredictions,
  getEventSchedule,
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

/** Derive win probability from synergy scores with sigmoid-like logic. */
function derivedWinProb(
  redSynergy: number | null,
  blueSynergy: number | null,
): { redProb: number; blueProb: number } | null {
  if (redSynergy == null || blueSynergy == null) return null;
  const diff = redSynergy - blueSynergy;
  const scale = 15;
  const redProb = 1 / (1 + Math.exp(-diff / scale));
  return { redProb, blueProb: 1 - redProb };
}

type PredictionSource = 'ml_model' | 'synergy_derived';

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
  const fetchData = useCallback(async (key: string) => {
    if (!key) return;
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

        // Prefer ML model prediction when available, fall back to synergy-derived
        const mlPred = m.prediction;
        let redProb: number | null = null;
        let blueProb: number | null = null;
        let source: PredictionSource | null = null;
        let mlEdge: number | null = null;

        if (mlPred?.available && mlPred.red_win_prob != null) {
          redProb = mlPred.red_win_prob;
          blueProb = mlPred.blue_win_prob ?? (1 - mlPred.red_win_prob);
          source = 'ml_model';
          mlEdge = mlPred.edge_confidence_0_1 ?? null;
        } else {
          const derived = derivedWinProb(rSyn, bSyn);
          if (derived) {
            redProb = derived.redProb;
            blueProb = derived.blueProb;
            source = 'synergy_derived';
          }
        }

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
        <SurfaceCard
          title="Match Predictions"
          subtitle="Match outcome predictions."
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
                <strong>{synergyData.event_name || eventKey}</strong> — {predictions.length} matches,{' '}
                {synergyData.projection_count} synergy projections
                {synergyData.ml_prediction_count ? (
                  <span style={{ opacity: 0.7 }}>
                    {' '}· {synergyData.ml_prediction_count} ML predictions
                  </span>
                ) : null}
              </p>
            </div>
          ) : null}

          {errorText ? <p className="center-callout warning">{errorText}</p> : null}
        </SurfaceCard>

        {/* ---- Model Accuracy ---- */}
        {stats.completed > 0 ? (
          <SurfaceCard title="Prediction Accuracy" subtitle="Model accuracy.">
            <div className="center-kpi-grid">
              <div className={`center-kpi-card ${stats.accuracy != null && stats.accuracy >= 0.6 ? 'tone-green' : 'tone-yellow'}`}>
                <span>Accuracy</span>
                <strong>
                  {stats.accuracy != null ? `${(stats.accuracy * 100).toFixed(1)}%` : '—'}
                </strong>
              </div>
              <div className="center-kpi-card">
                <span>Correct</span>
                <strong>
                  {stats.correct} / {stats.completed}
                </strong>
              </div>
              <div className="center-kpi-card">
                <span>Total Matches</span>
                <strong>{stats.total}</strong>
              </div>
              <div className="center-kpi-card">
                <span>Completed</span>
                <strong>{stats.completed}</strong>
              </div>
              {stats.ml_powered > 0 ? (
                <div className="center-kpi-card tone-blue">
                  <span>ML Powered</span>
                  <strong>{stats.ml_powered}</strong>
                </div>
              ) : null}
            </div>
          </SurfaceCard>
        ) : null}

        {/* ---- Filter + Match List ---- */}
        {predictions.length > 0 ? (
          <SurfaceCard
            title="Match Predictions"
            subtitle={`${filteredPredictions.length} matches`}
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
            {/* Desktop table */}
            <div className="center-table-wrap desktop-only">
              <table className="center-table">
                <thead>
                  <tr>
                    <th>Match</th>
                    <th className="text-red">Red Alliance</th>
                    <th className="text-blue">Blue Alliance</th>
                    <th>Prediction</th>
                    <th>Synergy</th>
                    <th>Confidence</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPredictions.map((pred) => (
                    <PredictionRow
                      key={pred.match_key}
                      pred={pred}
                      isMobile={false}
                      onOpenMatch={openMatchCenter}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile card list */}
            <div className="prediction-mobile-list mobile-only">
              {filteredPredictions.map((pred) => (
                <MobilePredictionCard
                  key={`m-${pred.match_key}`}
                  pred={pred}
                  onOpenMatch={openMatchCenter}
                />
              ))}
            </div>
          </SurfaceCard>
        ) : null}

        {/* ---- TBA Predictions (reference) ---- */}
        {tbaPredictions ? (
          <SurfaceCard
            title="TBA Predictions"
            subtitle="External reference predictions from The Blue Alliance."
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

/* ------------------------------------------------------------------ */
/*  Prediction Row                                                     */
/* ------------------------------------------------------------------ */

function PredictionRow({
  pred,
  onOpenMatch,
}: {
  pred: MatchPrediction;
  isMobile: boolean;
  onOpenMatch: (matchKey: string) => void;
}) {
  const redAdv = (pred.red_prob ?? 0.5) > 0.5;
  const probStr =
    pred.red_prob != null
      ? redAdv
        ? `Red ${(pred.red_prob * 100).toFixed(0)}%`
        : `Blue ${((pred.blue_prob ?? 0.5) * 100).toFixed(0)}%`
      : '—';

  const wasCorrect =
    pred.is_completed && pred.winner && pred.winner !== 'tie' && pred.red_prob != null
      ? (redAdv && pred.winner === 'red') || (!redAdv && pred.winner === 'blue')
      : null;

  const sourceLabel = pred.prediction_source === 'ml_model' ? 'ML' : '';

  return (
    <tr
      className={
        wasCorrect === true
          ? 'prediction-row-correct'
          : wasCorrect === false
            ? 'prediction-row-wrong'
            : undefined
      }
    >
      <td>
        <button
          type="button"
          className="center-link-btn"
          onClick={() => onOpenMatch(pred.match_key)}
          style={{ fontWeight: 600, whiteSpace: 'nowrap' }}
        >
          {pred.display_name}
        </button>
      </td>
      <td className="text-sm">
        {pred.red_teams.map((t) => teamNum(t)).join(', ')}
      </td>
      <td className="text-sm">
        {pred.blue_teams.map((t) => teamNum(t)).join(', ')}
      </td>
      <td>
        <span className={`prediction-label ${redAdv ? 'text-red' : 'text-blue'}`}>
          {probStr}
        </span>
        {sourceLabel ? (
          <span className="text-muted" style={{ marginLeft: 4, fontSize: '0.75rem' }}>{sourceLabel}</span>
        ) : null}
      </td>
      <td className="text-sm" style={{ whiteSpace: 'nowrap' }}>
        <span className="text-red">
          {metric(pred.red_synergy, 0)}
        </span>
        {' vs '}
        <span className="text-blue">
          {metric(pred.blue_synergy, 0)}
        </span>
      </td>
      <td className="text-sm">
        {pct(Math.min(pred.red_confidence, pred.blue_confidence), 0)}
      </td>
      <td>
        {pred.is_completed ? (
          <span
            className={`prediction-result ${
              pred.winner === 'red'
                ? 'text-red'
                : pred.winner === 'blue'
                  ? 'text-blue'
                  : ''
            }`}
          >
            {pred.red_score}–{pred.blue_score}
            {wasCorrect === true ? ' (Y)' : wasCorrect === false ? ' (N)' : ''}
          </span>
        ) : (
          <span className="text-sm text-muted">Upcoming</span>
        )}
      </td>
    </tr>
  );
}

/* ------------------------------------------------------------------ */
/*  Mobile Prediction Card                                             */
/* ------------------------------------------------------------------ */

function MobilePredictionCard({
  pred,
  onOpenMatch,
}: {
  pred: MatchPrediction;
  onOpenMatch: (matchKey: string) => void;
}) {
  const redAdv = (pred.red_prob ?? 0.5) > 0.5;
  const probStr =
    pred.red_prob != null
      ? redAdv
        ? `Red ${(pred.red_prob * 100).toFixed(0)}%`
        : `Blue ${((pred.blue_prob ?? 0.5) * 100).toFixed(0)}%`
      : '—';

  const wasCorrect =
    pred.is_completed && pred.winner && pred.winner !== 'tie' && pred.red_prob != null
      ? (redAdv && pred.winner === 'red') || (!redAdv && pred.winner === 'blue')
      : null;

  const sourceLabel = pred.prediction_source === 'ml_model' ? 'ML' : '';

  const cardClass = [
    'prediction-mobile-card',
    wasCorrect === true ? 'correct' : wasCorrect === false ? 'wrong' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={cardClass}>
      <div className="prediction-mobile-head">
        <button
          type="button"
          className="center-link-btn"
          onClick={() => onOpenMatch(pred.match_key)}
          style={{ fontWeight: 600 }}
        >
          {pred.display_name}
        </button>
        <span className={`prediction-label ${redAdv ? 'text-red' : 'text-blue'}`} style={{ fontSize: '0.82rem' }}>
          {probStr}
          {sourceLabel ? (
            <span className="text-muted" style={{ marginLeft: 4, fontSize: '0.75rem' }}>{sourceLabel}</span>
          ) : null}
        </span>
      </div>

      <div className="prediction-mobile-alliances">
        <div className="prediction-mobile-alliance">
          <span className="prediction-mobile-alliance-label red">Red</span>
          <span className="prediction-mobile-alliance-teams">
            {pred.red_teams.map((t) => teamNum(t)).join(', ')}
          </span>
        </div>
        <div className="prediction-mobile-alliance">
          <span className="prediction-mobile-alliance-label blue">Blue</span>
          <span className="prediction-mobile-alliance-teams">
            {pred.blue_teams.map((t) => teamNum(t)).join(', ')}
          </span>
        </div>
      </div>

      <div className="prediction-mobile-stats">
        <span>
          Synergy
          <strong>
            <span className="text-red">{metric(pred.red_synergy, 0)}</span>
            {' – '}
            <span className="text-blue">{metric(pred.blue_synergy, 0)}</span>
          </strong>
        </span>
        <span>
          Confidence
          <strong>{pct(Math.min(pred.red_confidence, pred.blue_confidence), 0)}</strong>
        </span>
        <span>
          Result
          <strong>
            {pred.is_completed ? (
              <span
                className={
                  pred.winner === 'red' ? 'text-red' : pred.winner === 'blue' ? 'text-blue' : ''
                }
              >
                {pred.red_score}–{pred.blue_score}
                {wasCorrect === true ? ' (Y)' : wasCorrect === false ? ' (N)' : ''}
              </span>
            ) : (
              <span className="text-muted">—</span>
            )}
          </strong>
        </span>
      </div>
    </div>
  );
}
