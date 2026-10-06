import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { EventPicker } from '../../components/EventPicker';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button, Stat } from '../../components/ui/primitives';
import { useEventKeyParam } from '../../hooks/useEventKeyParam';
import { NextAssignment } from './NextAssignment';
import { coverageSummary, myAssignments } from './teamAssignments';
import { useTeamRoom } from './useTeamRoom';
import styles from './TeamScoutingCard.module.css';

const EVENT_STORAGE_KEY = 'scouting_center_event_key';

// My Team's bridge into event scouting: leaders see coverage and a way to
// assign, members see what they're scouting next.
export function TeamScoutingCard() {
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent } = useEventKeyParam(EVENT_STORAGE_KEY);
  const [changingEvent, setChangingEvent] = useState(false);
  const room = useTeamRoom(eventKey);
  const schedule = useMemo(() => room.schedule ?? [], [room.schedule]);
  const snapshot = room.snapshot;
  const mine = useMemo(
    () => (snapshot ? myAssignments(snapshot.me.member_id, snapshot.assignments, schedule) : []),
    [schedule, snapshot],
  );
  const coverage = useMemo(
    () => coverageSummary(snapshot?.assignments ?? [], schedule),
    [schedule, snapshot],
  );
  const assignHref = `/scouting/assignments?event=${encodeURIComponent(eventKey)}`;

  if (!eventKey || changingEvent) {
    return (
      <SurfaceCard
        title="Team scouting"
        subtitle="Pick the event your team is at. Assignments and scouting follow it."
        expandable={false}
        mobileCollapsible={false}
      >
        <EventPicker
          value={eventKey}
          onSelect={(key) => { selectEvent(key); setChangingEvent(false); }}
          inputValue={eventInput}
          onInputChange={setEventInput}
          onSubmit={() => { commitInput(); setChangingEvent(false); }}
        />
        {changingEvent ? (
          <Button size="sm" variant="quiet" onClick={() => setChangingEvent(false)}>Keep {eventKey}</Button>
        ) : null}
      </SurfaceCard>
    );
  }

  const title = `Scouting at ${room.eventName || eventKey}`;
  const loading = !snapshot && room.status === 'loading';

  return (
    <SurfaceCard
      title={title}
      subtitle={room.isLeader ? 'You can assign matches to everyone on your team.' : undefined}
      right={<Button size="sm" variant="quiet" onClick={() => setChangingEvent(true)}>Change event</Button>}
      expandable={false}
      mobileCollapsible={false}
    >
      <div className={styles.body}>
        {room.error && !snapshot ? <p className="center-callout warning" role="alert">{room.error}</p> : null}
        {room.fromSavedCopy && !room.online ? (
          <p className="center-callout muted">You're offline. This is the last copy this phone saw.</p>
        ) : null}
        {loading ? <p className={styles.note}>Opening your team's room…</p> : null}

        {snapshot && room.isLeader ? (
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

        {snapshot && (!room.isLeader || mine.length > 0) ? (
          <NextAssignment
            eventKey={eventKey}
            list={mine}
            showAllHref={mine.length > 0 ? assignHref : undefined}
          />
        ) : null}
      </div>
    </SurfaceCard>
  );
}
