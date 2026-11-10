import { Button } from '../../components/ui/primitives';
import { AlertTriangleIcon, CheckIcon, CloudSyncIcon, InfoIcon } from '../../components/ui/Icons';
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
};

function StatusIcon({ tone }: { tone: PitSaveTone }) {
  if (tone === 'saved') return <CheckIcon />;
  if (tone === 'queued') return <CloudSyncIcon />;
  if (tone === 'error' || tone === 'warn') return <AlertTriangleIcon />;
  return <InfoIcon />;
}

// Pinned above the app's tab bar so the answer to "did that save?" sits next to the button that was tapped.
export function PitSaveBar({ status, saving, disabled, onSave, onSaveAndNext }: PitSaveBarProps) {
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
      <div className="pit-save-bar__actions">
        <Button variant="quiet" onClick={onSave} loading={saving} disabled={disabled}>
          Save
        </Button>
        <Button variant="primary" onClick={onSaveAndNext} disabled={disabled || saving}>
          Save &amp; next
        </Button>
      </div>
    </div>
  );
}
