import { ScheduleStatus } from './ScheduleStatus';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { getEventTeamsIntel, listPitEntries, type WorkspaceMemberRecord } from '../../api';
import { EventPicker } from '../../components/EventPicker';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button } from '../../components/ui/primitives';
import { useEventKeyParam } from '../../hooks/useEventKeyParam';
import { NextAssignment } from './NextAssignment';
import { coverageSummary, myAssignments } from './teamAssignments';
import { useTeamRoom } from './useTeamRoom';
import { useWorkspace } from './useWorkspace';
import { useAssignmentBadge } from '../../hooks/useAssignmentBadge';
import { TeamCalendarLinks } from '../calendar/TeamCalendarLinks';
import { ScoutThanksNote } from '../scoutThanks/ScoutThanksNote';
import { pitProgress } from './pitProgress';
import { LeaderDashboard } from './LeaderDashboard';
import styles from './TeamScoutingCard.module.css';

const EVENT_STORAGE_KEY = 'scouting_center_event_key';

function usePitProgress(eventKey: string) {
  const [progress, setProgress] = useState<{ eventKey: string; done: number; total: number } | null>(null);
  useEffect(() => {
    if (!eventKey) return;
    let cancelled = false;
    void (async () => {
      try {
        const [teams, pit] = await Promise.all([
          getEventTeamsIntel(eventKey, { include_tba: true, include_season_fallback: false, include_rating_details: false, include_rating_signals: false }),
          listPitEntries(eventKey),
        ]);
        if (cancelled) return;
        const counted = pitProgress(
          (teams.teams ?? []).map((team) => String(team.team_key || '')).filter(Boolean),
          (pit.entries ?? []).map((entry) => entry.team_key),
        );
        setProgress({ eventKey, ...counted });
      } catch {
        if (!cancelled) setProgress(null);
      }
    })();
    return () => { cancelled = true; };
  }, [eventKey]);
  return progress && progress.eventKey === eventKey && progress.total > 0 ? progress : null;
}

// My Team's bridge into event scouting: leaders see coverage and a way to
// assign, members see what they're scouting next.
export function TeamScoutingCard({ members }: { members?: WorkspaceMemberRecord[] } = {}) {
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
  const pit = usePitProgress(eventKey);
  const eventQuery = `?event=${encodeURIComponent(eventKey)}`;
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

  const slotsReady = Boolean(snapshot && room.schedule !== null && !room.scheduleError);
  const showDashboard = room.isLeader || session?.me.role === 'leader';

  return (
    <>
    {showDashboard ? (
      <LeaderDashboard
        eventKey={eventKey}
        members={members}
        slots={slotsReady ? { open: coverage.upcomingSlots - coverage.assignedSlots, needNewScout: coverage.needsReassignment } : null}
        pit={pit}
      />
    ) : null}
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
        <nav className={styles.links} aria-label="Team scouting tools">
          {room.isLeader ? null : (<>
          <Link className={styles.linkRow} to={`/scouting/pit${eventQuery}`}>
            <span className={styles.linkName}>Pit scouting</span>
            <span className={styles.linkMeta}>{pit ? `${pit.done}/${pit.total} teams` : 'Open'}</span>
            <span className={styles.arrow} aria-hidden="true">›</span>
          </Link>
          <Link className={styles.linkRow} to={`/scouting/coverage${eventQuery}`}>
            <span className={styles.linkName}>Coverage</span>
            <span className={styles.linkMeta}>Scouting gaps</span>
            <span className={styles.arrow} aria-hidden="true">›</span>
          </Link>
          </>)}
          <Link className={styles.linkRow} to={`/compare/picklist${eventQuery}`}>
            <span className={styles.linkName}>Picklist</span>
            <span className={styles.linkMeta}>Pick order</span>
            <span className={styles.arrow} aria-hidden="true">›</span>
          </Link>
        </nav>
        {snapshot && session ? (
          <ScoutThanksNote eventKey={eventKey} me={session.me.display_name} allDone={mine.length > 0 && mine.every((item) => item.done)} />
        ) : null}
        {teamNumber ? <TeamCalendarLinks eventKey={eventKey} teamNumber={teamNumber} /> : null}
      </div>
    </SurfaceCard>
    </>
  );
}
