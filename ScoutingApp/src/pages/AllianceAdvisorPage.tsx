import { rankedTeamPool, type PoolTeam } from './eventTeamPool';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  getAllianceOutlook,
  getEventTeamsIntel,
  getSelectionModel,
} from '../api';
import type {
  AllianceOutlookResponse,
  EventTeamsIntelResponse,
  TheoreticalSelectionModel,
  TheoreticalSelectionTopTeam,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { COMPARE_VIEWS, viewTitle } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { BestNextPick } from '../features/picks/BestNextPick';
import {
  Button,
  CardBody,
  Chip,
  FieldSelect,
  FieldStepper,
  Stat,
  Table,
  type SortDirection,
  type TableColumn,
} from '../components/ui/primitives';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import styles from './AllianceAdvisorPage.module.css';
import { plainSignalLabel } from './teamCenterOfficial';
import { teamsFromParam } from './allianceBuilder';
import { metric, pct, teamNumberFromTeamKey } from './centerUtils';

/* ------------------------------------------------------------------ */
/*  Constants                                                         */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'scouting_center_event_key';
const DEFAULT_SIMULATIONS = 500;
const DEFAULT_RANK_WEIGHT = 1.0;
const DEFAULT_SCALE = 35;
const DEFAULT_CAPTAINS = 8;

type EventTeam = PoolTeam;

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

function teamLabel(teamKey: string, teams: EventTeam[]): string {
  const team = teams.find((t) => t.team_key === teamKey);
  if (!team) {
    const num = teamNumberFromTeamKey(teamKey);
    return num ? `#${num}` : teamKey;
  }
  return team.nickname ? `#${team.team_number} ${team.nickname}` : `#${team.team_number}`;
}

function teamNumLabel(teamKey: string): string {
  const num = teamNumberFromTeamKey(teamKey);
  return num ? `${num}` : teamKey;
}

// Returns a class, not a colour. The old version returned
// `var(--color-muted, #94a3b8)` for the low band — and --color-muted is not a
// token, so that band was always the hardcoded grey, in both themes.
function barToneClass(value: number): string {
  if (value >= 80) return styles.barHigh;
  if (value >= 60) return styles.barGood;
  if (value >= 40) return styles.barFair;
  return styles.barLow;
}

function barWidth(value: number, max: number): string {
  if (max <= 0) return '0%';
  return `${Math.min(100, (value / max) * 100)}%`;
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */

export function AllianceAdvisorPage() {
  const navigate = useNavigate();

  /* --- Event selection (shared hook) --- */
  const { eventKey, eventInput, setEventInput, selectEvent, fetchTrigger } = useEventKeyParam(STORAGE_KEY);
  const [eventTeams, setEventTeams] = useState<EventTeamsIntelResponse | null>(null);
  const [loadingTeams, setLoadingTeams] = useState(false);

  /* --- Selection model settings --- */
  const [simulations, setSimulations] = useState(DEFAULT_SIMULATIONS);
  const [rankWeight, setRankWeight] = useState(DEFAULT_RANK_WEIGHT);
  const [scale, setScale] = useState(DEFAULT_SCALE);

  /* --- Results --- */
  const [selectionModel, setSelectionModel] = useState<TheoreticalSelectionModel | null>(null);
  const [loadingModel, setLoadingModel] = useState(false);
  const [errorText, setErrorText] = useState('');

  /* --- Alliance builder (what-if) --- */
  const [searchParams] = useSearchParams();
  const [builderSlots, setBuilderSlots] = useState<[string, string, string]>(() => teamsFromParam(searchParams.get('teams')));
  const [builderResult, setBuilderResult] = useState<AllianceOutlookResponse | null>(null);
  const [loadingBuilder, setLoadingBuilder] = useState(false);
  const [builderError, setBuilderError] = useState('');

  const teamsGeneration = useRef(0);
  const modelGeneration = useRef(0);
  const builderGeneration = useRef(0);

  /* --- Derived --- */
  const teamPool = useMemo<EventTeam[]>(() => rankedTeamPool(eventTeams?.teams), [eventTeams]);

  const teamKeySet = useMemo(() => new Set(teamPool.map((t) => t.team_key)), [teamPool]);

  useEffect(() => {
    modelGeneration.current += 1;
    setSelectionModel(null);
    setLoadingModel(false);
    return () => { modelGeneration.current += 1; };
  }, [eventKey, teamPool, rankWeight, scale, simulations, fetchTrigger]);

  useEffect(() => {
    builderGeneration.current += 1;
    setBuilderResult(null);
    setLoadingBuilder(false);
    return () => { builderGeneration.current += 1; };
  }, [eventKey, teamPool, builderSlots, fetchTrigger]);

  /* --- Fetch event teams --- */
  const fetchTeams = useCallback(async (key: string) => {
    const generation = ++teamsGeneration.current;
    setEventTeams(null);
    if (!key) return;
    setLoadingTeams(true);
    setErrorText('');
    try {
      const payload = await getEventTeamsIntel(key, {
        include_tba: true,
        include_season_fallback: true,
        include_rating_details: false,
        include_rating_signals: true,
      });
      if (generation === teamsGeneration.current) setEventTeams(payload);
    } catch (err) {
      if (generation !== teamsGeneration.current) return;
      setErrorText((err as Error).message || 'Failed to load event teams.');
      setEventTeams(null);
    } finally {
      if (generation === teamsGeneration.current) setLoadingTeams(false);
    }
  }, []);

  useEffect(() => {
    if (eventKey) void fetchTeams(eventKey);
    else {
      setEventTeams(null);
      setSelectionModel(null);
    }
    return () => { teamsGeneration.current += 1; };
  }, [eventKey, fetchTeams, fetchTrigger]);

  /* --- Run selection model --- */
  const runSelectionModel = useCallback(async () => {
    if (!eventKey || teamPool.length < 6) {
      setErrorText('Need an event with at least 6 teams to run the selection model.');
      return;
    }
    const generation = ++modelGeneration.current;
    setLoadingModel(true);
    setErrorText('');
    try {
      const result = await getSelectionModel(eventKey, {
        rank_weight: rankWeight,
        scale,
        captains: DEFAULT_CAPTAINS,
        simulations,
        rank_source: 'auto',
      });
      if (generation !== modelGeneration.current) return;
      setSelectionModel(result.selection_model ?? null);
      if (!result.selection_model) {
        setErrorText('The selection model was not returned. The event may not have enough data.');
      }
    } catch (err) {
      if (generation !== modelGeneration.current) return;
      setErrorText((err as Error).message || 'Selection model failed.');
      setSelectionModel(null);
    } finally {
      if (generation === modelGeneration.current) setLoadingModel(false);
    }
  }, [eventKey, teamPool, rankWeight, scale, simulations]);

  /* --- Run alliance builder --- */
  const runBuilder = useCallback(async () => {
    if (!eventKey) {
      setBuilderError('Select an event first.');
      return;
    }
    const keys = builderSlots.map((s) => s.trim().toLowerCase()).filter(Boolean);
    if (keys.length !== 3 || new Set(keys).size !== 3) {
      setBuilderError('Pick 3 unique teams.');
      return;
    }
    const invalid = keys.filter((k) => !teamKeySet.has(k));
    if (invalid.length) {
      setBuilderError(`Teams not in event: ${invalid.map(teamNumLabel).join(', ')}`);
      return;
    }
    const generation = ++builderGeneration.current;
    setLoadingBuilder(true);
    setBuilderError('');
    try {
      const result = await getAllianceOutlook(eventKey, keys);
      if (generation === builderGeneration.current) setBuilderResult(result);
    } catch (err) {
      if (generation !== builderGeneration.current) return;
      setBuilderError((err as Error).message || 'Alliance analysis failed.');
      setBuilderResult(null);
    } finally {
      if (generation === builderGeneration.current) setLoadingBuilder(false);
    }
  }, [eventKey, builderSlots, teamKeySet]);

  // Memoised because the table columns depend on it; a fresh function each
  // render would rebuild every column definition on every keystroke.
  const openTeamCenter = useCallback((teamKey: string) => {
    const params = new URLSearchParams();
    params.set('team', teamKey.toLowerCase());
    if (eventKey) params.set('event', eventKey);
    navigate(`/team-center?${params.toString()}`);
  }, [eventKey, navigate]);

  function handleEventSelect(key: string) {
    selectEvent(key);
    setSelectionModel(null);
    setBuilderResult(null);
  }

  /* --- Render helpers --- */
  const maxDesirability = useMemo(
    () =>
      selectionModel?.top_desirability?.reduce(
        (max, t) => Math.max(max, t.selection_desirability),
        0,
      ) ?? 100,
    [selectionModel],
  );

  const allianceBoard = useMemo<Array<{ captain: string; picks: string[] }>>(() => {
    if (!selectionModel?.expected_alliance_board) return [];
    return Object.entries(selectionModel.expected_alliance_board)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([, teams]) => ({
        captain: teams[0] || '',
        picks: teams.slice(1),
      }));
  }, [selectionModel]);

  const [desirabilitySort, setDesirabilitySort] = useState<{ key: string; direction: SortDirection }>({
    key: 'selection_desirability',
    direction: 'desc',
  });

  // Flattened so the Table can take it as rows: the API returns a map of seed
  // to picks, and a map is not a row list.
  const seedPickRows = useMemo(() => {
    const map = selectionModel?.first_round_seed_pick_probabilities;
    if (!map) return [];
    return Object.entries(map)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([seed, picks]) => ({
        seed,
        captainLabel: selectionModel?.captains[Number(seed) - 1]
          ? teamNumLabel(selectionModel.captains[Number(seed) - 1])
          : '',
        picks,
      }));
  }, [selectionModel]);

  const desirabilityColumns: TableColumn<TheoreticalSelectionTopTeam>[] = useMemo(
    () => [
      {
        key: 'team_key',
        label: 'Team',
        render: (row) => (
          <button type="button" className={styles.teamLink} onClick={() => openTeamCenter(row.team_key)}>
            {teamLabel(row.team_key, teamPool)}
          </button>
        ),
      },
      { key: 'rank', label: 'Rank', numeric: true, sortable: true, width: '80px' },
      {
        key: 'strength_score',
        label: 'Strength',
        numeric: true,
        sortable: true,
        width: '100px',
        render: (row) => metric(row.strength_score, 1),
      },
      {
        key: 'selection_desirability',
        label: 'Desirability',
        numeric: true,
        sortable: true,
        width: '160px',
        render: (row) => (
          <span className={styles.desirability}>
            <span className={styles.barTrack}>
              <span
                className={`${styles.bar} ${barToneClass(row.selection_desirability)}`}
                style={{ width: barWidth(row.selection_desirability, maxDesirability) }}
              />
            </span>
            <span className={styles.desirabilityValue}>{metric(row.selection_desirability, 1)}</span>
          </span>
        ),
      },
      {
        key: 'first_round_pick_probability_0_1',
        label: 'R1 Pick',
        numeric: true,
        sortable: true,
        width: '100px',
        render: (row) => pct(row.first_round_pick_probability_0_1, 0),
      },
      {
        key: 'second_round_pick_probability_0_1',
        label: 'R2 Pick',
        numeric: true,
        sortable: true,
        width: '100px',
        render: (row) => pct(row.second_round_pick_probability_0_1, 0),
      },
      {
        key: 'is_captain',
        label: 'Captain',
        align: 'center',
        width: '90px',
        render: (row) => (row.is_captain ? <Chip tone="accent" size="sm">Captain</Chip> : '—'),
      },
    ],
    [teamPool, maxDesirability, openTeamCenter],
  );

  const sortedDesirability = useMemo(() => {
    const rows = selectionModel?.top_desirability ?? [];
    if (!desirabilitySort) return rows;
    const { key, direction } = desirabilitySort;
    return [...rows].sort((a, b) => {
      const left = (a as unknown as Record<string, unknown>)[key];
      const right = (b as unknown as Record<string, unknown>)[key];
      const order =
        typeof left === 'number' && typeof right === 'number'
          ? left - right
          : String(left).localeCompare(String(right));
      return direction === 'asc' ? order : -order;
    });
  }, [selectionModel, desirabilitySort]);

  return (
    <>
    <PageViewBar items={COMPARE_VIEWS} />
    <div className={styles.layout}>

        {/* ---- Event Selection ---- */}
        <SurfaceCard
          title={viewTitle(COMPARE_VIEWS, '/compare/alliance-advisor')}
          subtitle="Build an alliance and see who fits it best."
          mobileCollapsible={false}
        >
          <EventPicker
            label="Event"
            value={eventKey}
            onSelect={handleEventSelect}
            inputValue={eventInput}
            onInputChange={setEventInput}
            onSubmit={() => handleEventSelect(eventInput)}
            loading={loadingTeams}
          />

          {eventTeams ? (
            <p className={styles.eventSummary}>
              <strong>{eventTeams.event_name || 'Selected event'}</strong> — {teamPool.length} team{teamPool.length === 1 ? '' : 's'} loaded
              {eventTeams.teams_with_event_rating > 0
                ? ` (${eventTeams.teams_with_event_rating} with ratings)`
                : ''}
            </p>
          ) : null}

          {errorText ? (
            <p className={`${styles.note} ${styles.noteWarning}`} role="alert">{errorText}</p>
          ) : null}
        </SurfaceCard>

        {/* ---- Selection Model Controls ---- */}
        {teamPool.length > 0 ? (
          <SurfaceCard
            title="Selection Model Settings"
            collapsible
          >
            <CardBody>
              <div className={styles.paramGrid}>
                <FieldStepper
                  label="Simulations"
                  name="simulations"
                  value={simulations}
                  onValueChange={setSimulations}
                  min={50}
                  max={5000}
                  step={50}
                />
                <FieldStepper
                  label="Rank Weight"
                  name="rank weight"
                  value={rankWeight}
                  onValueChange={setRankWeight}
                  min={0}
                  max={4}
                  step={0.1}
                />
                <FieldStepper
                  label="Scale"
                  name="scale"
                  value={scale}
                  onValueChange={setScale}
                  min={1}
                  max={200}
                  step={1}
                />
              </div>
              <div className={styles.actions}>
                <Button
                  variant="primary"
                  onClick={() => void runSelectionModel()}
                  loading={loadingModel}
                  disabled={teamPool.length < 6}
                >
                  {loadingModel ? 'Simulating…' : 'Run Selection Model'}
                </Button>
              </div>

              {selectionModel?.notes?.length ? (
                <ul className={styles.notes}>
                  {selectionModel.notes.map((note, i) => (
                    <li key={i}>{note}</li>
                  ))}
                </ul>
              ) : null}
            </CardBody>
          </SurfaceCard>
        ) : null}

        {/* ---- Top Teams by Desirability ---- */}
        {selectionModel && selectionModel.top_desirability.length > 0 ? (
          <SurfaceCard
            expandable
            title="Team Desirability Rankings"
            subtitle={`${selectionModel.top_desirability.length} team${selectionModel.top_desirability.length === 1 ? '' : 's'} ranked by selection desirability. ${selectionModel.simulations.toLocaleString()} simulation${selectionModel.simulations === 1 ? '' : 's'}.`}
            right={
              <Chip tone={selectionModel.rank_source === 'tba' ? 'accent' : 'neutral'}>
                {selectionModel.rank_source === 'tba' ? 'TBA Rankings' : 'Model Rankings'}
              </Chip>
            }
          >
            {/* One Table, not a desktop table plus a hand-maintained mobile card
                list. Below 560px it renders each row as a stacked card itself. */}
            <Table
              columns={desirabilityColumns}
              rows={sortedDesirability}
              rowKey={(row) => row.team_key}
              sortBy={desirabilitySort}
              onSort={(key, direction) => setDesirabilitySort({ key, direction })}
              stickyHeader
              caption="Teams ranked by how desirable they are as an alliance pick."
            />
          </SurfaceCard>
        ) : null}

        {/* ---- Expected Alliance Board ---- */}
        {allianceBoard.length > 0 ? (
          <SurfaceCard title="Expected Alliance Board">
            <div className={styles.allianceGrid}>
              {allianceBoard.map((alliance, idx) => (
                <AllianceCard
                  key={idx}
                  allianceNum={idx + 1}
                  captain={alliance.captain}
                  picks={alliance.picks}
                  teams={teamPool}
                  onOpenTeam={openTeamCenter}
                />
              ))}
            </div>
          </SurfaceCard>
        ) : null}

        {/* ---- First-Round Seed Pick Probabilities ---- */}
        {selectionModel?.first_round_seed_pick_probabilities &&
          Object.keys(selectionModel.first_round_seed_pick_probabilities).length > 0 ? (
          <SurfaceCard
            expandable
            title="First-Round Pick Probabilities"
            collapsible
          >
            <Table
              columns={[
                {
                  key: 'seed',
                  label: 'Captain (Seed)',
                  render: (row) => (
                    <span className={styles.captainRow}>
                      Seed {row.seed}
                      {row.captainLabel ? ` (${row.captainLabel})` : ''}
                    </span>
                  ),
                },
                {
                  key: 'picks',
                  label: 'Most Likely Picks',
                  render: (row) => (
                    <span className={styles.pickChips}>
                      {row.picks.slice(0, 5).map((pick) => (
                        <button
                          key={pick.team_key}
                          type="button"
                          className={styles.chipButton}
                          onClick={() => openTeamCenter(pick.team_key)}
                          title={`Desirability: ${metric(pick.selection_desirability, 1)}`}
                        >
                          <Chip size="sm">
                            {teamNumLabel(pick.team_key)}{' '}
                            <span className={styles.pickPct}>{pct(pick.probability_0_1, 0)}</span>
                          </Chip>
                        </button>
                      ))}
                    </span>
                  ),
                },
              ]}
              rows={seedPickRows}
              rowKey={(row) => row.seed}
            />
          </SurfaceCard>
        ) : null}

        {/* ---- Alliance Builder (What-If) ---- */}
        {teamPool.length > 0 ? (
          <SurfaceCard expandable title="Alliance Builder">
            <CardBody>
              <div className={styles.paramGrid}>
                {([0, 1, 2] as const).map((slot) => (
                  <FieldSelect
                    key={slot}
                    label={slot === 0 ? 'Captain' : `Pick ${slot}`}
                    value={builderSlots[slot]}
                    onChange={(e) => {
                      const next: [string, string, string] = [...builderSlots];
                      next[slot] = e.target.value;
                      setBuilderSlots(next);
                      setBuilderResult(null);
                      setBuilderError('');
                    }}
                  >
                    <option value="">— Select team —</option>
                    {teamPool.map((t) => (
                      <option key={t.team_key} value={t.team_key}>
                        #{t.team_number} {t.nickname || ''}
                        {t.rating_0_100 != null ? ` (${metric(t.rating_0_100, 1)})` : ''}
                      </option>
                    ))}
                  </FieldSelect>
                ))}
              </div>

              <div className={styles.actions}>
                <Button variant="primary" onClick={() => void runBuilder()} loading={loadingBuilder}>
                  {loadingBuilder ? 'Analyzing…' : 'Analyze Alliance'}
                </Button>
              </div>

              {builderError ? (
                <p className={`${styles.note} ${styles.noteWarning}`} role="alert">{builderError}</p>
              ) : null}

              {builderResult?.unavailable ? (
                <p className={styles.note}>
                  {builderResult.unavailable === 'too_few_teams'
                    ? 'This event has too few teams to compare alliances.'
                    : 'Alliances are only analysed for the current, rated season.'}
                </p>
              ) : builderResult && builderResult.vs_strong && builderResult.vs_typical ? (
                <div className={styles.stack}>
                  {/* One question: how good is this lineup. The chance to win a playoff
                      match against a strong alliance at this event leads; the rest says
                      why. Same team model and reference alliances as Best Next Pick. */}
                  <div className="page-hero">
                    <Stat
                      size="display"
                      label="Beats a strong alliance"
                      value={pct(builderResult.vs_strong.win_prob, 0)}
                      sub={`${builderResult.vs_strong.teams.map(teamNumLabel).join(', ')} · ${metric(builderResult.vs_strong.points, 0)} pts`}
                    />
                    <div className="page-hero-stats">
                      <Stat
                        size="sm"
                        label="Expected points"
                        value={metric(builderResult.alliance_points ?? null, 0)}
                        sub={builderResult.alliance_points_range_80
                          ? `usually ${metric(builderResult.alliance_points_range_80[0], 0)}–${metric(builderResult.alliance_points_range_80[1], 0)}`
                          : undefined}
                      />
                      <Stat
                        size="sm"
                        label="Beats a typical alliance"
                        value={pct(builderResult.vs_typical.win_prob, 0)}
                        sub={`${metric(builderResult.vs_typical.points, 0)} pts`}
                      />
                      {Object.entries(builderResult.bonus_rp_chances || {}).map(([rp, chance]) => (
                        <Stat key={rp} size="sm" label={`${rp.charAt(0).toUpperCase()}${rp.slice(1)} RP`} value={pct(chance, 0)} />
                      ))}
                    </div>
                  </div>

                  <Table
                    columns={[
                      {
                        key: 'team_key',
                        label: 'Team',
                        render: (team) => (
                          <button
                            type="button"
                            className={styles.teamLink}
                            onClick={() => openTeamCenter(team.team_key)}
                          >
                            {teamLabel(team.team_key, teamPool)}
                          </button>
                        ),
                      },
                      { key: 'rating_0_100', label: 'Rating', numeric: true, render: (t) => metric(t.rating_0_100, 1) },
                      { key: 'points_per_match', label: 'Pts / match', numeric: true, render: (t) => metric(t.points_per_match, 0) },
                      { key: 'climb_points', label: 'Climb', numeric: true, render: (t) => metric(t.climb_points, 1) },
                      { key: 'event_rank_by_points', label: 'Rank here', numeric: true, render: (t) => `${t.event_rank_by_points} of ${builderResult.team_count ?? '—'}` },
                    ]}
                    rows={builderResult.teams}
                    rowKey={(team) => team.team_key}
                    caption="What each robot brings, from its rating."
                  />

                  {builderResult.teams.some((t) => t.strengths.length > 0 || t.risks.length > 0) ? (
                    <div className={styles.prosConsGrid}>
                      {builderResult.teams.map((team) => (
                        <div key={team.team_key} className={styles.prosConsCard}>
                          <strong className={styles.prosConsTitle}>
                            {teamLabel(team.team_key, teamPool)}
                          </strong>
                          {team.strengths.length > 0 ? (
                            <ul className={styles.prosList}>
                              {team.strengths.map((label) => <li key={label}>{plainSignalLabel(label)}</li>)}
                            </ul>
                          ) : null}
                          {team.risks.length > 0 ? (
                            <ul className={styles.consList}>
                              {team.risks.map((label) => <li key={label}>{plainSignalLabel(label)}</li>)}
                            </ul>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </CardBody>
          </SurfaceCard>
        ) : null}

        {eventKey && teamPool.length > 0 ? (
          <BestNextPick
            eventKey={eventKey}
            partners={builderSlots.slice(0, 2).map((s) => s.trim().toLowerCase()).filter(Boolean)}
            onOpenTeam={openTeamCenter}
          />
        ) : null}

    </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function AllianceCard({
  allianceNum,
  captain,
  picks,
  teams,
  onOpenTeam,
}: {
  allianceNum: number;
  captain: string;
  picks: string[];
  teams: EventTeam[];
  onOpenTeam: (teamKey: string) => void;
}) {
  return (
    <div className={styles.allianceCard}>
      <div className={styles.allianceHead}>
        <span className={styles.allianceBadge}>{allianceNum}</span>
        <span>Alliance {allianceNum}</span>
      </div>
      <div className={styles.allianceChips}>
        <button
          type="button"
          className={styles.chipButton}
          onClick={() => onOpenTeam(captain)}
          title="Captain"
        >
          <Chip tone="accent" dot>{teamLabel(captain, teams)}</Chip>
        </button>
        {picks.map((pick) => (
          <button
            key={pick}
            type="button"
            className={styles.chipButton}
            onClick={() => onOpenTeam(pick)}
          >
            <Chip>{teamLabel(pick, teams)}</Chip>
          </button>
        ))}
      </div>
    </div>
  );
}
