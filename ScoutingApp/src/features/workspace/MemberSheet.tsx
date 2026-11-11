import type { WorkspaceMemberRecord } from '../../api';
import { Button, Chip, Modal } from '../../components/ui/primitives';
import styles from './MemberSheet.module.css';

type Props = {
  member: WorkspaceMemberRecord | null;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onToggleRole: () => void;
  onRemove: () => void;
};

// Phone: a bottom sheet (the Modal primitive); desktop: the same Modal, centred.
export function MemberSheet({ member, busy, error, onClose, onToggleRole, onRemove }: Props) {
  const isLeader = member?.role === 'leader';
  return (
    <Modal
      open={member !== null}
      onClose={onClose}
      title={member?.display_name ?? ''}
      size="sm"
      dismissible={!busy}
      footer={<Button variant="quiet" onClick={onClose} disabled={busy}>Cancel</Button>}
    >
      <div className={styles.body}>
        <p className={styles.role}>
          <Chip size="sm" tone={isLeader ? 'accent' : 'neutral'}>{isLeader ? 'Leader' : 'Member'}</Chip>
        </p>
        {error ? <p className="center-callout warning" role="alert">{error}</p> : null}
        <Button variant="default" loading={busy} onClick={onToggleRole}>
          {isLeader ? 'Make member' : 'Make leader'}
        </Button>
        <Button variant="danger" disabled={busy} onClick={onRemove}>Remove from team</Button>
      </div>
    </Modal>
  );
}
