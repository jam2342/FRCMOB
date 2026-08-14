import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getEventSchedule,
  getEventScheduleWithSynergy,
  getEventRatings,
  getEventTeamLiveForm,
} from '../api';
import type {
  EventScheduleItem,
  EventTeamRatingItem,
  EventTeamLiveFormEntry,
  ScheduleWithSynergyMatch,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { LiveRatingsPanel } from '../components/LiveRatingsPanel';
import { PageViewBar } from '../components/PageViewBar';
import { MATCH_HUB_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { metric, pct, normalizeTeamKeyInput, teamNumberFromTeamKey } from './centerUtils';

/* ------------------------------------------------------------------ */
/*  Constants & types                                                  */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'scouting_center_event_key';
const MY_TEAM_STORAGE = 'scouting_manual_my_team_v1';

const COMP_LEVEL_ORDER: Record<string, number> = { qm: 0, ef: 1, qf: 2, sf: 3, f: 4 };

function matchSortKey(m: { comp_level: string; set_number: number; match_number: number }): number {
  const level = COMP_LEVEL_ORDER[m.comp_level] ?? 9;
  return level * 1_000_000 + m.set_number * 1_000 + m.match_number;
}

function teamNum(teamKey: string): string {
  const n = teamNumberFromTeamKey(teamKey);
  return n ? String(n) : teamKey;
}

function derivedWinProb(redSynergy: number | null, blueSynergy: number | null): number | null {
  if (redSynergy == null || blueSynergy == null) return null;
  const diff = redSynergy - blueSynergy;
  return 1 / (1 + Math.exp(-diff / 15));
}

function averageMetric(values: Array<number | null | undefined>): number | null {
  const valid = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  if (valid.length === 0) return null;
  return valid.reduce((total, value) => total + value, 0) / valid.length;
}

function signed(value: number, digits: number = 0): string {
  if (!Number.isFinite(value)) return '0';
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
}

type TeamProfile = {
  team_key: string;
  rating: EventTeamRatingItem | null;
  form: EventTeamLiveFormEntry | null;
};

type MatchBriefing = {
  match_key: string;
  display_name: string;
  comp_level: string;
  set_number: number;
  match_number: number;
  scheduled_time: number | null;
  my_alliance: 'red' | 'blue';
  allies: TeamProfile[];        // other 2 teams on my alliance (excluding my team)
  my_profile: TeamProfile;      // my team's profile
  opponents: TeamProfile[];     // 3 opposing teams
  my_synergy: number | null;
  opp_synergy: number | null;
  win_prob: number | null;      // probability MY alliance wins
  // Tactical recommendations
  strongest_opponent: TeamProfile | null;
  weakest_opponent: TeamProfile | null;
  defense_target: TeamProfile | null;
  climb_plan: ClimbPlan[];
  offensive_focus: string;
  defensive_recommendation: string;
  strategy_why: string[];
  key_warnings: string[];
};

type ClimbPlan = {
  team_key: string;
  action: 'climb' | 'park' | 'keep_scoring';
  confidence: string;
  reason: string;
};

/* ------------------------------------------------------------------ */
/*  Briefing generation logic                                          */
/* ------------------------------------------------------------------ */

function buildBriefing(
  match: EventScheduleItem,
  synergyMatch: ScheduleWithSynergyMatch | undefined,
  myTeamKey: string,
  ratings: Map<string, EventTeamRatingItem>,
  liveForm: Map<string, EventTeamLiveFormEntry>,
): MatchBriefing | null {
  const isRed = match.red.some((t) => t.team_key === myTeamKey);
  const isBlue = match.blue.some((t) => t.team_key === myTeamKey);
  if (!isRed && !isBlue) return null;

  const myAlliance = isRed ? 'red' : 'blue';
  const myAllianceTeams = isRed ? match.red : match.blue;
  const oppAllianceTeams = isRed ? match.blue : match.red;

  function makeProfile(teamKey: string): TeamProfile {
    return {
      team_key: teamKey,
      rating: ratings.get(teamKey) ?? null,
      form: liveForm.get(teamKey) ?? null,
    };
  }

  const myProfile = makeProfile(myTeamKey);
  const allies = myAllianceTeams
    .filter((t) => t.team_key !== myTeamKey)
    .map((t) => makeProfile(t.team_key));
  const opponents = oppAllianceTeams.map((t) => makeProfile(t.team_key));

  // Synergy
  const mySyn = synergyMatch
    ? (isRed ? synergyMatch.red.synergy : synergyMatch.blue.synergy)
    : null;
  const oppSyn = synergyMatch
    ? (isRed ? synergyMatch.blue.synergy : synergyMatch.red.synergy)
    : null;
  const mySynergyScore = mySyn?.alliance_synergy_score_0_100 ?? null;
  const oppSynergyScore = oppSyn?.alliance_synergy_score_0_100 ?? null;

  // Win probability from MY alliance perspective
  const redProb = derivedWinProb(
    synergyMatch?.red.synergy.alliance_synergy_score_0_100 ?? null,
    synergyMatch?.blue.synergy.alliance_synergy_score_0_100 ?? null,
  );
  const winProb = redProb != null ? (isRed ? redProb : 1 - redProb) : null;

  // Strongest / weakest opponent by rating
  const ratedOpponents = opponents.filter((o) => o.rating != null);
  ratedOpponents.sort((a, b) => (b.rating!.rating_0_100 ?? 0) - (a.rating!.rating_0_100 ?? 0));
  const strongest = ratedOpponents[0] ?? null;
  const weakest = ratedOpponents[ratedOpponents.length - 1] ?? null;

  // Defense target — lowest-rated opponent who scores the most (highest throughput subscore)
  let defenseTarget: TeamProfile | null = null;
  if (ratedOpponents.length > 0) {
    // Target the opponent whose throughput is highest (they benefit most from being disrupted)
    const byThroughput = [...ratedOpponents].sort(
      (a, b) => (b.rating!.subscores.throughput ?? 0) - (a.rating!.subscores.throughput ?? 0),
    );
    defenseTarget = byThroughput[0] ?? null;
    // But if the strongest opponent is also the highest throughput, that's the clear target
    if (strongest && strongest.rating!.subscores.throughput >= (defenseTarget?.rating?.subscores.throughput ?? 0)) {
      defenseTarget = strongest;
    }
  }

  // Climb plan
  const climbPlan: ClimbPlan[] = [];
  const allAlliance = [myProfile, ...allies];
  const allProfiles = [...allAlliance, ...opponents];
  const myThroughputAvg = averageMetric(allAlliance.map((profile) => profile.rating?.subscores.throughput ?? null));
  const oppThroughputAvg = averageMetric(opponents.map((profile) => profile.rating?.subscores.throughput ?? null));
  const myEndgameAvg = averageMetric(allAlliance.map((profile) => profile.rating?.subscores.endgame ?? null));
  const oppEndgameAvg = averageMetric(opponents.map((profile) => profile.rating?.subscores.endgame ?? null));
  const myAutoAvg = averageMetric(allAlliance.map((profile) => profile.rating?.subscores.auto_contribution ?? null));
  const oppAutoAvg = averageMetric(opponents.map((profile) => profile.rating?.subscores.auto_contribution ?? null));
  const throughputDiff = myThroughputAvg != null && oppThroughputAvg != null ? myThroughputAvg - oppThroughputAvg : null;
  const endgameDiff = myEndgameAvg != null && oppEndgameAvg != null ? myEndgameAvg - oppEndgameAvg : null;
  const autoDiff = myAutoAvg != null && oppAutoAvg != null ? myAutoAvg - oppAutoAvg : null;
  const synergyDiff = mySynergyScore != null && oppSynergyScore != null ? mySynergyScore - oppSynergyScore : null;
  const myDefensePresence = myProfile.rating?.subscores.defense_presence ?? null;
  for (const profile of allAlliance) {
    const endgameScore = profile.rating?.subscores.endgame ?? 50;
    if (endgameScore >= 70) {
      climbPlan.push({
        team_key: profile.team_key,
        action: 'climb',
        confidence: 'High',
        reason: `Endgame subscore ${metric(endgameScore, 0)}/100`,
      });
    } else if (endgameScore >= 40) {
      climbPlan.push({
        team_key: profile.team_key,
        action: 'climb',
        confidence: 'Medium',
        reason: `Endgame subscore ${metric(endgameScore, 0)}/100 — attempt climb if time allows`,
      });
    } else {
      climbPlan.push({
        team_key: profile.team_key,
        action: 'keep_scoring',
        confidence: 'Low',
        reason: `Endgame subscore ${metric(endgameScore, 0)}/100 — better to keep scoring`,
      });
    }
  }

  // Offensive focus
  let offensiveFocus = 'Run controlled cycles and keep possession clean through traffic.';
  if (throughputDiff != null && throughputDiff >= 8) {
    offensiveFocus = `Lean into pace: projected throughput edge is ${signed(throughputDiff, 0)} (${metric(myThroughputAvg, 0)} vs ${metric(oppThroughputAvg, 0)}). Extra clean possessions should compound if you avoid foul-risk routes.`;
  } else if (throughputDiff != null && throughputDiff <= -8) {
    offensiveFocus = `Prioritize efficiency: projected throughput trails ${signed(throughputDiff, 0)} (${metric(myThroughputAvg, 0)} vs ${metric(oppThroughputAvg, 0)}). Shorter routes and fewer dead trips are the best way to stay in range.`;
  } else if (endgameDiff != null && endgameDiff >= 8) {
    offensiveFocus = `Play for a close teleop, then convert late. Endgame profile edge is ${signed(endgameDiff, 0)} (${metric(myEndgameAvg, 0)} vs ${metric(oppEndgameAvg, 0)}).`;
  } else if (autoDiff != null && autoDiff >= 8) {
    offensiveFocus = `Front-load scoring in auto. Auto contribution edge is ${signed(autoDiff, 0)} (${metric(myAutoAvg, 0)} vs ${metric(oppAutoAvg, 0)}), so early separation reduces pressure later.`;
  }
  if (winProb != null) {
    if (winProb >= 0.7) {
      offensiveFocus += ` Win model is ${pct(winProb, 0)}, so disciplined execution has better value than high-variance gambles.`;
    } else if (winProb <= 0.35) {
      offensiveFocus += ` Win model is ${pct(winProb, 0)}, so you likely need higher tempo and one additional scoring swing to flip the match.`;
    } else {
      offensiveFocus += ` Win model is ${pct(winProb, 0)}, so early-cycle efficiency and endgame timing are likely the deciding factors.`;
    }
  }

  // Defensive recommendation
  let defensiveRec = 'Start offense-first and use short disruption windows only if opponents chain uncontested cycles.';
  if (defenseTarget && defenseTarget.rating) {
    const targetThroughput = defenseTarget.rating.subscores.throughput;
    const targetVsAlliance = oppThroughputAvg != null ? targetThroughput - oppThroughputAvg : null;
    if (targetThroughput >= 72) {
      defensiveRec = `Target selective defense on ${teamNum(defenseTarget.team_key)} during lane transitions. They project ${metric(targetThroughput, 0)}/100 throughput`;
      if (targetVsAlliance != null) {
        defensiveRec += ` (${signed(targetVsAlliance, 0)} vs their alliance average)`;
      }
      defensiveRec += ', so breaking a few high-value cycles can swing teleop pace.';
      if (myDefensePresence != null && myDefensePresence >= 60) {
        defensiveRec += ` Your defense presence is ${metric(myDefensePresence, 0)}/100, so you can absorb this role without fully sacrificing output.`;
      } else if (myDefensePresence != null && myDefensePresence <= 40) {
        defensiveRec += ` Your defense presence is ${metric(myDefensePresence, 0)}/100, so keep stints short and return to scoring quickly.`;
      }
    } else if (targetThroughput >= 60) {
      defensiveRec = `Use situational pressure on ${teamNum(defenseTarget.team_key)} after securing your first cycles. Their throughput is ${metric(targetThroughput, 0)}/100; full-time defense is likely lower value than maintaining your own scoring cadence.`;
    } else {
      defensiveRec = 'Opponent throughput profile is balanced; prioritize clean scoring and avoid overcommitting a robot to defense.';
    }
  } else if (oppThroughputAvg != null && oppThroughputAvg < 58) {
    defensiveRec = `Opponent average throughput projects at ${metric(oppThroughputAvg, 0)}/100. Defense should be low priority unless match flow changes.`;
  }

  const strategyWhy: string[] = [];
  if (synergyDiff != null) {
    strategyWhy.push(`Synergy differential: ${signed(synergyDiff, 0)} (${metric(mySynergyScore, 0)} vs ${metric(oppSynergyScore, 0)}).`);
  }
  if (throughputDiff != null) {
    strategyWhy.push(`Throughput projection: ${signed(throughputDiff, 0)} (${metric(myThroughputAvg, 0)} vs ${metric(oppThroughputAvg, 0)}).`);
  }
  if (endgameDiff != null) {
    strategyWhy.push(`Endgame differential: ${signed(endgameDiff, 0)} (${metric(myEndgameAvg, 0)} vs ${metric(oppEndgameAvg, 0)}).`);
  }
  if (autoDiff != null) {
    strategyWhy.push(`Auto contribution differential: ${signed(autoDiff, 0)} (${metric(myAutoAvg, 0)} vs ${metric(oppAutoAvg, 0)}).`);
  }
  if (winProb != null) {
    strategyWhy.push(`Model win probability for your alliance: ${pct(winProb, 0)}.`);
  }
  const ratedCount = allProfiles.filter((profile) => profile.rating != null).length;
  if (ratedCount < allProfiles.length) {
    const missing = allProfiles.length - ratedCount;
    strategyWhy.push(
      `${missing} of ${allProfiles.length} robots have limited rating coverage, so plan for wider-than-normal performance variance.`,
    );
  }

  // Warnings
  const warnings: string[] = [];
  // Check for cold form opponents (could be underrated)
  for (const opp of opponents) {
    if (opp.form?.in_form_label === 'in_form') {
      warnings.push(`${teamNum(opp.team_key)} is in hot form (${opp.form.recent_form.join('')}) and may outperform baseline projections.`);
    }
  }
  // Check if any ally has low confidence rating
  for (const ally of allies) {
    if (ally.rating && ally.rating.confidence_0_1 < 0.3) {
      warnings.push(
        `Limited data on ally ${teamNum(ally.team_key)} — rating confidence is ${(ally.rating.confidence_0_1 * 100).toFixed(0)}%, so role assumptions should stay flexible.`,
      );
    }
  }
  // Check for penalty-prone opponents
  for (const opp of opponents) {
    const pd = opp.rating?.subscores.penalty_discipline;
    if (pd != null && pd <= 30) {
      warnings.push(`${teamNum(opp.team_key)} is foul-prone (discipline ${metric(pd, 0)}/100). Keep pressure on legal lanes and avoid protected-zone contacts.`);
    }
  }
  if (strongest?.rating && myProfile.rating) {
    const ratingGap = strongest.rating.rating_0_100 - myProfile.rating.rating_0_100;
    if (ratingGap >= 12) {
      warnings.push(
        `Top opponent ${teamNum(strongest.team_key)} is rated ${signed(ratingGap, 0)} above your robot (${metric(strongest.rating.rating_0_100, 0)} vs ${metric(myProfile.rating.rating_0_100, 0)}). Avoid isolated pace races.`,
      );
    }
  }
  if (synergyDiff != null && synergyDiff <= -8) {
    warnings.push(`Opponent synergy edge is ${signed(synergyDiff, 0)}. Missed handoffs or blocked lanes are more costly than usual in this matchup.`);
  }
  if (endgameDiff != null && endgameDiff <= -10) {
    warnings.push(`Endgame projection trails ${signed(endgameDiff, 0)}. Bank points earlier and start setup sooner than normal.`);
  }

  return {
    match_key: match.match_key,
    display_name: match.display_name || match.match_key,
    comp_level: match.comp_level,
    set_number: match.set_number,
    match_number: match.match_number,
    scheduled_time: match.scheduled_time ?? null,
    my_alliance: myAlliance,
    allies,
    my_profile: myProfile,
    opponents,
    my_synergy: mySynergyScore,
    opp_synergy: oppSynergyScore,
    win_prob: winProb,
    strongest_opponent: strongest,
    weakest_opponent: weakest,
    defense_target: defenseTarget,
    climb_plan: climbPlan,
    offensive_focus: offensiveFocus,
    defensive_recommendation: defensiveRec,
    strategy_why: strategyWhy,
    key_warnings: warnings,
  };
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function StrategyBriefingPage() {
  const navigate = useNavigate();

  const { eventKey, eventInput, setEventInput, commitInput, selectEvent, fetchTrigger } = useEventKeyParam(STORAGE_KEY);
  const [eventName, setEventName] = useState('');
  const [myTeamKey, setMyTeamKey] = useState(() => {
    try { return localStorage.getItem(MY_TEAM_STORAGE) || ''; } catch { return ''; }
  });
  const [myTeamInput, setMyTeamInput] = useState(() => {
    try {
      const stored = localStorage.getItem(MY_TEAM_STORAGE) || '';
      const num = teamNumberFromTeamKey(stored);
      return num ? String(num) : '';
    } catch {
      return '';
    }
  });
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState('');

  const [schedule, setSchedule] = useState<EventScheduleItem[] | null>(null);
  const [synergyMatches, setSynergyMatches] = useState<ScheduleWithSynergyMatch[]>([]);
  const [ratings, setRatings] = useState<EventTeamRatingItem[]>([]);
  const [liveFormStatuses, setLiveFormStatuses] = useState<Record<string, EventTeamLiveFormEntry>>({});

  const [expandedMatch, setExpandedMatch] = useState<string | null>(null);

  // Persist my team
  useEffect(() => {
    if (myTeamKey) localStorage.setItem(MY_TEAM_STORAGE, myTeamKey);
  }, [myTeamKey]);

  // Fetch all data
  const fetchData = useCallback(async (key: string) => {
    if (!key) return;
    setLoading(true);
    setErrorText('');

    const results = await Promise.allSettled([
      getEventSchedule(key),
      getEventScheduleWithSynergy(key, { include_pair_breakdown: false }),
      getEventRatings(key),
      getEventTeamLiveForm(key),
    ]);

    if (results[0].status === 'fulfilled') {
      setSchedule(results[0].value.matches);
      setEventName(results[0].value.event_name || key);
    } else {
      setErrorText((results[0].reason as Error)?.message || 'Failed to load schedule.');
    }

    if (results[1].status === 'fulfilled') {
      setSynergyMatches(results[1].value.matches);
    }

    if (results[2].status === 'fulfilled') {
      setRatings(results[2].value.ratings);
    }

    if (results[3].status === 'fulfilled') {
      setLiveFormStatuses(results[3].value.team_statuses ?? {});
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

  // Maps for quick lookup
  const ratingsMap = useMemo(() => {
    const map = new Map<string, EventTeamRatingItem>();
    for (const r of ratings) map.set(r.team_key, r);
    return map;
  }, [ratings]);

  const liveFormMap = useMemo(() => {
    const map = new Map<string, EventTeamLiveFormEntry>();
    for (const [key, f] of Object.entries(liveFormStatuses)) map.set(key, f);
    return map;
  }, [liveFormStatuses]);

  const synergyMap = useMemo(() => {
    const map = new Map<string, ScheduleWithSynergyMatch>();
    for (const m of synergyMatches) map.set(m.match_key, m);
    return map;
  }, [synergyMatches]);

  // Build briefings
  const briefings = useMemo<MatchBriefing[]>(() => {
    if (!schedule || !myTeamKey) return [];
    return schedule
      .filter((m) => !m.is_completed)
      .map((m) => buildBriefing(m, synergyMap.get(m.match_key), myTeamKey, ratingsMap, liveFormMap))
      .filter((b): b is MatchBriefing => b !== null)
      .sort((a, b) => matchSortKey(a) - matchSortKey(b));
  }, [schedule, myTeamKey, synergyMap, ratingsMap, liveFormMap]);

  function commitMyTeam() {
    const normalized = normalizeTeamKeyInput(myTeamInput.trim());
    if (!normalized) return;
    setMyTeamKey(normalized);
    const teamNumber = teamNumberFromTeamKey(normalized);
    if (teamNumber !== null) {
      setMyTeamInput(String(teamNumber));
    }
  }

  function openTeamCenter(teamKey: string) {
    const params = new URLSearchParams();
    params.set('team', teamKey);
    if (eventKey) params.set('event', eventKey);
    navigate(`/team-center?${params.toString()}`);
  }

  const surfaceGroupId = 'strategy-briefing';

  return (
    <>
    <PageViewBar items={MATCH_HUB_VIEWS} />
    <div className="center-page-container">
      <SurfaceCardGroup groupId={surfaceGroupId}>
        {/* ---- Event + Team Selection ---- */}
        <SurfaceCard
          title="Match Strategy Briefing"
          subtitle="Tactical match briefings."
          className="no-print"
        >
          <EventPicker
            value={eventKey}
            onSelect={selectEvent}
            inputValue={eventInput}
            onInputChange={setEventInput}
            onSubmit={commitInput}
            loading={loading}
          />

          {eventName && schedule ? (
            <p className="center-event-status">
              <strong>{eventName}</strong> — {schedule.filter((m) => !m.is_completed).length} upcoming matches
            </p>
          ) : null}

          {errorText ? <p className="center-callout warning">{errorText}</p> : null}

          {/* My Team Input */}
          <div className="center-input-row" style={{ marginTop: '0.75rem' }}>
            <input
              className="center-input"
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              placeholder="Your team number"
              value={myTeamInput}
              onChange={(e) => setMyTeamInput(e.target.value.replace(/\D/g, ''))}
              onKeyDown={(e) => e.key === 'Enter' && commitMyTeam()}
              style={{ flex: 1, maxWidth: 200 }}
            />
            <button type="button" className="center-btn" onClick={commitMyTeam}>
              Set My Team
            </button>
            {myTeamKey ? (
              <span className="center-chip" style={{ fontWeight: 600 }}>
                Team {teamNum(myTeamKey)}
              </span>
            ) : null}
          </div>

          {!myTeamKey && schedule ? (
            <p className="center-callout muted" style={{ marginTop: '0.5rem' }}>
              Enter your team number to generate briefings.
            </p>
          ) : null}
        </SurfaceCard>

        {/* ---- Live ratings board ---- */}
        {eventKey ? (
          <SurfaceCard
            title="Live Ratings"
            subtitle="Updates automatically as new match analysis lands."
            className="no-print"
          >
            <LiveRatingsPanel eventKey={eventKey} title="Event ratings — live" />
          </SurfaceCard>
        ) : null}

        {/* ---- Briefing Overview ---- */}
        {briefings.length > 0 ? (
          <SurfaceCard
            title="Upcoming Match Briefings"
            subtitle={`${briefings.length} matches with tactical analysis for Team ${teamNum(myTeamKey)}.`}
            right={
              <span style={{ display: 'inline-flex', gap: '0.5rem', alignItems: 'center' }}>
                <button
                  type="button"
                  className="center-btn ghost no-print"
                  onClick={() => window.print()}
                  title="Print or save the briefings as a PDF for the drive team clipboard"
                >
                  Print / PDF
                </button>
                <span className="center-chip">
                  {ratings.length > 0 ? `${ratings.length} rated teams` : 'No ratings'}
                </span>
              </span>
            }
          >
            <div className="briefing-kpi-overview">
              <div className="center-kpi-grid">
                <div className="center-kpi-card">
                  <span>Matches Left</span>
                  <strong>{briefings.length}</strong>
                </div>
                <div className={`center-kpi-card ${(ratingsMap.get(myTeamKey)?.rating_0_100 ?? 50) >= 70 ? 'tone-green' : ''}`}>
                  <span>My Rating</span>
                  <strong>{metric(ratingsMap.get(myTeamKey)?.rating_0_100 ?? null, 1)}</strong>
                </div>
                <div className="center-kpi-card">
                  <span>Avg Win Prob</span>
                  <strong>
                    {(() => {
                      const probs = briefings.filter((b) => b.win_prob != null).map((b) => b.win_prob!);
                      return probs.length > 0
                        ? pct(probs.reduce((a, b) => a + b, 0) / probs.length, 0)
                        : 'N/A';
                    })()}
                  </strong>
                </div>
                <div className="center-kpi-card">
                  <span>My Form</span>
                  <strong>
                    {liveFormMap.get(myTeamKey)?.recent_form.join('') || '—'}
                  </strong>
                </div>
              </div>
            </div>
          </SurfaceCard>
        ) : null}

        {/* ---- Individual Match Briefings ---- */}
        {briefings.map((briefing) => {
          const isExpanded = expandedMatch === briefing.match_key;
          const winProbPct = briefing.win_prob != null ? (briefing.win_prob * 100).toFixed(0) : null;
          const favoredClass =
            briefing.win_prob != null
              ? briefing.win_prob >= 0.6
                ? 'tone-green'
                : briefing.win_prob >= 0.4
                  ? 'tone-yellow'
                  : 'tone-red'
              : '';

          return (
            <SurfaceCard
              key={briefing.match_key}
              title={briefing.display_name}
              subtitle={`${briefing.my_alliance === 'red' ? 'Red' : 'Blue'} Alliance · ${winProbPct ? `${winProbPct}% win probability` : 'No prediction data'}`}
              className={`briefing-match-card ${favoredClass}`}
              right={
                <button
                  type="button"
                  className="center-chip clickable"
                  onClick={() => setExpandedMatch(isExpanded ? null : briefing.match_key)}
                >
                  {isExpanded ? 'Collapse' : 'Details'}
                </button>
              }
            >
              {/* Win probability bar */}
              {briefing.win_prob != null ? (
                <div className="briefing-win-bar-wrap">
                  <div className="briefing-win-bar">
                    <div
                      className={`briefing-win-fill ${briefing.my_alliance}`}
                      style={{ width: `${briefing.win_prob * 100}%` }}
                    />
                  </div>
                  <div className="briefing-win-labels">
                    <span className={briefing.my_alliance === 'red' ? 'text-red' : 'text-blue'}>
                      You {winProbPct}%
                    </span>
                    <span className={briefing.my_alliance === 'red' ? 'text-blue' : 'text-red'}>
                      Opp {(100 - Number(winProbPct))}%
                    </span>
                  </div>
                </div>
              ) : null}

              {/* Alliance overview - compact */}
              <div className="briefing-alliances">
                <div className="briefing-alliance-col">
                  <div className={`briefing-alliance-label ${briefing.my_alliance}`}>
                    Your Alliance ({briefing.my_alliance === 'red' ? 'Red' : 'Blue'})
                  </div>
                  <div className="briefing-team-chips">
                    <button
                      type="button"
                      className="center-chip clickable"
                      onClick={() => openTeamCenter(briefing.my_profile.team_key)}
                      style={{ fontWeight: 700 }}
                    >
                      {teamNum(briefing.my_profile.team_key)}
                      {briefing.my_profile.rating ? ` (${metric(briefing.my_profile.rating.rating_0_100, 0)})` : ''}
                    </button>
                    {briefing.allies.map((ally) => (
                      <button
                        type="button"
                        key={ally.team_key}
                        className="center-chip clickable"
                        onClick={() => openTeamCenter(ally.team_key)}
                      >
                        {teamNum(ally.team_key)}
                        {ally.rating ? ` (${metric(ally.rating.rating_0_100, 0)})` : ''}
                      </button>
                    ))}
                  </div>
                  {briefing.my_synergy != null ? (
                    <div className="briefing-synergy-label">
                      Synergy: <strong>{metric(briefing.my_synergy, 0)}</strong>/100
                    </div>
                  ) : null}
                </div>
                <div className="briefing-alliance-col">
                  <div className={`briefing-alliance-label ${briefing.my_alliance === 'red' ? 'blue' : 'red'}`}>
                    Opponent ({briefing.my_alliance === 'red' ? 'Blue' : 'Red'})
                  </div>
                  <div className="briefing-team-chips">
                    {briefing.opponents.map((opp) => (
                      <button
                        type="button"
                        key={opp.team_key}
                        className="center-chip clickable"
                        onClick={() => openTeamCenter(opp.team_key)}
                      >
                        {teamNum(opp.team_key)}
                        {opp.rating ? ` (${metric(opp.rating.rating_0_100, 0)})` : ''}
                      </button>
                    ))}
                  </div>
                  {briefing.opp_synergy != null ? (
                    <div className="briefing-synergy-label">
                      Synergy: <strong>{metric(briefing.opp_synergy, 0)}</strong>/100
                    </div>
                  ) : null}
                </div>
              </div>

              {/* Tactical recommendations - always visible  */}
              <div className="briefing-tactics">
                <div className="briefing-tactic-item">
                  <span className="briefing-tactic-icon">&#x1F3AF;</span>
                  <div>
                    <strong>Offense</strong>
                    <p>{briefing.offensive_focus}</p>
                  </div>
                </div>
                <div className="briefing-tactic-item">
                  <span className="briefing-tactic-icon">&#x1F6E1;</span>
                  <div>
                    <strong>Defense</strong>
                    <p>{briefing.defensive_recommendation}</p>
                  </div>
                </div>
              </div>

              {briefing.strategy_why.length > 0 ? (
                <div className="briefing-rationale">
                  <strong>Why This Call</strong>
                  <div className="briefing-rationale-list">
                    {briefing.strategy_why.map((reason, index) => (
                      <p key={`briefing-why-${briefing.match_key}-${index}`}>{reason}</p>
                    ))}
                  </div>
                </div>
              ) : null}

              {/* Warnings */}
              {briefing.key_warnings.length > 0 ? (
                <div className="briefing-warnings">
                  {briefing.key_warnings.map((w, i) => (
                    <p key={i} className="center-callout warning" style={{ margin: '2px 0', fontSize: '0.8rem' }}>
                      {w}
                    </p>
                  ))}
                </div>
              ) : null}

              {/* Expanded details */}
              {isExpanded ? (
                <div className="briefing-expanded">
                  {/* Opponent deep dive */}
                  <div className="briefing-section">
                    <h4>Opponent Breakdown</h4>
                    <div className="briefing-opponent-cards">
                      {briefing.opponents.map((opp) => (
                        <div key={opp.team_key} className="briefing-opponent-card">
                          <div className="briefing-opponent-head">
                            <button
                              type="button"
                              className="center-link-btn"
                              onClick={() => openTeamCenter(opp.team_key)}
                              style={{ fontWeight: 600 }}
                            >
                              {opp.rating?.nickname || teamNum(opp.team_key)}
                            </button>
                            {opp.form ? (
                              <span className={`center-chip freshness ${opp.form.in_form_label === 'in_form' ? 'fresh' : opp.form.in_form_label === 'cold' ? 'stale' : ''}`}>
                                {opp.form.recent_form.join('')}
                              </span>
                            ) : null}
                          </div>
                          {opp.rating ? (
                            <div className="briefing-opponent-metrics">
                              <span>Rating <strong>{metric(opp.rating.rating_0_100, 0)}</strong></span>
                              <span>Throughput <strong>{metric(opp.rating.subscores.throughput, 0)}</strong></span>
                              <span>Endgame <strong>{metric(opp.rating.subscores.endgame, 0)}</strong></span>
                              <span>Auto <strong>{metric(opp.rating.subscores.auto_contribution ?? null, 0)}</strong></span>
                              <span>Consistency <strong>{metric(opp.rating.subscores.consistency, 0)}</strong></span>
                              <span>Defense <strong>{metric(opp.rating.subscores.defense_presence ?? null, 0)}</strong></span>
                            </div>
                          ) : (
                            <p className="text-muted text-sm">No rating data available.</p>
                          )}
                          {opp.rating?.cons && opp.rating.cons.length > 0 ? (
                            <div className="briefing-signal-list">
                              <span className="text-sm" style={{ color: '#ef4444' }}>Weaknesses:</span>
                              {opp.rating.cons.slice(0, 3).map((c, i) => (
                                <span key={i} className="text-sm"> {c.label}</span>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Endgame plan */}
                  <div className="briefing-section">
                    <h4>Endgame Plan</h4>
                    <div className="briefing-climb-plan">
                      {briefing.climb_plan.map((plan) => (
                        <div key={plan.team_key} className="briefing-climb-row">
                          <span className="briefing-climb-team">{teamNum(plan.team_key)}</span>
                          <span className={`center-chip ${plan.action === 'climb' ? (plan.confidence === 'High' ? 'tone-green' : 'tone-yellow') : 'tone-red'}`}>
                            {plan.action === 'climb' ? 'Climb' : plan.action === 'park' ? 'Park' : 'Keep Scoring'}
                          </span>
                          <span className="text-sm text-muted">{plan.reason}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Alliance strengths */}
                  <div className="briefing-section">
                    <h4>Your Alliance Strengths</h4>
                    <div className="briefing-signal-list">
                      {[briefing.my_profile, ...briefing.allies].map((ally) =>
                        ally.rating?.pros?.slice(0, 2).map((p, i) => (
                          <span key={`${ally.team_key}-pro-${i}`} className="center-chip" style={{ fontSize: '0.78rem' }}>
                            {teamNum(ally.team_key)}: {p.label}
                          </span>
                        )),
                      )}
                    </div>
                  </div>
                </div>
              ) : null}
            </SurfaceCard>
          );
        })}

        {/* Empty state */}
        {myTeamKey && schedule && briefings.length === 0 ? (
          <SurfaceCard title="No Upcoming Matches">
            <p className="center-callout muted">
              Team {teamNum(myTeamKey)} has no upcoming matches, or all are completed.
            </p>
          </SurfaceCard>
        ) : null}
      </SurfaceCardGroup>
    </div>
    </>
  );
}
