import { ScheduleStatus } from './ScheduleStatus';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { EventPicker } from '../../components/EventPicker';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button, Stat } from '../../components/ui/primitives';
import { useEventKeyParam } from '../../hooks/useEventKeyParam';
import { NextAssignment } from './NextAssignment';
import { coverageSummary, myAssignments } from './teamAssignments';
import { useTeamRoom } from './useTeamRoom';
import { useWorkspace } from './useWorkspace';
import { useAssignmentBadge } from '../../hooks/useAssignmentBadge';
import { TeamCalendarLinks } from '../calendar/TeamCalendarLinks';
import { ScoutThanksNote } from '../scoutThanks/ScoutThanksNote';
import styles from './TeamScoutingCard.module.css';

const EVENT_STORAGE_KEY = 'scouting_center_event_key';

// My Team's bridge into event scouting: leaders see coverage and a way to
// assign, members see what they're scouting next.
export function TeamScoutingCard() {
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent } = useEventKeyParam(EVENT_STORAGE_KEY);
  const [changingEvent, setChangingEvent] = useState(false);
  const room = useTeamRoom(eventKey);
  const session = useWorkspace();
  const teamNumber = session?.workspace.frc_team_number ?? null;
  const schedule = useMemo(() => room.schedule ?? [], [room.schedule]);
  const snapshot = room.snapshot;
  const mine = useMemo(
    () => (snapshot ? myAssignments(snapshot.me.member_id, snapshot.assignments, schedule) : []),
    [schedule, snapshot],
  );
  // Without the schedule nothing can be matched up, which isn't the same as nothing left.
  useAssignmentBadge(snapshot && room.schedule ? mine : null);
  const coverage = useMemo(
    () => coverageSummary(snapshot?.assignments ?? [], schedule),
    [schedule, snapshot],
  );
  const assignHref = `/scouting/assignments?event=${encodeURIComponent(eventKey)}`;

  if (!eventKey || changingEvent) {
    return (
      <SurfaceCard tourId="myteam-scouting"
        title="Team scouting"
        subtitle="Pick the event your team is at. Assignments and scouting follow it."
        mobileCollapsible={false}
      >
        <EventPicker
          label="Event"
          value={eventKey}
          onSelect={(key) => { selectEvent(key); setChangingEvent(false); }}
          inputValue={eventInput}
          onInputChange={setEventInput}
          onSubmit={() => { commitInput(); setChangingEvent(false); }}
        />
        {changingEvent ? (
          <Button size="sm" variant="quiet" onClick={() => setChangingEvent(false)}>Keep this event</Button>
        ) : null}
      </SurfaceCard>
    );
  }

  const title = `Scouting at ${room.eventName || 'this event'}`;
  const loading = !snapshot && room.status === 'loading';

  return (
    <SurfaceCard tourId="myteam-scouting"
      title={title}
      subtitle={room.isLeader ? 'You can assign matches to everyone on your team.' : undefined}
      right={<Button size="sm" variant="quiet" onClick={() => setChangingEvent(true)}>Change event</Button>}
      mobileCollapsible={false}
    >
      <div className={styles.body}>
        {room.error ? <p className="center-callout warning" role="alert">{room.error}</p> : null}
        {room.fromSavedCopy && !room.online ? (
          <p className="center-callout muted">You're offline. This is the last copy this phone saw.</p>
        ) : null}
        {room.schedule === null || room.scheduleError ? <ScheduleStatus error={room.scheduleError} loading={room.scheduleLoading} retry={room.retry} /> : null}
        {loading ? <p className={styles.note}>Opening your team's room…</p> : null}

        {snapshot && room.schedule !== null && !room.scheduleError && room.isLeader ? (
          <>
            <div className={styles.stats}>
              <Stat
                label="Slots assigned"
                value={`${coverage.assignedSlots} / ${coverage.upcomingSlots}`}
                tone={coverage.upcomingSlots > 0 && coverage.assignedSlots >= coverage.upcomingSlots ? 'success' : 'default'}
                size="sm"
              />
              <Stat label="Scouts" value={snapshot.members.length} size="sm" />
              {coverage.needsReassignment > 0 ? (
                <Stat label="Need a new scout" value={coverage.needsReassignment} tone="warning" size="sm" />
              ) : null}
            </div>
            {coverage.upcomingSlots === 0 && room.schedule ? (
              <p className={styles.note}>No upcoming matches at this event right now.</p>
            ) : null}
            <Link className={`center-btn ${styles.primary}`} to={assignHref}>Assign matches</Link>
          </>
        ) : null}

        {snapshot && room.schedule !== null && !room.scheduleError && (!room.isLeader || mine.length > 0) ? (
          <NextAssignment
            eventKey={eventKey}
            list={mine}
            showAllHref={mine.length > 0 ? assignHref : undefined}
          />
        ) : null}
        {snapshot && session ? (
          <ScoutThanksNote eventKey={eventKey} me={session.me.display_name} allDone={mine.length > 0 && mine.every((item) => item.done)} />
        ) : null}
        {teamNumber ? <TeamCalendarLinks eventKey={eventKey} teamNumber={teamNumber} /> : null}
      </div>
    </SurfaceCard>
  );
}
