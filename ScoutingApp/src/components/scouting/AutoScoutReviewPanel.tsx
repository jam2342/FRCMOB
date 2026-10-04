import './AutoScoutReviewPanel.css';

import { RefreshIcon, RobotIcon, SaveIcon, SearchIcon, TrashIcon } from '../ui/Icons';
import type { AutoScoutDraftRecord } from '../../pages/scoutingPage.types';

type AutoScoutReviewPanelProps = {
  enabled: boolean;
  canGenerate: boolean;
  loading: boolean;
  approving: boolean;
  error: string;
  draft: AutoScoutDraftRecord | null;
  onEnableAuto: () => void;
  onDisableAuto: () => void;
  onGenerate: () => void;
  onRegenerate: () => void;
  onReject: () => void;
  onSave: () => void;
};

function missingReasonCopy(reason: string): string {
  switch (reason) {
    case 'analysis_pending':
      return 'The accepted phone recording is still being processed on this device.';
    case 'analysis_stale':
      return 'This recording was processed by an older app version. Process it again.';
    case 'no_video':
      return 'Accept a phone recording before generating an auto-scout draft.';
    case 'no_calibration':
      return 'Calibrate the phone\'s field view before processing the recording.';
    case 'low_track_coverage':
      return 'Tracking coverage is too low to trust the draft.';
    case 'team_not_resolved':
      return 'Tracks exist, but this team was not resolved confidently.';
    case 'game_year_unsupported':
      return 'Auto Draft does not support this season yet.';
    default:
      return 'Auto Draft is not ready yet. Process the recording again or scout this match manually.';
  }
}

export function AutoScoutReviewPanel({
  enabled,
  canGenerate,
  loading,
  approving,
  error,
  draft,
  onEnableAuto,
  onDisableAuto,
  onGenerate,
  onRegenerate,
  onReject,
  onSave,
}: AutoScoutReviewPanelProps) {
  const status = draft?.status || 'pending';
  const canReview = status === 'ready' || status === 'low_confidence' || status === 'approved';

  return (
    <div className="auto-scout-panel">
      <div className="auto-scout-panel__head">
        <div>
          <span className="auto-scout-panel__kicker">Auto Draft</span>
          <strong>{enabled ? 'Review auto-scout draft' : 'Manual scouting active'}</strong>
          <p>
            {enabled
              ? 'Objective fields can be prefilled from an accepted phone recording, then reviewed before save.'
              : 'Switch to Auto Draft to prefill supported fields with evidence.'}
          </p>
        </div>
        <button
          type="button"
          className={`center-btn ${enabled ? 'ghost' : ''}`.trim()}
          onClick={enabled ? onDisableAuto : onEnableAuto}
        >
          <RobotIcon className="icon-inline" /> {enabled ? 'Manual Mode' : 'Auto Draft'}
        </button>
      </div>

      {enabled ? (
        <div className="auto-scout-panel__body">
          <div className="center-status-row compact scout-status-row">
            <span className="center-chip">Status: {status.replace(/_/g, ' ')}</span>
          </div>

          {draft ? (
            <p className="center-callout muted">
              {status === 'ready'
                ? 'Draft is ready. Review the highlighted fields and save when you agree.'
                : status === 'low_confidence'
                  ? 'Draft is usable, but one or more machine-filled fields are below the confidence threshold.'
                  : status === 'approved'
                    ? 'Draft approval is recorded. Saving will persist the reviewed entry through the normal flow.'
                    : status === 'generating'
                      ? 'Generating a draft from the accepted phone recording.'
                      : 'No usable draft yet.'}
            </p>
          ) : (
            <p className="center-callout muted">
              Generate a draft after choosing an event, match, and team.
            </p>
          )}

          {draft?.missing_reasons?.length ? (
            <div className="auto-scout-panel__missing">
              {draft.missing_reasons.map((reason) => (
                <span key={reason} className="auto-scout-panel__missing-chip">
                  {missingReasonCopy(reason)}
                </span>
              ))}
            </div>
          ) : null}

          {error ? <p className="center-callout danger">{error}</p> : null}

          <div className="center-actions-row">
            <button
              type="button"
              className="center-btn"
              onClick={onGenerate}
              disabled={!canGenerate || loading}
            >
              <SearchIcon className="icon-inline" /> {loading ? 'Working...' : draft ? 'Refresh Draft' : 'Generate Draft'}
            </button>
            {draft ? (
              <button
                type="button"
                className="center-btn ghost"
                onClick={onRegenerate}
                disabled={loading}
              >
                <RefreshIcon className="icon-inline" /> Regenerate
              </button>
            ) : null}
            {draft && status !== 'approved' ? (
              <button
                type="button"
                className="center-btn ghost"
                onClick={onReject}
                disabled={loading}
              >
                <TrashIcon className="icon-inline" /> Reject
              </button>
            ) : null}
            {draft && canReview ? (
              <button
                type="button"
                className="center-btn"
                onClick={onSave}
                disabled={approving}
              >
                <SaveIcon className="icon-inline" /> {approving ? 'Approving...' : 'Save Reviewed Entry'}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
