import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getEventTeamsIntel,
  getTheoreticalAlliance,
} from '../api';
import type {
  EventTeamsIntelResponse,
  TheoreticalAllianceResponse,
  TheoreticalSelectionModel,
  TheoreticalSelectionTopTeam,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { COMPARE_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
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
import {
  asRecord,
  metric,
  parseNumber,
  pct,
  teamNumberFromTeamKey,
} from './centerUtils';

/* ------------------------------------------------------------------ */
/*  Constants                                                         */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'scouting_center_event_key';
const DEFAULT_SIMULATIONS = 500;
const DEFAULT_RANK_WEIGHT = 1.0;
const DEFAULT_SCALE = 35;
const DEFAULT_CAPTAINS = 8;

type EventTeam = {
  team_key: string;
  team_number: number;
  nickname: string | null;
  rating_0_100: number | null;
};

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
  const [builderSlots, setBuilderSlots] = useState<[string, string, string]>(['', '', '']);
  const [builderResult, setBuilderResult] = useState<TheoreticalAllianceResponse | null>(null);
  const [loadingBuilder, setLoadingBuilder] = useState(false);
  const [builderError, setBuilderError] = useState('');

  /* --- Derived --- */
  const teamPool = useMemo<EventTeam[]>(() => {
    const teams = Array.isArray(eventTeams?.teams) ? eventTeams.teams : [];
    return teams
      .map((entry) => {
        const row = asRecord(entry);
        const rating = asRecord(row?.rating);
        return {
          team_key: String(row?.team_key || '').toLowerCase(),
          team_number: parseNumber(row?.team_number) ?? 0,
          nickname: typeof row?.nickname === 'string' ? row.nickname : null,
          rating_0_100: parseNumber(rating?.rating_0_100),
        };
      })
      .filter((row) => row.team_key.length > 0)
      .sort((a, b) => (b.rating_0_100 ?? 0) - (a.rating_0_100 ?? 0));
  }, [eventTeams]);

  const teamKeySet = useMemo(() => new Set(teamPool.map((t) => t.team_key)), [teamPool]);

  /* --- Fetch event teams --- */
  const fetchTeams = useCallback(async (key: string) => {
    if (!key) return;
    setLoadingTeams(true);
    setErrorText('');
    try {
      const payload = await getEventTeamsIntel(key, {
        include_tba: true,
        include_statbotics: false,
        include_season_fallback: true,
        include_rating_details: false,
        include_rating_signals: true,
      });
      setEventTeams(payload);
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to load event teams.');
      setEventTeams(null);
    } finally {
      setLoadingTeams(false);
    }
  }, []);

  useEffect(() => {
    if (eventKey) void fetchTeams(eventKey);
    else {
      setEventTeams(null);
      setSelectionModel(null);
    }
  }, [eventKey, fetchTeams, fetchTrigger]);

  /* --- Run selection model --- */
  const runSelectionModel = useCallback(async () => {
    if (!eventKey || teamPool.length < 6) {
      setErrorText('Need an event with at least 6 teams to run the selection model.');
      return;
    }
    setLoadingModel(true);
    setErrorText('');
    try {
      // We send a dummy 3-team alliance to trigger the selection model.
      // The selection model is event-wide so the specific teams don't affect the overall rankings.
      const topThree = teamPool.slice(0, 3).map((t) => t.team_key);
      const result = await getTheoreticalAlliance(eventKey, {
        team_keys: topThree,
        compatibility_weight: 0.5,
        pros_weight: 0.3,
        cons_weight: 0.2,
        include_selection_model: true,
        selection_rank_weight: rankWeight,
        selection_scale: scale,
        selection_captains: DEFAULT_CAPTAINS,
        selection_simulations: simulations,
        selection_rank_source: 'auto',
      });
      setSelectionModel(result.selection_model ?? null);
      if (!result.selection_model) {
        setErrorText('The selection model was not returned. The event may not have enough data.');
      }
    } catch (err) {
      setErrorText((err as Error).message || 'Selection model failed.');
      setSelectionModel(null);
    } finally {
      setLoadingModel(false);
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
      setBuilderError(`Teams not in event: ${invalid.join(', ')}`);
      return;
    }
    setLoadingBuilder(true);
    setBuilderError('');
    try {
      const result = await getTheoreticalAlliance(eventKey, {
        team_keys: keys,
        compatibility_weight: 0.5,
        pros_weight: 0.3,
        cons_weight: 0.2,
        include_selection_model: false,
        selection_rank_weight: rankWeight,
        selection_scale: scale,
        selection_captains: DEFAULT_CAPTAINS,
        selection_simulations: simulations,
        selection_rank_source: 'auto',
      });
      setBuilderResult(result);
    } catch (err) {
      setBuilderError((err as Error).message || 'Alliance analysis failed.');
      setBuilderResult(null);
    } finally {
      setLoadingBuilder(false);
    }
  }, [eventKey, builderSlots, teamKeySet, rankWeight, scale, simulations]);

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
  const surfaceGroupId = 'alliance-advisor';

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
      <SurfaceCardGroup groupId={surfaceGroupId}>
        {/* ---- Event Selection ---- */}
        <SurfaceCard
          title="Alliance Selection Advisor"
        >
          <EventPicker
            value={eventKey}
            onSelect={handleEventSelect}
            inputValue={eventInput}
            onInputChange={setEventInput}
            onSubmit={() => handleEventSelect(eventInput)}
            loading={loadingTeams}
          />

          {eventTeams ? (
            <p className={styles.eventSummary}>
              <strong>{eventTeams.event_name || eventKey}</strong> — {teamPool.length} teams loaded
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
                  {loadingModel ? 'Simulating...' : 'Run Selection Model'}
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
            title="Team Desirability Rankings"
            subtitle={`${selectionModel.top_desirability.length} teams ranked by selection desirability. ${selectionModel.simulations.toLocaleString()} Monte Carlo simulations.`}
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
          <SurfaceCard title="Alliance Builder">
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
                        #{t.team_number} {t.nickname || t.team_key}
                        {t.rating_0_100 != null ? ` (${metric(t.rating_0_100, 1)})` : ''}
                      </option>
                    ))}
                  </FieldSelect>
                ))}
              </div>

              <div className={styles.actions}>
                <Button variant="primary" onClick={() => void runBuilder()} loading={loadingBuilder}>
                  {loadingBuilder ? 'Analyzing...' : 'Analyze Alliance'}
                </Button>
              </div>

              {builderError ? (
                <p className={`${styles.note} ${styles.noteWarning}`} role="alert">{builderError}</p>
              ) : null}

              {builderResult ? (
                <div className={styles.stack}>
                  {/* Four equal figures answered four questions; the page only
                      asks one — how good is this lineup. Alliance Score takes
                      the display step with its confidence attached, and the
                      rest step down beside it. */}
                  <div className="page-hero">
                    <Stat
                      size="display"
                      label="Alliance Score"
                      value={metric(builderResult.weighted_total_score_0_100, 1)}
                      unit="/ 100"
                      confidence={builderResult.compatibility.confidence_0_1}
                    />
                    <div className="page-hero-stats">
                      <Stat
                        size="sm"
                        label="Compatibility"
                        value={metric(builderResult.compatibility.compatibility_score_0_100, 1)}
                        unit="/ 100"
                      />
                      <Stat
                        size="sm"
                        label="Synergy Points"
                        value={metric(builderResult.compatibility.alliance_synergy_points, 2)}
                      />
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
                      { key: 'compatibility_score_0_100', label: 'Compatibility', numeric: true, render: (t) => metric(t.compatibility_score_0_100, 1) },
                      { key: 'pros_score_0_100', label: 'Pros', numeric: true, render: (t) => metric(t.pros_score_0_100, 1) },
                      { key: 'cons_risk_0_100', label: 'Cons Risk', numeric: true, render: (t) => metric(t.cons_risk_0_100, 1) },
                      { key: 'weighted_score_0_100', label: 'Weighted', numeric: true, render: (t) => metric(t.weighted_score_0_100, 1) },
                    ]}
                    rows={builderResult.teams}
                    rowKey={(team) => team.team_key}
                    caption="Per-team contribution to the alliance score."
                  />

                  <Table
                    columns={[
                      {
                        key: 'pair',
                        label: 'Pair',
                        render: (pair) => (
                          <span className={styles.nowrap}>
                            {teamNumLabel(pair.team_key_a)} + {teamNumLabel(pair.team_key_b)}
                          </span>
                        ),
                      },
                      { key: 'synergy_points', label: 'Synergy', numeric: true, render: (p) => metric(p.synergy_points, 2) },
                      { key: 'base_synergy_points', label: 'Base', numeric: true, render: (p) => metric(p.base_synergy_points, 2) },
                      { key: 'complement_bonus_points', label: 'Complement', numeric: true, render: (p) => metric(p.complement_bonus_points, 2) },
                      { key: 'risk_penalty_points', label: 'Risk', numeric: true, render: (p) => metric(p.risk_penalty_points, 2) },
                      { key: 'source', label: 'Source', align: 'center', render: (p) => <Chip size="sm">{p.source}</Chip> },
                    ]}
                    rows={builderResult.compatibility.pair_breakdown}
                    rowKey={(_pair, index) => String(index)}
                    caption="How each pair of teams contributes synergy."
                  />

                  {builderResult.teams.some((t) => t.pros_top.length > 0 || t.cons_top.length > 0) ? (
                    <div className={styles.prosConsGrid}>
                      {builderResult.teams.map((team) => (
                        <div key={team.team_key} className={styles.prosConsCard}>
                          <strong className={styles.prosConsTitle}>
                            {teamLabel(team.team_key, teamPool)}
                          </strong>
                          {team.pros_top.length > 0 ? (
                            <ul className={styles.prosList}>
                              {team.pros_top.slice(0, 3).map((p, i) => (
                                <li key={i}>{p.label ?? JSON.stringify(p)}</li>
                              ))}
                            </ul>
                          ) : null}
                          {team.cons_top.length > 0 ? (
                            <ul className={styles.consList}>
                              {team.cons_top.slice(0, 3).map((c, i) => (
                                <li key={i}>{c.label ?? JSON.stringify(c)}</li>
                              ))}
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
      </SurfaceCardGroup>
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
