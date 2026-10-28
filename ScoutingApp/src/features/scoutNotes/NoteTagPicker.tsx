import { useId } from 'react';
import { CheckCircleIcon } from '../../components/ui/Icons';
import { Button } from '../../components/ui/primitives';
import { hapticTap } from '../../utils/haptics';
import { SCOUT_NOTE_TAGS, toggleNoteTag, type ScoutNoteTag } from './noteTags';
import styles from './ScoutNotes.module.css';

type Props = {
  value: readonly ScoutNoteTag[];
  onChange: (next: ScoutNoteTag[], toggled: { tag: ScoutNoteTag; on: boolean }) => void;
  /** A subset to offer, e.g. the few worth flagging mid-match. All tags when absent. */
  only?: readonly ScoutNoteTag[];
  label?: string;
  /** Show the label above the tags, where nothing else on screen says what they're for. */
  showLabel?: boolean;
};

// One tap per tag, no typing: the scout's eyes stay on the robot.
export function NoteTagPicker({ value, onChange, only, label = 'Quick tags', showLabel = false }: Props) {
  const labelId = useId();
  const offered = only ? SCOUT_NOTE_TAGS.filter((tag) => only.includes(tag.id)) : SCOUT_NOTE_TAGS;
  const picker = (
    <div
      className={styles.picker}
      role="group"
      aria-label={showLabel ? undefined : label}
      aria-labelledby={showLabel ? labelId : undefined}
    >
      {offered.map((tag) => {
        const on = value.includes(tag.id);
        return (
          <Button
            key={tag.id}
            size="sm"
            variant="quiet"
            pressed={on}
            className={styles.chip}
            icon={on ? <CheckCircleIcon className="icon-inline" /> : undefined}
            onClick={() => {
              hapticTap();
              onChange(toggleNoteTag(value, tag.id), { tag: tag.id, on: !on });
            }}
          >
            {tag.label}
          </Button>
        );
      })}
    </div>
  );
  if (!showLabel) return picker;
  return (
    <div className={styles.labelled}>
      <p className={styles.pickerLabel} id={labelId}>{label}</p>
      {picker}
    </div>
  );
}
