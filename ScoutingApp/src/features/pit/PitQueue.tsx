import { useEffect, useMemo, useState } from 'react';
import { SegmentedTabs } from '../../components/ui/SegmentedTabs';
import { Button, FieldText } from '../../components/ui/primitives';
import { CameraIcon, CheckIcon, CloudSyncIcon, PenIcon, UsersIcon, WifiOffIcon } from '../../components/ui/Icons';
import { PitAutoSplit, PitReassign } from './PitLeaderTools';
import { PIT_STATUS_LABEL, pitUpdatedAgo, queueRowLabel, rowsForTab, searchQueueRows, tabCounts } from './pitTeams';
import type { PitQueueRow, PitStatus, PitTab } from './pitTeams';
import type { PitAssignments } from './usePitAssignments';
import { usePitScouts } from './usePitScouts';
import styles from './PitQueue.module.css';

const OFFLINE_REASON = "Offline: reconnect to change who's on a team";

const EMPTY: Record<PitTab, string> = {
  mine: 'Nothing assigned to you. Take a team from Left.',
  left: 'Every team has notes.',
  done: 'No team has notes yet.',
};

const STATUS_ICON: Record<PitStatus, typeof CheckIcon | null> = {
  todo: null,
  draft: PenIcon,
  queued: CloudSyncIcon,
  done: CheckIcon,
};

export type PitQueueProps = {
  rows: PitQueueRow[];
  selectedTeam: string;
  disabled: boolean;
  assignments: PitAssignments;
  isLeader: boolean;
  meId: number | null;
  onOpen: (teamKey: string) => void;
};

function RowActions({
  row,
  assignments,
  isLeader,
  onReassign,
  offlineId,
}: {
  row: PitQueueRow;
  assignments: PitAssignments;
  isLeader: boolean;
  onReassign: (row: PitQueueRow) => void;
  offlineId: string;
}) {
  if (row.done) return null;
  const busy = assignments.pending.has(row.team.team_key);
  const blocked = !assignments.online;
  const reason = blocked ? OFFLINE_REASON : undefined;
  const number = row.team.team_number;
  return (
    <div className={styles.actions}>
      {row.assigneeId === null ? (
        <Button
          size="sm"
          variant="quiet"
          className={styles.action}
          loading={busy}
          disabled={blocked || !assignments.ready}
          title={reason}
          aria-describedby={blocked ? offlineId : undefined}
          aria-label={`I'm on it for ${number}`}
          onClick={() => void assignments.claim(row.team.team_key)}
        >
          I'm on it
        </Button>
      ) : null}
      {row.mine ? (
        <Button
          size="sm"
          variant="quiet"
          className={styles.action}
          loading={busy}
          disabled={blocked}
          title={reason}
          aria-describedby={blocked ? offlineId : undefined}
          aria-label={`Release ${number}`}
          onClick={() => void assignments.release(row.team.team_key)}
        >
          Release
        </Button>
      ) : null}
      {isLeader ? (
        <Button
          size="sm"
          variant="quiet"
          iconOnly
          className={styles.more}
          icon={<UsersIcon />}
          aria-label={`Change who has ${number}`}
          title="Reassign"
          onClick={() => onReassign(row)}
        />
      ) : null}
    </div>
  );
}

export function PitQueue({ rows, selectedTeam, disabled, assignments, isLeader, meId, onOpen }: PitQueueProps) {
  const [query, setQuery] = useState('');
  // Null until the scout picks: the first answer from the server decides it once, so claiming a team later doesn't yank the view.
  const [tab, setTab] = useState<PitTab | null>(null);
  const [splitOpen, setSplitOpen] = useState(false);
  const [reassigning, setReassigning] = useState<string | null>(null);
  const counts = useMemo(() => tabCounts(rows), [rows]);
  const offlineId = 'pit-queue-offline';

  useEffect(() => {
    if (assignments.ready && rows.length > 0) setTab((current) => current ?? (counts.mine > 0 ? 'mine' : 'left'));
  }, [assignments.ready, rows.length, counts.mine]);

  const shown: PitTab = tab ?? 'left';
  const visible = useMemo(() => searchQueueRows(rowsForTab(rows, shown), query, selectedTeam), [rows, shown, query, selectedTeam]);
  const { scouts, failed } = usePitScouts(isLeader && (splitOpen || reassigning !== null));
  const reassignRow = rows.find((row) => row.team.team_key === reassigning) ?? null;
  const unassigned = useMemo(() => rows.filter((row) => !row.done && row.assigneeId === null).map((row) => row.team.team_key), [rows]);
  const notDone = useMemo(() => rows.filter((row) => !row.done).map((row) => row.team.team_key), [rows]);
  const now = Date.now();

  return (
    <div className={styles.queue}>
      <div className={styles.tools}>
        <FieldText
          label="Find a team"
          type="search"
          inputMode="search"
          enterKeyHint="search"
          autoComplete="off"
          placeholder="Number or name"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <SegmentedTabs<PitTab>
          className={styles.tabs}
          ariaLabel="Pit queue"
          value={shown}
          onChange={setTab}
          items={[
            { value: 'mine', label: <>Mine<small>{counts.mine}</small></>, ariaLabel: `Mine, ${counts.mine}` },
            { value: 'left', label: <>Left<small>{counts.left}</small></>, ariaLabel: `Left, ${counts.left}` },
            { value: 'done', label: <>Done<small>{counts.done}</small></>, ariaLabel: `Done, ${counts.done}` },
          ]}
        />
        {isLeader ? (
          <div className={styles.leaderBar}>
            <Button size="sm" variant="quiet" icon={<UsersIcon />} onClick={() => setSplitOpen(true)} disabled={disabled}>
              Auto-split
            </Button>
          </div>
        ) : null}
      </div>

      {!assignments.online ? (
        <p id={offlineId} className={styles.banner} role="status">
          <WifiOffIcon className={styles.bannerIcon} />
          <span>Offline. Showing the last assignments. Taking or letting go of a team waits until you're back.</span>
        </p>
      ) : null}
      {assignments.notice ? (
        <p className={styles.banner} role="status">
          <span>{assignments.notice.text}</span>
          <button type="button" className={styles.dismiss} onClick={assignments.dismissNotice}>Dismiss</button>
        </p>
      ) : null}

      {visible.length === 0 ? (
        <p className={styles.empty} role="status">
          {query.trim() ? `No team matches "${query.trim()}" here.` : EMPTY[shown]}
        </p>
      ) : (
        <ul className={styles.list} aria-label={`Teams, ${shown === 'done' ? 'done' : shown === 'mine' ? 'mine' : 'left'}`}>
          {visible.map((row) => {
            const key = row.team.team_key;
            const selected = key === selectedTeam;
            const StatusIcon = STATUS_ICON[row.status];
            const ago = pitUpdatedAgo(row.updatedAt, now);
            return (
              <li key={key} className={styles.row}>
                <button
                  type="button"
                  className={styles.main}
                  disabled={disabled}
                  aria-current={selected ? 'true' : undefined}
                  aria-label={queueRowLabel(row, { selected })}
                  onClick={() => onOpen(key)}
                >
                  <span className={styles.head}>
                    <strong className={styles.number}>{row.team.team_number}</strong>
                    {row.team.nickname ? <span className={styles.name} title={row.team.nickname}>{row.team.nickname}</span> : null}
                    {row.hasPhotos ? <CameraIcon className={styles.mark} /> : null}
                  </span>
                  <span className={styles.meta}>
                    <span className={styles.status} data-status={row.status}>
                      {StatusIcon ? <StatusIcon className={styles.mark} /> : null}
                      {PIT_STATUS_LABEL[row.status]}
                    </span>
                    {row.assigneeId !== null || !row.done ? (
                      <span className={row.mine ? styles.mine : undefined}>
                        {row.mine ? 'You' : row.assigneeName ?? 'Unassigned'}
                      </span>
                    ) : null}
                    {ago ? <span>{ago}</span> : null}
                  </span>
                </button>
                <RowActions
                  row={row}
                  assignments={assignments}
                  isLeader={isLeader}
                  onReassign={(target) => setReassigning(target.team.team_key)}
                  offlineId={offlineId}
                />
              </li>
            );
          })}
        </ul>
      )}

      {isLeader && meId !== null ? (
        <>
          <PitAutoSplit
            open={splitOpen}
            onClose={() => setSplitOpen(false)}
            unassigned={unassigned}
            notDone={notDone}
            scouts={scouts}
            scoutsFailed={failed}
            assignments={assignments}
            meId={meId}
          />
          <PitReassign row={reassignRow} onClose={() => setReassigning(null)} scouts={scouts} assignments={assignments} meId={meId} />
        </>
      ) : null}
    </div>
  );
}
