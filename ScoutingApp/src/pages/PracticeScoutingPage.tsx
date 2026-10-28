import { useEffect, useMemo, useState } from 'react';
import { getEventSchedule, getMatchPractice, type EventScheduleItem, type MatchPracticeResponse } from '../api';
import { EventPicker } from '../components/EventPicker';
import { LiteStreamEmbed } from '../components/LiteStreamEmbed';
import { ScoutingViewBar } from '../components/ScoutingViewBar';
import { scoutingViewTitle } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { Button, FieldRadioGroup, FieldSelect, FieldStepper, FieldToggle } from '../components/ui/primitives';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { gradePractice, practiceScore, type PracticeAnswers, type PracticeCheck } from '../features/practice/grade';
import { ENDGAME_LABELS } from './scoutingPage.helpers';
import type { EndgameMode } from './scoutingPage.types';
import styles from './PracticeScoutingPage.module.css';

const STORAGE_KEY = 'scouting_center_event_key';
const EMPTY: PracticeAnswers = { autoFuel: 0, teleopFuel: 0, autoClimb: false, endgame: 'none' };
const VERDICT_TEXT: Record<PracticeCheck['verdict'], string> = { right: 'Right', off: 'Off', plausible: 'Plausible', unknown: 'Not checked' };

function teamNumber(teamKey: string): string {
  return teamKey.replace(/^frc/i, '');
}

// Scout training: watch a robot in a match that's already been played, scout it, then see how
// the report compares with the official result. Nothing here is saved or shared: it's practice.
export function PracticeScoutingPage() {
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent } = useEventKeyParam(STORAGE_KEY);
  const [schedule, setSchedule] = useState<{ eventKey: string; matches: EventScheduleItem[] } | null>(null);
  const [scheduleFailed, setScheduleFailed] = useState(false);
  const [matchKey, setMatchKey] = useState('');
  const [teamKey, setTeamKey] = useState('');
  const [practice, setPractice] = useState<MatchPracticeResponse | null>(null);
  const [practiceFailed, setPracticeFailed] = useState(false);
  const [answers, setAnswers] = useState<PracticeAnswers>(EMPTY);
  const [checks, setChecks] = useState<PracticeCheck[] | null>(null);

  useEffect(() => {
    // A new event starts a new practice: the last event's match and robot don't carry over.
    setMatchKey('');
    setTeamKey('');
    if (!eventKey) return undefined;
    let live = true;
    setScheduleFailed(false);
    getEventSchedule(eventKey)
      .then((result) => { if (live) setSchedule({ eventKey, matches: result.matches }); })
      .catch(() => { if (live) setScheduleFailed(true); });
    return () => { live = false; };
  }, [eventKey]);

  const played = useMemo(
    () => (schedule?.eventKey === eventKey ? schedule.matches.filter((match) => match.is_completed) : []),
    [eventKey, schedule],
  );
  const match = played.find((row) => row.match_key === matchKey) ?? null;

  useEffect(() => {
    setPractice(null);
    setChecks(null);
    setAnswers(EMPTY);
    if (!matchKey || !teamKey) return undefined;
    let live = true;
    setPracticeFailed(false);
    getMatchPractice(matchKey, teamKey)
      .then((result) => { if (live) setPractice(result); })
      .catch(() => { if (live) setPracticeFailed(true); });
    return () => { live = false; };
  }, [matchKey, teamKey]);

  const set = <K extends keyof PracticeAnswers>(key: K, value: PracticeAnswers[K]) => {
    setAnswers((current) => ({ ...current, [key]: value }));
    setChecks(null);
  };
  const score = checks ? practiceScore(checks) : null;

  return (
    <>
      <ScoutingViewBar />
      <div className="center-page-container">
        <SurfaceCard
          title={scoutingViewTitle('/scouting/practice')}
          subtitle="Scout a match that's already been played, then check your report against the official result. Nothing is saved."
          mobileCollapsible={false}
        >
          <EventPicker label="Event" value={eventKey} onSelect={selectEvent} inputValue={eventInput} onInputChange={setEventInput} onSubmit={commitInput} />
          {scheduleFailed ? <p className="center-callout warning">Couldn't load this event's matches. Try again in a moment.</p> : null}
          {eventKey && schedule?.eventKey === eventKey && played.length === 0 ? (
            <p className="center-callout muted">No matches have been played at this event yet. Pick an event with results.</p>
          ) : null}
          {played.length > 0 ? (
            <div className={styles.pickers}>
              <FieldSelect label="Match" value={matchKey} onChange={(event) => { setMatchKey(event.target.value); setTeamKey(''); }}>
                <option value="">Choose a played match</option>
                {played.map((row) => <option key={row.match_key} value={row.match_key}>{row.display_name}</option>)}
              </FieldSelect>
              {match ? (
                <FieldRadioGroup
                  legend="Robot to scout"
                  name="practice-robot"
                  segmented
                  value={teamKey}
                  onValueChange={setTeamKey}
                  options={[...match.red, ...match.blue].map((team) => ({ value: team.team_key, label: teamNumber(team.team_key) }))}
                />
              ) : null}
            </div>
          ) : null}
        </SurfaceCard>

        {practiceFailed ? <p className="center-callout warning">Couldn't load that match. Try another.</p> : null}

        {practice ? (
          <SurfaceCard
            title={`Scout ${teamNumber(practice.team_key)} · ${practice.label}`}
            subtitle={`${practice.alliance === 'red' ? 'Red' : 'Blue'} alliance. Watch the robot, count, then check.`}
            mobileCollapsible={false}
          >
            {practice.video ? (
              <LiteStreamEmbed src={practice.video.embed_url} title={`Video of ${practice.label}`} className={styles.video} />
            ) : (
              <p className="center-callout muted">No video of this match is posted yet. Any recording of it works: scout from that.</p>
            )}
            {!practice.checkable ? (
              <p className="center-callout warning">This season's official results don't break down by robot, so there's nothing to check against. Pick a match from the current season.</p>
            ) : (
              <>
                <div className={styles.form}>
                  <FieldStepper label="Auto fuel scored" value={answers.autoFuel} onValueChange={(value) => set('autoFuel', value)} step={1} feedback />
                  <FieldStepper label="Teleop fuel scored" value={answers.teleopFuel} onValueChange={(value) => set('teleopFuel', value)} step={1} feedback />
                  <FieldToggle label="Climbed in auto" checked={answers.autoClimb} onCheckedChange={(value) => set('autoClimb', value)} />
                  <FieldSelect label="Endgame" value={answers.endgame} onChange={(event) => set('endgame', event.target.value as EndgameMode)}>
                    {Object.entries(ENDGAME_LABELS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
                  </FieldSelect>
                </div>
                <Button variant="primary" onClick={() => setChecks(gradePractice(answers, practice))}>Check my report</Button>
              </>
            )}
          </SurfaceCard>
        ) : null}

        {checks && score ? (
          <SurfaceCard
            title={score.checked ? `${score.right} of ${score.checked} right` : 'Checked'}
            subtitle={!score.checked
              ? 'Nothing official to check this robot against in this match. Try another match.'
              : score.right === score.checked ? 'Spot on. Try a busier match next.' : 'Watch the moments you missed again, then try another robot.'}
            mobileCollapsible={false}
          >
            <ul className={styles.checks}>
              {checks.map((check) => (
                <li key={check.id} className={`${styles.check} ${styles[check.verdict]}`}>
                  <span className={styles.checkHead}>
                    <strong>{check.label}</strong>
                    <span className={styles.verdict}>{VERDICT_TEXT[check.verdict]}</span>
                  </span>
                  <span className={styles.compare}>You: {check.yours} · Official: {check.official}</span>
                  {check.note ? <span className={styles.note}>{check.note}</span> : null}
                </li>
              ))}
            </ul>
            <Button size="sm" variant="quiet" onClick={() => { setAnswers(EMPTY); setChecks(null); }}>Try again</Button>
          </SurfaceCard>
        ) : null}
      </div>
    </>
  );
}
