import { Button } from '../../components/ui/primitives';
import { AlertTriangleIcon, CheckIcon, ChevronLeftIcon, CloudSyncIcon, InfoIcon } from '../../components/ui/Icons';
import type { PitStepper } from './pitStepConfig';
import './pit.css';

export type PitSaveTone = 'idle' | 'dirty' | 'saving' | 'saved' | 'queued' | 'warn' | 'error';

export type PitSaveStatus = { tone: PitSaveTone; text: string };

export type PitSaveBarProps = {
  status: PitSaveStatus;
  saving: boolean;
  // Saving needs the team's server notes loaded first, or it could replace a teammate's entry.
  disabled: boolean;
  onSave: () => void;
  onSaveAndNext: () => void;
  // On a phone the primary button walks the steps; Save stays put so a partial entry can be saved from any of them.
  stepper?: PitStepper;
};

function StatusIcon({ tone }: { tone: PitSaveTone }) {
  if (tone === 'saved') return <CheckIcon />;
  if (tone === 'queued') return <CloudSyncIcon />;
  if (tone === 'error' || tone === 'warn') return <AlertTriangleIcon />;
  return <InfoIcon />;
}

// Pinned above the app's tab bar so the answer to "did that save?" sits next to the button that was tapped.
export function PitSaveBar({ status, saving, disabled, onSave, onSaveAndNext, stepper }: PitSaveBarProps) {
  const stepped = Boolean(stepper?.stepped);
  const index = stepper?.index ?? 0;
  const lastStep = !stepper || index >= stepper.count - 1;
  return (
    <div className="pit-save-bar" role="group" aria-label="Save pit entry">
      <p
        className="pit-save-bar__status"
        data-tone={status.tone}
        role={status.tone === 'error' ? 'alert' : 'status'}
        title={status.text}
      >
        <span className="pit-save-bar__icon" aria-hidden="true">
          <StatusIcon tone={status.tone} />
        </span>
        <span className="pit-save-bar__text">{status.text}</span>
      </p>
      <div className={`pit-save-bar__actions ${stepped && index > 0 ? 'pit-save-bar__actions--back' : ''}`}>
        {stepped && index > 0 ? (
          <Button variant="quiet" iconOnly icon={<ChevronLeftIcon />} aria-label="Back to the previous step" onClick={() => stepper?.setIndex(index - 1)} />
        ) : null}
        <Button variant="quiet" onClick={onSave} loading={saving} disabled={disabled}>
          Save
        </Button>
        {stepped && !lastStep ? (
          <Button variant="primary" onClick={() => stepper?.setIndex(index + 1)}>
            Next step
          </Button>
        ) : (
          <Button variant="primary" onClick={onSaveAndNext} disabled={disabled || saving}>
            Save &amp; next
          </Button>
        )}
      </div>
    </div>
  );
}
