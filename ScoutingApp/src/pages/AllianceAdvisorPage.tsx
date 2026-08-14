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
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useMobileLayout } from '../hooks/useMobileLayout';
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

function colorForDesirability(value: number): string {
  if (value >= 80) return 'var(--color-success, #22c55e)';
  if (value >= 60) return 'var(--color-info, #3b82f6)';
  if (value >= 40) return 'var(--color-warning, #eab308)';
  return 'var(--color-muted, #94a3b8)';
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
  const isMobile = useMobileLayout();

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

  function openTeamCenter(teamKey: string) {
    const params = new URLSearchParams();
    params.set('team', teamKey.toLowerCase());
    if (eventKey) params.set('event', eventKey);
    navigate(`/team-center?${params.toString()}`);
  }

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

  return (
    <>
    <PageViewBar items={COMPARE_VIEWS} />
    <div className={`alliance-advisor-layout${isMobile ? ' mobile' : ''}`}>
      <SurfaceCardGroup groupId={surfaceGroupId}>
        {/* ---- Event Selection ---- */}
        <SurfaceCard
          title="Alliance Selection Advisor"
          subtitle="Monte Carlo alliance predictions."
          className="advisor-event-card"
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
            <p style={{ margin: '4px 0 0', fontSize: '0.84rem', color: '#a4b8c9' }}>
              <strong style={{ color: '#d4e3f0' }}>{eventTeams.event_name || eventKey}</strong> — {teamPool.length} teams loaded
              {eventTeams.teams_with_event_rating > 0
                ? ` (${eventTeams.teams_with_event_rating} with ratings)`
                : ''}
            </p>
          ) : null}

          {errorText ? <p className="center-callout warning">{errorText}</p> : null}
        </SurfaceCard>

        {/* ---- Selection Model Controls ---- */}
        {teamPool.length > 0 ? (
          <SurfaceCard
            title="Selection Model Settings"
            subtitle="Simulation parameters."
            collapsible
          >
            <div className="advisor-param-grid">
              <label className="center-stack-form">
                <span className="center-label">Simulations</span>
                <input
                  className="center-input"
                  type="number"
                  min={50}
                  max={5000}
                  step={50}
                  value={simulations}
                  onChange={(e) => setSimulations(Math.max(50, Math.min(5000, Number(e.target.value) || DEFAULT_SIMULATIONS)))}
                />
              </label>
              <label className="center-stack-form">
                <span className="center-label">Rank Weight</span>
                <input
                  className="center-input"
                  type="number"
                  min={0}
                  max={4}
                  step={0.1}
                  value={rankWeight}
                  onChange={(e) => setRankWeight(Number(e.target.value) || DEFAULT_RANK_WEIGHT)}
                />
              </label>
              <label className="center-stack-form">
                <span className="center-label">Scale</span>
                <input
                  className="center-input"
                  type="number"
                  min={1}
                  max={200}
                  step={1}
                  value={scale}
                  onChange={(e) => setScale(Number(e.target.value) || DEFAULT_SCALE)}
                />
              </label>
            </div>
            <div className="center-actions-row">
              <button
                type="button"
                className="center-btn"
                onClick={() => void runSelectionModel()}
                disabled={loadingModel || teamPool.length < 6}
              >
                {loadingModel ? 'Simulating...' : 'Run Selection Model'}
              </button>
            </div>

            {selectionModel?.notes?.length ? (
              <ul style={{ fontSize: '0.8rem', opacity: 0.7, margin: '0.5rem 0 0', paddingLeft: '1.25rem' }}>
                {selectionModel.notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            ) : null}
          </SurfaceCard>
        ) : null}

        {/* ---- Top Teams by Desirability ---- */}
        {selectionModel && selectionModel.top_desirability.length > 0 ? (
          <SurfaceCard
            title="Team Desirability Rankings"
            subtitle={`${selectionModel.top_desirability.length} teams ranked by selection desirability. ${selectionModel.simulations.toLocaleString()} Monte Carlo simulations.`}
            right={
              <span className="center-chip">
                {selectionModel.rank_source === 'tba' ? 'TBA Rankings' : 'Model Rankings'}
              </span>
            }
          >
            {/* Desktop table */}
            <div className="center-table-wrap desktop-only">
              <table className="center-table" style={{ width: '100%' }}>
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>#</th>
                    <th>Team</th>
                    <th>Rank</th>
                    <th style={{ minWidth: 80 }}>Strength</th>
                    <th style={{ minWidth: 120 }}>Desirability</th>
                    <th>R1 Pick</th>
                    <th>R2 Pick</th>
                    <th>Captain</th>
                  </tr>
                </thead>
                <tbody>
                  {selectionModel.top_desirability.map((row, idx) => (
                    <DesirabilityRow
                      key={row.team_key}
                      row={row}
                      index={idx}
                      maxDesirability={maxDesirability}
                      teams={teamPool}
                      onOpenTeam={openTeamCenter}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile card list */}
            <div className="advisor-mobile-rank-list mobile-only">
              {selectionModel.top_desirability.map((row, idx) => (
                <MobileDesirabilityCard
                  key={`m-${row.team_key}`}
                  row={row}
                  index={idx}
                  maxDesirability={maxDesirability}
                  teams={teamPool}
                  onOpenTeam={openTeamCenter}
                />
              ))}
            </div>
          </SurfaceCard>
        ) : null}

        {/* ---- Expected Alliance Board ---- */}
        {allianceBoard.length > 0 ? (
          <SurfaceCard
            title="Expected Alliance Board"
            subtitle="Predicted alliances."
          >
            <div
              className="advisor-pros-cons-grid"
              style={{ gridTemplateColumns: isMobile ? '1fr' : 'repeat(2, 1fr)' }}
            >
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
            subtitle="First-round pick probabilities by seed."
            collapsible
          >
            <div className="center-table-wrap">
              <table className="center-table" style={{ width: '100%' }}>
                <thead>
                  <tr>
                    <th>Captain (Seed)</th>
                    <th>Most Likely Picks</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(selectionModel.first_round_seed_pick_probabilities)
                    .sort(([a], [b]) => Number(a) - Number(b))
                    .map(([seed, picks]) => (
                      <tr key={seed}>
                        <td style={{ fontWeight: 600 }}>
                          Seed {seed}
                          {selectionModel.captains[Number(seed) - 1]
                            ? ` (${teamNumLabel(selectionModel.captains[Number(seed) - 1])})`
                            : ''}
                        </td>
                        <td>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                            {picks.slice(0, 5).map((pick) => (
                              <span
                                key={pick.team_key}
                                className="center-chip"
                                style={{
                                  cursor: 'pointer',
                                  fontSize: '0.8rem',
                                }}
                                onClick={() => openTeamCenter(pick.team_key)}
                                title={`Desirability: ${metric(pick.selection_desirability, 1)}`}
                              >
                                {teamNumLabel(pick.team_key)}{' '}
                                <span style={{ opacity: 0.7 }}>{pct(pick.probability_0_1, 0)}</span>
                              </span>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </SurfaceCard>
        ) : null}

        {/* ---- Alliance Builder (What-If) ---- */}
        {teamPool.length > 0 ? (
          <SurfaceCard
            title="Alliance Builder"
            subtitle="Test a specific alliance."
          >
            <div className="advisor-param-grid">
              {([0, 1, 2] as const).map((slot) => (
                <label key={slot} className="center-stack-form">
                  <span className="center-label">{slot === 0 ? 'Captain' : `Pick ${slot}`}</span>
                  <select
                    className="center-input"
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
                  </select>
                </label>
              ))}
            </div>

            <div className="center-actions-row">
              <button
                type="button"
                className="center-btn"
                onClick={() => void runBuilder()}
                disabled={loadingBuilder}
              >
                {loadingBuilder ? 'Analyzing...' : 'Analyze Alliance'}
              </button>
            </div>

            {builderError ? <p className="center-callout warning">{builderError}</p> : null}

            {builderResult ? (
              <div style={{ marginTop: '0.75rem' }}>
                {/* KPI cards */}
                <div className="center-kpi-grid">
                  <div className="center-kpi-card">
                    <span>Alliance Score</span>
                    <strong>{metric(builderResult.weighted_total_score_0_100, 1)} / 100</strong>
                  </div>
                  <div className="center-kpi-card">
                    <span>Compatibility</span>
                    <strong>{metric(builderResult.compatibility.compatibility_score_0_100, 1)} / 100</strong>
                  </div>
                  <div className="center-kpi-card">
                    <span>Synergy Points</span>
                    <strong>{metric(builderResult.compatibility.alliance_synergy_points, 2)}</strong>
                  </div>
                  <div className="center-kpi-card">
                    <span>Confidence</span>
                    <strong>{pct(builderResult.compatibility.confidence_0_1, 1)}</strong>
                  </div>
                </div>

                {/* Team breakdown */}
                <div className="center-table-wrap" style={{ marginTop: '0.5rem' }}>
                  <table className="center-table" style={{ width: '100%' }}>
                    <thead>
                      <tr>
                        <th>Team</th>
                        <th>Rating</th>
                        <th>Compatibility</th>
                        <th>Pros</th>
                        <th>Cons Risk</th>
                        <th>Weighted</th>
                      </tr>
                    </thead>
                    <tbody>
                      {builderResult.teams.map((team) => (
                        <tr key={team.team_key}>
                          <td>
                            <button
                              type="button"
                              className="center-link-btn"
                              onClick={() => openTeamCenter(team.team_key)}
                              style={{ fontWeight: 600 }}
                            >
                              {teamLabel(team.team_key, teamPool)}
                            </button>
                          </td>
                          <td>{metric(team.rating_0_100, 1)}</td>
                          <td>{metric(team.compatibility_score_0_100, 1)}</td>
                          <td>{metric(team.pros_score_0_100, 1)}</td>
                          <td>{metric(team.cons_risk_0_100, 1)}</td>
                          <td style={{ fontWeight: 600 }}>{metric(team.weighted_score_0_100, 1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Pair synergy breakdown */}
                <div className="center-table-wrap" style={{ marginTop: '0.5rem' }}>
                  <table className="center-table" style={{ width: '100%' }}>
                    <thead>
                      <tr>
                        <th>Pair</th>
                        <th>Synergy</th>
                        <th>Base</th>
                        <th>Complement</th>
                        <th>Risk</th>
                        <th>Source</th>
                      </tr>
                    </thead>
                    <tbody>
                      {builderResult.compatibility.pair_breakdown.map((pair, i) => (
                        <tr key={i}>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            {teamNumLabel(pair.team_key_a)} + {teamNumLabel(pair.team_key_b)}
                          </td>
                          <td style={{ fontWeight: 600 }}>{metric(pair.synergy_points, 2)}</td>
                          <td>{metric(pair.base_synergy_points, 2)}</td>
                          <td>{metric(pair.complement_bonus_points, 2)}</td>
                          <td>{metric(pair.risk_penalty_points, 2)}</td>
                          <td className="center-chip" style={{ fontSize: '0.75rem' }}>
                            {pair.source}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Pros / Cons */}
                {builderResult.teams.some((t) => t.pros_top.length > 0 || t.cons_top.length > 0) ? (
                  <div
                    className="advisor-pros-cons-grid"
                    style={{ gridTemplateColumns: isMobile ? '1fr' : `repeat(${builderResult.teams.length}, 1fr)` }}
                  >
                    {builderResult.teams.map((team) => (
                      <div
                        key={team.team_key}
                        className="advisor-pros-cons-card"
                      >
                        <strong style={{ fontSize: '0.85rem' }}>
                          {teamLabel(team.team_key, teamPool)}
                        </strong>
                        {team.pros_top.length > 0 ? (
                          <ul style={{ paddingLeft: '1rem', margin: '0.25rem 0', fontSize: '0.8rem' }}>
                            {team.pros_top.slice(0, 3).map((p, i) => (
                              <li key={i} style={{ color: 'var(--color-success, #22c55e)' }}>
                                {p.label ?? JSON.stringify(p)}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                        {team.cons_top.length > 0 ? (
                          <ul style={{ paddingLeft: '1rem', margin: '0.25rem 0', fontSize: '0.8rem' }}>
                            {team.cons_top.slice(0, 3).map((c, i) => (
                              <li key={i} style={{ color: 'var(--color-danger, #ef4444)' }}>
                                {c.label ?? JSON.stringify(c)}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
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

function DesirabilityRow({
  row,
  index,
  maxDesirability,
  teams,
  onOpenTeam,
}: {
  row: TheoreticalSelectionTopTeam;
  index: number;
  maxDesirability: number;
  teams: EventTeam[];
  onOpenTeam: (teamKey: string) => void;
}) {
  return (
    <tr
      style={{
        background: row.is_captain ? 'var(--surface-highlight, rgba(255,255,255,0.04))' : undefined,
      }}
    >
      <td style={{ fontWeight: 600, opacity: 0.7 }}>{index + 1}</td>
      <td>
        <button
          type="button"
          className="center-link-btn"
          onClick={() => onOpenTeam(row.team_key)}
          style={{ fontWeight: 600 }}
        >
          {teamLabel(row.team_key, teams)}
        </button>
      </td>
      <td>{row.rank}</td>
      <td>{metric(row.strength_score, 1)}</td>
      <td>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <div
            className="advisor-desirability-bar"
            style={{
              background: colorForDesirability(row.selection_desirability),
              width: barWidth(row.selection_desirability, maxDesirability),
            }}
          />
          <span className="advisor-desirability-value">
            {metric(row.selection_desirability, 1)}
          </span>
        </div>
      </td>
      <td>{pct(row.first_round_pick_probability_0_1, 0)}</td>
      <td>{pct(row.second_round_pick_probability_0_1, 0)}</td>
      <td>{row.is_captain ? '*' : '—'}</td>
    </tr>
  );
}

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
    <div className="advisor-alliance-card">
      <div className="advisor-alliance-head">
        <span className="advisor-alliance-badge">
          {allianceNum}
        </span>
        <span>Alliance {allianceNum}</span>
      </div>
      <div className="advisor-alliance-chips">
        <span
          className="center-chip"
          style={{ cursor: 'pointer', fontWeight: 600 }}
          onClick={() => onOpenTeam(captain)}
          title="Captain"
        >
          * {teamLabel(captain, teams)}
        </span>
        {picks.map((pick) => (
          <span
            key={pick}
            className="center-chip"
            style={{ cursor: 'pointer' }}
            onClick={() => onOpenTeam(pick)}
          >
            {teamLabel(pick, teams)}
          </span>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Mobile Desirability Card                                           */
/* ------------------------------------------------------------------ */

function MobileDesirabilityCard({
  row,
  index,
  maxDesirability,
  teams,
  onOpenTeam,
}: {
  row: TheoreticalSelectionTopTeam;
  index: number;
  maxDesirability: number;
  teams: EventTeam[];
  onOpenTeam: (teamKey: string) => void;
}) {
  return (
    <div
      className="advisor-mobile-rank-card"
      style={{
        background: row.is_captain
          ? 'var(--surface-highlight, rgba(255,255,255,0.04))'
          : undefined,
      }}
    >
      <div className="advisor-mobile-rank-num">{index + 1}</div>
      <div className="advisor-mobile-rank-body">
        <div className="advisor-mobile-rank-header">
          <button
            type="button"
            className="center-link-btn"
            onClick={() => onOpenTeam(row.team_key)}
            style={{ fontWeight: 600 }}
          >
            {teamLabel(row.team_key, teams)}
          </button>
          {row.is_captain && <span className="center-chip" style={{ fontSize: '0.7rem' }}>* Captain</span>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: 2 }}>
          <div
            className="advisor-desirability-bar"
            style={{
              background: colorForDesirability(row.selection_desirability),
              width: barWidth(row.selection_desirability, maxDesirability),
            }}
          />
          <span className="advisor-desirability-value">
            {metric(row.selection_desirability, 1)}
          </span>
        </div>
        <div className="advisor-mobile-rank-metrics">
          <span>Rank <strong>{row.rank}</strong></span>
          <span>Strength <strong>{metric(row.strength_score, 1)}</strong></span>
          <span>R1 Pick <strong>{pct(row.first_round_pick_probability_0_1, 0)}</strong></span>
          <span>R2 Pick <strong>{pct(row.second_round_pick_probability_0_1, 0)}</strong></span>
        </div>
      </div>
    </div>
  );
}
