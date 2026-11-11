import { useEffect, useState } from 'react';
import type { PitAssignment } from '../../api';
import { Button, FieldCheckbox, FieldSelect, Modal } from '../../components/ui/primitives';
import type { PitAssignments } from './usePitAssignments';
import { teamNumber } from './pitTeams';
import type { PitQueueRow } from './pitTeams';
import type { Scout } from './usePitScouts';
import styles from './PitQueue.module.css';

type SplitProps = {
  open: boolean;
  onClose: () => void;
  // Teams still to do that nobody holds: what a split without "replace" actually hands out.
  unassigned: string[];
  // Every team still to do; the server skips the ones already held or already scouted.
  notDone: string[];
  scouts: Scout[];
  scoutsFailed: boolean;
  assignments: PitAssignments;
  meId: number;
};

type SplitResult = { teams: number; perScout: Array<{ name: string; count: number }> };

function splitResult(rows: PitAssignment[], handedOut: Set<string>): SplitResult {
  const counts = new Map<string, number>();
  let teams = 0;
  for (const row of rows) {
    if (!handedOut.has(row.team_key.toLowerCase())) continue;
    teams += 1;
    const name = row.member_name?.trim() || 'A teammate';
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  return { teams, perScout: [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => a.name.localeCompare(b.name)) };
}

export function PitAutoSplit({ open, onClose, unassigned, notDone, scouts, scoutsFailed, assignments, meId }: SplitProps) {
  const [unticked, setUnticked] = useState<Set<number>>(new Set());
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<SplitResult | null>(null);

  useEffect(() => {
    if (open) { setResult(null); setUnticked(new Set()); }
  }, [open]);

  const chosen = scouts.filter((scout) => !unticked.has(scout.id));
  const nothingToSplit = unassigned.length === 0;
  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

  async function run() {
    setRunning(true);
    const handedOut = new Set(unassigned);
    const rows = await assignments.autoSplit(notDone, chosen.map((scout) => scout.id));
    setRunning(false);
    if (rows) setResult(splitResult(rows, handedOut));
    else onClose();
  }

  const footer = result ? (
    <Button variant="primary" onClick={onClose}>Done</Button>
  ) : (
    <>
      <Button variant="quiet" onClick={onClose} disabled={running}>Cancel</Button>
      <Button variant="primary" onClick={() => void run()} loading={running} disabled={nothingToSplit || chosen.length === 0 || !assignments.online}>
        Split
      </Button>
    </>
  );

  return (
    <Modal open={open} onClose={onClose} title="Auto-split teams" size="sm" footer={footer} dismissible={!running}>
      {result ? (
        <div className={styles.sheetBody} role="status">
          <p className={styles.sheetLead}>Split {plural(result.teams, 'team')} across {plural(result.perScout.length, 'scout')}.</p>
          <ul className={styles.splitList}>
            {result.perScout.map((row) => (
              <li key={row.name}><span>{row.name}</span><span>{row.count}</span></li>
            ))}
          </ul>
        </div>
      ) : (
        <div className={styles.sheetBody}>
          <p className={styles.sheetLead}>
            {nothingToSplit
              ? 'Every team that is left already has a scout.'
              : `Split ${plural(unassigned.length, 'team')} across ${plural(chosen.length, 'scout')}?`}
          </p>
          <p className={styles.sheetNote}>Teams that are scouted or already taken stay as they are.</p>
          {scoutsFailed ? <p className={styles.sheetNote} role="alert">Couldn't load your team's members. Check your connection and try again.</p> : null}
          <div className={styles.scoutPick} role="group" aria-label="Scouts to split between">
            {scouts.map((scout) => (
              <FieldCheckbox
                key={scout.id}
                label={scout.id === meId ? `${scout.display_name} (you)` : scout.display_name}
                checked={!unticked.has(scout.id)}
                onChange={(event) =>
                  setUnticked((current) => {
                    const next = new Set(current);
                    if (event.target.checked) next.delete(scout.id);
                    else next.add(scout.id);
                    return next;
                  })
                }
              />
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}

type ReassignProps = {
  row: PitQueueRow | null;
  onClose: () => void;
  scouts: Scout[];
  assignments: PitAssignments;
  meId: number;
};

export function PitReassign({ row, onClose, scouts, assignments, meId }: ReassignProps) {
  const [choice, setChoice] = useState('');
  const [confirmingTake, setConfirmingTake] = useState(false);
  const teamKey = row?.team.team_key ?? '';

  useEffect(() => {
    setChoice(row?.assigneeId ? String(row.assigneeId) : '');
    setConfirmingTake(false);
  }, [row]);

  if (!row) return null;
  const heldByOther = row.assigneeId !== null && !row.mine;
  const changed = choice !== (row.assigneeId ? String(row.assigneeId) : '');

  async function save() {
    const scout = scouts.find((item) => String(item.id) === choice);
    if (await assignments.reassign(teamKey, scout ? { id: scout.id, name: scout.display_name } : null)) onClose();
  }

  async function takeIt() {
    if (await assignments.claim(teamKey, { force: true })) onClose();
  }

  if (confirmingTake) {
    return (
      <Modal
        open
        onClose={onClose}
        title={`Take Team ${teamNumber(row.team.team_key)}?`}
        size="sm"
        footer={
          <>
            <Button variant="quiet" onClick={() => setConfirmingTake(false)}>Back</Button>
            <Button variant="primary" onClick={() => void takeIt()} loading={assignments.pending.has(teamKey)}>Take it</Button>
          </>
        }
      >
        <p className={styles.sheetLead}>{row.assigneeName} has this team. It becomes yours and they'll see it gone.</p>
      </Modal>
    );
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`Team ${teamNumber(row.team.team_key)}`}
      size="sm"
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => void save()} disabled={!changed || !assignments.online} loading={assignments.pending.has(teamKey)}>
            Save
          </Button>
        </>
      }
    >
      <div className={styles.sheetBody}>
        <FieldSelect label="Assigned to" value={choice} onChange={(event) => setChoice(event.target.value)}>
          <option value="">Nobody</option>
          {scouts.map((scout) => (
            <option key={scout.id} value={scout.id}>{scout.id === meId ? `${scout.display_name} (you)` : scout.display_name}</option>
          ))}
        </FieldSelect>
        {heldByOther ? (
          <Button variant="quiet" size="sm" onClick={() => setConfirmingTake(true)} disabled={!assignments.online}>
            Take it from {row.assigneeName}
          </Button>
        ) : null}
        {!assignments.online ? <p className={styles.sheetNote}>You're offline. Assignments can't change until you reconnect.</p> : null}
      </div>
    </Modal>
  );
}
