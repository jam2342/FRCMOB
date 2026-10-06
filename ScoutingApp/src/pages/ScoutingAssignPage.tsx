import { useMemo, useState } from 'react';
import type { EventScheduleItem, TeamRoomAssignment, TeamRoomAssignmentChange, TeamRoomMember } from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { SCOUTING_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { Button, Chip, FieldCheckbox, Modal, Stat } from '../components/ui/primitives';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { NextAssignment } from '../features/workspace/NextAssignment';
import {
  assignmentIndex,
  clearUpcomingChanges,
  coverageSummary,
  matchSlots,
  myAssignments,
  planAutoAssign,
  slotKey,
  sortSchedule,
  teamNumber,
  workloadByMember,
  type AutoAssignMode,
} from '../features/workspace/teamAssignments';
import { useTeamRoom } from '../features/workspace/useTeamRoom';
import { WorkspaceGate } from '../features/workspace/WorkspaceGate';
import styles from './ScoutingAssignPage.module.css';

const STORAGE_KEY_EVENT = 'scouting_center_event_key';
const SCOUT_COLOR_COUNT = 10;

type CellState = { saving: boolean; target: number | null; error?: string };

type Pending =
  | { kind: 'auto'; mode: AutoAssignMode }
  | { kind: 'clear' };

/* Ten identity colours, cycled by roster position, as classes so the fill and
   the ink on it are decided together in the stylesheet. */
function scoutColorClass(memberId: number, members: TeamRoomMember[]): string {
  const idx = members.findIndex((member) => member.member_id === memberId);
  const slot = (idx < 0 ? 0 : idx % SCOUT_COLOR_COUNT) + 1;
  return styles[`scout${slot}`];
}

function SlotSelect({
  match,
  teamKey,
  row,
  members,
  busyInMatch,
  workload,
  state,
  editable,
  onChange,
}: {
  match: EventScheduleItem;
  teamKey: string;
  row: TeamRoomAssignment | undefined;
  members: TeamRoomMember[];
  busyInMatch: Set<number>;
  workload: Map<number, number>;
  state: CellState | undefined;
  editable: boolean;
  onChange: (memberId: number | null) => void;
}) {
  const removed = row && !row.member_active ? row : null;
  const serverValue = row && row.member_active && row.assigned_member_id !== null ? row.assigned_member_id : null;
  const shown = state?.saving ? state.target : serverValue;
  const value = shown !== null ? String(shown) : removed ? 'removed' : '';
  const label = `Scout for team ${teamNumber(teamKey)} in ${match.display_name || match.match_key}`;
  return (
    <select
      className={[
        'assign-cell-select',
        shown !== null ? 'assigned' : '',
        shown !== null ? scoutColorClass(shown, members) : '',
        removed && shown === null ? styles.needsScout : '',
      ].filter(Boolean).join(' ')}
      value={value}
      disabled={!editable || Boolean(state?.saving)}
      aria-label={label}
      aria-busy={state?.saving ? true : undefined}
      onChange={(event) => {
        const next = event.target.value;
        if (next === 'removed') return;
        onChange(next ? Number(next) : null);
      }}
    >
      <option value="">—</option>
      {removed ? <option value="removed" disabled>Needs a scout ({removed.assigned_display_name} left)</option> : null}
      {members.map((member) => {
        // Someone already on another team in this match can't take a second one.
        const busy = busyInMatch.has(member.member_id) && member.member_id !== shown;
        return (
          <option key={member.member_id} value={member.member_id} disabled={busy}>
            {member.display_name} · {workload.get(member.member_id) ?? 0}{busy ? ' (busy this match)' : ''}
          </option>
        );
      })}
    </select>
  );
}

function CellStatus({ state, onRetry }: { state: CellState | undefined; onRetry: () => void }) {
  if (!state) return null;
  if (state.saving) return <span className={styles.cellNote} role="status">Saving…</span>;
  if (state.error) {
    return (
      <span className={styles.cellError} role="alert">
        Not saved.{' '}
        <button type="button" className={styles.retry} onClick={onRetry}>Try again</button>
      </span>
    );
  }
  return null;
}

function ScoutingAssignWorkspacePage() {
  const isMobile = useMobileLayout();
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent } = useEventKeyParam(STORAGE_KEY_EVENT);
  const room = useTeamRoom(eventKey);
  const [showCompleted, setShowCompleted] = useState(false);
  const [skipped, setSkipped] = useState<Set<number>>(() => new Set());
  const [cells, setCells] = useState<Record<string, CellState>>({});
  const [pending, setPending] = useState<Pending | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'muted' | 'warning'; text: string } | null>(null);

  const snapshot = room.snapshot;
  const members = useMemo(() => snapshot?.members ?? [], [snapshot]);
  const assignments = useMemo(() => snapshot?.assignments ?? [], [snapshot]);
  const schedule = useMemo(() => sortSchedule(room.schedule ?? []), [room.schedule]);
  const index = useMemo(() => assignmentIndex(assignments), [assignments]);
  const workload = useMemo(() => workloadByMember(assignments, schedule), [assignments, schedule]);
  const coverage = useMemo(() => coverageSummary(assignments, schedule), [assignments, schedule]);
  const visibleMatches = useMemo(
    () => (showCompleted ? schedule : schedule.filter((match) => !match.is_completed)),
    [schedule, showCompleted],
  );
  const mine = useMemo(
    () => (snapshot ? myAssignments(snapshot.me.member_id, assignments, schedule) : []),
    [assignments, schedule, snapshot],
  );
  const canEdit = room.isLeader && room.online;
  const autoMembers = members.filter((member) => !skipped.has(member.member_id)).map((member) => member.member_id);
  const pendingChanges = useMemo<TeamRoomAssignmentChange[]>(() => {
    if (!pending) return [];
    if (pending.kind === 'clear') return clearUpcomingChanges(schedule, assignments);
    return planAutoAssign(schedule, assignments, autoMembers, pending.mode);
    // autoMembers is derived from members + skipped.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, schedule, assignments, members, skipped]);

  const busyByMatch = (match: EventScheduleItem): Set<number> => {
    const busy = new Set<number>();
    for (const slot of matchSlots(match)) {
      const key = slotKey(slot.match_key, slot.team_key);
      const pendingCell = cells[key];
      const row = index.get(key);
      const id = pendingCell?.saving ? pendingCell.target : row?.member_active ? row.assigned_member_id : null;
      if (id !== null && id !== undefined) busy.add(id);
    }
    return busy;
  };

  const saveSlot = async (matchKey: string, teamKey: string, memberId: number | null) => {
    const key = slotKey(matchKey, teamKey);
    setCells((current) => ({ ...current, [key]: { saving: true, target: memberId } }));
    const result = await room.saveChanges([{ match_key: matchKey, team_key: teamKey, assigned_member_id: memberId }]);
    setCells((current) => {
      const next = { ...current };
      if (result.ok) delete next[key];
      else next[key] = { saving: false, target: memberId, error: result.error };
      return next;
    });
    if (!result.ok) setNotice({ tone: 'warning', text: result.error });
  };

  const runPending = async () => {
    if (!pending || !pendingChanges.length) { setPending(null); return; }
    setBulkBusy(true);
    const result = await room.saveChanges(pendingChanges);
    setBulkBusy(false);
    if (result.ok) {
      setNotice({
        tone: 'muted',
        text: pending.kind === 'clear'
          ? `Cleared ${pendingChanges.length} upcoming slot${pendingChanges.length === 1 ? '' : 's'}.`
          : `Saved ${pendingChanges.length} assignment${pendingChanges.length === 1 ? '' : 's'}. Everyone sees them now.`,
      });
      setPending(null);
    } else {
      setNotice({ tone: 'warning', text: `Nothing was changed: ${result.error}` });
    }
  };

  const renderSlot = (match: EventScheduleItem, teamKey: string, busy: Set<number>) => {
    const key = slotKey(match.match_key, teamKey);
    const state = cells[key];
    return (
      <>
        <SlotSelect
          match={match}
          teamKey={teamKey}
          row={index.get(key)}
          members={members}
          busyInMatch={busy}
          workload={workload}
          state={state}
          editable={canEdit && !match.is_completed}
          onChange={(memberId) => { void saveSlot(match.match_key, teamKey, memberId); }}
        />
        <CellStatus state={state} onRetry={() => { void saveSlot(match.match_key, teamKey, state?.target ?? null); }} />
        {!state && index.get(key)?.covered ? <span className={styles.cellDone}>Scouted</span> : null}
      </>
    );
  };

  const eventCard = (
    <SurfaceCard title="Assignments">
      <EventPicker
        value={eventKey}
        onSelect={selectEvent}
        inputValue={eventInput}
        onInputChange={setEventInput}
        onSubmit={commitInput}
        loading={room.status === 'loading' && !snapshot}
      />
      {eventKey && room.eventName ? (
        <p className="center-event-status">
          <strong>{room.eventName}</strong> — {schedule.length} match{schedule.length === 1 ? '' : 'es'}
        </p>
      ) : null}
      {!eventKey ? <p className="center-callout muted">Pick your event to see and hand out matches.</p> : null}
      {room.error && !snapshot ? <p className="center-callout warning" role="alert">{room.error}</p> : null}
      {!room.online ? (
        <p className="center-callout warning">
          You're offline. {room.isLeader ? 'Assignments can be changed once you reconnect; ' : ''}this is the last copy this device saw.
        </p>
      ) : null}
      {notice ? (
        <p className={`center-callout ${notice.tone}`} role={notice.tone === 'warning' ? 'alert' : 'status'}>{notice.text}</p>
      ) : null}
    </SurfaceCard>
  );

  if (!eventKey || !snapshot) {
    return <SurfaceCardGroup groupId="scouting-assignments">{eventCard}</SurfaceCardGroup>;
  }

  if (!room.isLeader) {
    return (
      <SurfaceCardGroup groupId="scouting-assignments">
        {eventCard}
        <SurfaceCard title="Your assignments" subtitle="Your team lead hands these out. They update on their own.">
          <NextAssignment eventKey={eventKey} list={mine} />
        </SurfaceCard>
      </SurfaceCardGroup>
    );
  }

  return (
    <SurfaceCardGroup groupId="scouting-assignments">
      {eventCard}

      <SurfaceCard
        title={`Scouts (${members.length})`}
        subtitle="Everyone on your team. Tap a name to leave them out of auto-assign."
      >
        <div className={styles.scoutLegend}>
          {members.map((member) => {
            const out = skipped.has(member.member_id);
            return (
              <button
                key={member.member_id}
                type="button"
                className={`${styles.scoutToggle} ${out ? styles.scoutOut : ''}`.trim()}
                aria-pressed={!out}
                onClick={() => setSkipped((current) => {
                  const next = new Set(current);
                  if (next.has(member.member_id)) next.delete(member.member_id);
                  else next.add(member.member_id);
                  return next;
                })}
              >
                <Chip size="sm" className={`${styles.scoutChip} ${scoutColorClass(member.member_id, members)}`}>
                  {member.display_name}
                  {member.member_id === snapshot.me.member_id ? ' (you)' : ''}{' '}
                  <span className={styles.workload}>{workload.get(member.member_id) ?? 0}</span>
                </Chip>
                {out ? <span className={styles.outLabel}>skipped</span> : null}
              </button>
            );
          })}
        </div>
        {members.length < 2 ? (
          <p className="center-callout muted">
            Only you so far. Share your join code on My Team so scouts can join; they show up here on their own.
          </p>
        ) : null}
      </SurfaceCard>

      <SurfaceCard title="Coverage">
        <div className={styles.stats}>
          <Stat
            label="Slots assigned"
            value={`${coverage.assignedSlots} / ${coverage.upcomingSlots}`}
            tone={coverage.upcomingSlots > 0 && coverage.assignedSlots >= coverage.upcomingSlots ? 'success' : 'default'}
            size="sm"
          />
          <Stat label="Upcoming matches" value={schedule.filter((match) => !match.is_completed).length} size="sm" />
          {coverage.needsReassignment > 0 ? (
            <Stat label="Need a new scout" value={coverage.needsReassignment} tone="warning" size="sm" />
          ) : null}
        </div>
        <div className={`center-actions-row ${styles.actions}`}>
          <Button
            variant="primary"
            onClick={() => setPending({ kind: 'auto', mode: 'fill' })}
            disabled={!canEdit || autoMembers.length === 0 || coverage.upcomingSlots === 0}
          >
            Auto-assign
          </Button>
          <Button
            variant="quiet"
            onClick={() => setPending({ kind: 'clear' })}
            disabled={!canEdit || coverage.assignedSlots + coverage.needsReassignment === 0}
          >
            Clear upcoming
          </Button>
          <FieldCheckbox
            label="Show played matches"
            checked={showCompleted}
            onChange={(event) => setShowCompleted(event.target.checked)}
          />
        </div>
      </SurfaceCard>

      <SurfaceCard
        title="Match Assignments"
        subtitle={`${visibleMatches.length} match${visibleMatches.length === 1 ? '' : 'es'}. Each change saves straight away.`}
      >
        {visibleMatches.length === 0 ? (
          <p className="center-callout muted">
            {schedule.length === 0 ? 'No schedule published for this event yet.' : 'Every match here has been played.'}
          </p>
        ) : !isMobile ? (
          <div className="center-table-wrap">
            <table className="center-table">
              <thead>
                <tr>
                  <th scope="col">Match</th>
                  <th scope="col" colSpan={3} className="text-red" style={{ textAlign: 'center' }}>Red Alliance</th>
                  <th scope="col" colSpan={3} className="text-blue" style={{ textAlign: 'center' }}>Blue Alliance</th>
                </tr>
              </thead>
              <tbody>
                {visibleMatches.map((match) => {
                  const busy = busyByMatch(match);
                  return (
                    <tr key={match.match_key} className={match.is_completed ? 'assign-row-completed' : undefined}>
                      <th scope="row" className={styles.matchCell}>{match.display_name || match.match_key}</th>
                      {matchSlots(match).map((slot, idx) => (
                        <td key={slot.team_key} className={idx === 3 ? `${styles.cell} ${styles.allianceSplit}` : styles.cell}>
                          <div className="assign-cell-team-num">{teamNumber(slot.team_key)}</div>
                          {renderSlot(match, slot.team_key, busy)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="assign-mobile-list">
            {visibleMatches.map((match) => {
              const busy = busyByMatch(match);
              const slots = matchSlots(match);
              return (
                <div key={match.match_key} className={`assign-mobile-card${match.is_completed ? ' assign-mobile-completed' : ''}`}>
                  <div className="assign-mobile-card-head">
                    <span>{match.display_name || match.match_key}</span>
                    {match.is_completed ? <span className="text-muted">Played</span> : null}
                  </div>
                  {(['red', 'blue'] as const).map((alliance) => (
                    <div className="assign-mobile-alliance" key={alliance}>
                      <div className={`assign-mobile-alliance-label ${alliance}`}>{alliance === 'red' ? 'Red' : 'Blue'} Alliance</div>
                      {slots.filter((slot) => slot.alliance === alliance).map((slot) => (
                        <div className="assign-mobile-team-row" key={slot.team_key}>
                          <span className="assign-mobile-team-num">{teamNumber(slot.team_key)}</span>
                          <div className={styles.mobileSlot}>{renderSlot(match, slot.team_key, busy)}</div>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </SurfaceCard>

      {mine.length > 0 ? (
        <SurfaceCard title="Your own assignments">
          <NextAssignment eventKey={eventKey} list={mine} />
        </SurfaceCard>
      ) : null}

      <Modal
        open={pending !== null}
        onClose={() => { if (!bulkBusy) setPending(null); }}
        dismissible={!bulkBusy}
        title={pending?.kind === 'clear' ? 'Clear upcoming assignments?' : 'Auto-assign scouts?'}
        footer={
          <>
            <Button variant="quiet" onClick={() => setPending(null)} disabled={bulkBusy}>Cancel</Button>
            <Button
              variant={pending?.kind === 'clear' ? 'danger' : 'primary'}
              loading={bulkBusy}
              disabled={pendingChanges.length === 0}
              onClick={() => { void runPending(); }}
            >
              {pending?.kind === 'clear' ? 'Clear them' : `Save ${pendingChanges.length}`}
            </Button>
          </>
        }
      >
        {pending?.kind === 'clear' ? (
          <p>This removes {pendingChanges.length} upcoming assignment{pendingChanges.length === 1 ? '' : 's'}. Played matches stay as they are.</p>
        ) : (
          <>
            <p>
              {pendingChanges.length === 0
                ? 'Nothing to change: every upcoming slot already has a scout.'
                : `${pendingChanges.length} slot${pendingChanges.length === 1 ? '' : 's'} will be filled from ${autoMembers.length} scout${autoMembers.length === 1 ? '' : 's'}, spreading the work evenly. Nobody gets two teams in the same match.`}
            </p>
            {autoMembers.length < 6 ? (
              <p className="center-callout muted">
                With {autoMembers.length} scout{autoMembers.length === 1 ? '' : 's'}, some teams in each match stay unassigned.
              </p>
            ) : null}
            <FieldCheckbox
              label="Redo everything, not just the empty slots"
              checked={pending?.kind === 'auto' && pending.mode === 'redo'}
              onChange={(event) => setPending({ kind: 'auto', mode: event.target.checked ? 'redo' : 'fill' })}
            />
          </>
        )}
      </Modal>
    </SurfaceCardGroup>
  );
}

// Team-only: scouting assignments are private to a workspace, so nothing loads until
// the device has joined one.
export function ScoutingAssignPage() {
  const viewBar = <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />;
  return (
    <WorkspaceGate feature="Scouting assignments" viewBar={viewBar}>
      {viewBar}
      <div className="scouting-layout-grid">
        <div className="center-page-container">
          <ScoutingAssignWorkspacePage />
        </div>
      </div>
    </WorkspaceGate>
  );
}
