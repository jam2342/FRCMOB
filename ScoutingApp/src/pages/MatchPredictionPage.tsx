import { matchLabel } from '../utils/matchLabel';
import { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getEventPredictionSchedule,
  getEventPredictions,
  getEventSchedule,
  isClientAdminModeEnabled,
} from '../api';
import type {
  EventPredictionScheduleResponse,
  EventScheduleItem,
  PredictedScheduleMatch,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { MATCH_HUB_VIEWS, viewTitle } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling } from '../hooks/useSingleFlightPolling';
import { eventHasMatchesInPlay } from './matchStatus';
import { buildMatchCenterPath, metric, pct, teamNumberFromTeamKey } from './centerUtils';
import { MOBILE_LAYOUT_BREAKPOINT, useMobileLayout } from '../hooks/useMobileLayout';
import { Stat, Table, type TableColumn } from '../components/ui/primitives';
import styles from './MatchPredictionPage.module.css';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';

const PHONE_PAGE_SIZE = 20;
import { matchWinProbability, type WinProbabilitySource } from '../features/predictions/winProbability';

/* ------------------------------------------------------------------ */
/*  Constants & helpers                                                */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'scouting_center_event_key';
const PREDICTIONS_REFRESH_MS = 60_000;

const COMP_LEVEL_ORDER: Record<string, number> = { qm: 0, ef: 1, qf: 2, sf: 3, f: 4 };

function matchSortKey(m: { comp_level: string; set_number: number; match_number: number }): number {
  const level = COMP_LEVEL_ORDER[m.comp_level] ?? 9;
  return level * 1_000_000 + m.set_number * 1_000 + m.match_number;
}

function matchDisplayName(m: { comp_level: string; set_number: number; match_number: number }): string {
  return matchLabel(m.comp_level, m.set_number, m.match_number);
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
// Edge and expected RP stay on the wider table; the call and the result are what
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
              {pred.is_completed == null ? <span title="Schedule unavailable" aria-label="Result unknown">—</span> : !pred.is_completed ? 'Upcoming' : correct !== null ? <ResultVerdict correct={correct} /> : 'Tie'}
            </span>
          </button>
        );
      })}
    </div>
  );
}

const BONUS_RP_LABELS: Record<string, string> = { energized: 'Energized', supercharged: 'Supercharged', traversal: 'Traversal' };

type BonusChances = NonNullable<PredictedScheduleMatch['prediction']>['bonus_rp_chances'];

function rpSummary(chances: BonusChances): MatchPrediction['rp'] {
  if (!chances) return null;
  const side = (color: 'red' | 'blue') =>
    Object.entries(chances[color])
      .map(([rp, p]) => `${BONUS_RP_LABELS[rp] ?? rp} ${Math.round(p * 100)}%`)
      .join(', ');
  return { red: chances.red_expected_rp, blue: chances.blue_expected_rp, detail: `Red: ${side('red')}. Blue: ${side('blue')}.` };
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
        </>
      );
    },
  },
  {
    key: 'rp',
    label: 'Exp. RP',
    render: (pred) =>
      pred.rp ? (
        <span className={styles.nowrap} title={pred.rp.detail}>
          <span className="text-red">{metric(pred.rp.red, 1)}</span>
          {' vs '}
          <span className="text-blue">{metric(pred.rp.blue, 1)}</span>
        </span>
      ) : '—',
  },
  {
    key: 'confidence',
    label: 'Edge',
    numeric: true,
    // How far the call is from a coin flip.
    render: (pred) => (pred.red_prob == null ? '—' : pct(Math.abs(pred.red_prob - 0.5) * 2, 0)),
  },
  {
    key: 'result',
    label: 'Result',
    render: (pred) => {
      // The banner above says why; repeating it on every row was noise.
      if (pred.is_completed == null) return <span className="text-muted" title="Schedule unavailable" aria-label="Result unknown">—</span>;
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
  red_prob: number | null;
  blue_prob: number | null;
  /* Quals: expected ranking points and each bonus RP's chance, per alliance. */
  rp: { red: number; blue: number; detail: string } | null;
  prediction_source: PredictionSource | null;
  ml_edge_confidence: number | null;
  // Actual results (from schedule)
  red_score: number | null;
  blue_score: number | null;
  winner: 'red' | 'blue' | 'tie' | null;
  is_completed: boolean | null;
};

type FilterMode = 'all' | 'upcoming' | 'completed';

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function MatchPredictionPage() {
  const navigate = useNavigate();

  const { eventKey, eventInput, setEventInput, commitInput, selectEvent, fetchTrigger } = useEventKeyParam(STORAGE_KEY);
  const [predictionData, setPredictionData] = useState<EventPredictionScheduleResponse | null>(null);
  const [scheduleData, setScheduleData] = useState<EventScheduleItem[] | null>(null);
  const [tbaPredictions, setTbaPredictions] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [scheduleError, setScheduleError] = useState('');
  const [errorText, setErrorText] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');
  const filterInitializedFor = useRef('');

  /* --- Fetch data --- */
  // Only the latest request may write: switching events on a slow connection let
  // the old event's late response replace the new one's predictions.
  const requestGeneration = useRef(0);
  const loadedAtMs = useRef(0);
  // A refresh keeps what is on screen and only replaces what came back, so a
  // dropped request on venue Wi-Fi never blanks the list.
  const fetchData = useCallback(async (key: string, refresh = false) => {
    if (!key) return false;
    const generation = ++requestGeneration.current;
    if (!refresh) {
      setLoading(true);
      setErrorText('');
      setPredictionData(null);
      setScheduleData(null);
      setScheduleError('');
      setTbaPredictions(null);
    }

    const results = await Promise.allSettled([
      getEventPredictionSchedule(key),
      getEventSchedule(key),
      getEventPredictions(key),
    ]);
    if (generation !== requestGeneration.current) return true;

    if (results[0].status === 'fulfilled') {
      setPredictionData(results[0].value);
      setErrorText('');
    } else if (!refresh) {
      setErrorText((results[0].reason as Error)?.message || 'Failed to load predictions.');
    }

    if (results[1].status === 'fulfilled') {
      setScheduleData(results[1].value.matches);
      if (filterInitializedFor.current !== key) {
        filterInitializedFor.current = key;
        setFilterMode(results[1].value.matches.some((match) => match.is_completed === false) ? 'upcoming' : 'all');
      }
      setScheduleError('');
      loadedAtMs.current = Date.now();
    } else {
      setScheduleError('Schedule unavailable. Match results could not be checked.');
    }

    if (results[2].status === 'fulfilled') {
      setTbaPredictions(results[2].value.predictions);
    }

    if (!refresh) setLoading(false);
    // TBA's own predictions are optional; back off only when ours fail.
    return results[0].status === 'fulfilled' && results[1].status === 'fulfilled';
  }, []);

  useEffect(() => {
    if (!eventKey) return;
    const timer = window.setTimeout(() => {
      void fetchData(eventKey);
    }, 0);
    return () => { window.clearTimeout(timer); requestGeneration.current += 1; };
  }, [eventKey, fetchData, fetchTrigger]);

  // While the event is running, results and forecasts change every few minutes.
  const pageVisible = usePageVisibility();
  const [inPlay, setInPlay] = useState(false);
  useEffect(() => {
    const check = () => setInPlay(eventHasMatchesInPlay(scheduleData, Date.now()));
    check();
    const timer = window.setInterval(check, 60_000);
    return () => window.clearInterval(timer);
  }, [scheduleData]);
  useSingleFlightPolling({
    enabled: Boolean(eventKey) && inPlay,
    visible: pageVisible,
    intervalMs: PREDICTIONS_REFRESH_MS,
    minBackoffMs: PREDICTIONS_REFRESH_MS,
    maxBackoffMs: 5 * 60_000,
    // 'initial' also fires when a locked phone wakes up; refresh only if stale.
    run: (reason) => (
      reason === 'initial' && Date.now() - loadedAtMs.current < PREDICTIONS_REFRESH_MS
        ? true
        : fetchData(eventKey, true)
    ),
  });

  /* --- Merge predictions + schedule results --- */
  const predictions = useMemo<MatchPrediction[]>(() => {
    if (!predictionData?.matches) return [];

    const scheduleMap = new Map<string, EventScheduleItem>();
    for (const m of scheduleData ?? []) {
      scheduleMap.set(m.match_key, m);
    }

    return predictionData.matches
      .map((m): MatchPrediction => {
        const sched = scheduleMap.get(m.match_key);
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
          red_prob: redProb,
          blue_prob: blueProb,
          rp: rpSummary(m.prediction?.bonus_rp_chances),
          prediction_source: source,
          ml_edge_confidence: mlEdge,
          red_score: sched?.red_score ?? null,
          blue_score: sched?.blue_score ?? null,
          winner: sched?.winner_alliance ?? null,
          is_completed: sched?.is_completed ?? null,
        };
      })
      .sort((a, b) => matchSortKey(a) - matchSortKey(b));
  }, [predictionData, scheduleData]);

  // A phone got all ~140 matches at once: 20 to 38 screens of scrolling. Show a page at a time.
  const isMobileLayout = useMobileLayout();
  const [phoneVisibleCount, setPhoneVisibleCount] = useState(PHONE_PAGE_SIZE);
  useEffect(() => {
    setPhoneVisibleCount(PHONE_PAGE_SIZE);
  }, [filterMode, eventKey]);

  const filteredPredictions = useMemo(() => {
    if (filterMode === 'upcoming') return predictions.filter((p) => p.is_completed === false);
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
    return {
      total: predictions.length,
      completed: completed.length,
      assessed: nonTie.length,
      ties: completed.length - nonTie.length,
      // Points-model predictions use only results from before each match: real forecasts.
      forecast: completed.filter((p) => p.prediction_source === 'points_model').length,
      correct,
      accuracy: nonTie.length > 0 ? correct / nonTie.length : null,
    };
  }, [predictions]);

  function openMatchCenter(matchKey: string) {
    navigate(buildMatchCenterPath('', matchKey));
  }


  return (
    <>
    <PageViewBar items={MATCH_HUB_VIEWS} />
    <div className="center-page-container">

        {/* ---- Event Selection ---- */}
        {/* Named for what it holds. Both this card and the list below it were
            titled "Match Predictions", so the page had two identically named
            cards and two identical "Open Match Predictions fullscreen"
            buttons. This one is a picker. */}
        <SurfaceCard
          title={viewTitle(MATCH_HUB_VIEWS, '/match-center/predictions')}
          subtitle="Who's likely to win each match, from the official results so far."
          mobileCollapsible={false}
        >
          <EventPicker
            label="Event"
            value={eventKey}
            onSelect={selectEvent}
            inputValue={eventInput}
            onInputChange={setEventInput}
            onSubmit={commitInput}
            loading={loading}
          />

          {/* No count here: the Matches card below carries it. */}

          {scheduleError ? <p className="center-callout warning">{scheduleError} <button type="button" className="center-btn ghost" onClick={() => void fetchData(eventKey, true)}>Retry schedule</button></p> : null}
          {errorText ? <p className="center-callout warning">{errorText}</p> : null}
        </SurfaceCard>

        {/* ---- Filter + Match List ---- */}
        {predictions.length > 0 ? (
          <SurfaceCard
            expandable
            title="Matches"
            subtitle={`${filteredPredictions.length} match${filteredPredictions.length === 1 ? '' : 'es'}`}
            right={
              <SegmentedTabs<FilterMode>
                ariaLabel="Which matches"
                value={filterMode}
                onChange={setFilterMode}
                items={[
                  { value: 'all', label: 'All' },
                  { value: 'upcoming', label: 'Upcoming' },
                  { value: 'completed', label: 'Completed' },
                ]}
              />
            }
          >
            <Table
              columns={predictionColumns(openMatchCenter)}
              rows={isMobileLayout ? filteredPredictions.slice(0, phoneVisibleCount) : filteredPredictions}
              rowKey={(pred) => pred.match_key}
              cardBreakpoint={MOBILE_LAYOUT_BREAKPOINT}
              renderCards={(narrowRows) => renderPredictionCards(narrowRows, openMatchCenter)}
              rowClassName={(pred) => {
                const correct = wasPredictionCorrect(pred);
                if (correct === null) return undefined;
                return correct ? styles.rowCorrect : styles.rowWrong;
              }}
            />
            {isMobileLayout && filteredPredictions.length > phoneVisibleCount ? (
              <button
                type="button"
                className="center-btn ghost"
                onClick={() => setPhoneVisibleCount((count) => count + PHONE_PAGE_SIZE)}
              >
                Show {Math.min(PHONE_PAGE_SIZE, filteredPredictions.length - phoneVisibleCount)} more
              </button>
            ) : null}
          </SurfaceCard>
        ) : null}

        {/* ---- Model Accuracy ---- */}
        {stats.completed > 0 ? (
          <SurfaceCard
            title={stats.forecast > 0 ? 'Forecast Check' : 'Hindsight Check'}
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
                sub={`${stats.correct} of ${stats.assessed} completed match${stats.assessed === 1 ? '' : 'es'}`}
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
              </div>
            </div>
            {stats.ties > 0 ? (
              <p className="center-callout muted">{stats.ties} tied match{stats.ties === 1 ? '' : 'es'} excluded from accuracy.</p>
            ) : null}
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

    </div>
    </>
  );
}
