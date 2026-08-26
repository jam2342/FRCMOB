import './AutoScoutEvidencePanel.css';

import { BottomSheet } from '../ui/BottomSheet';
import { SurfaceCard } from '../ui/SurfaceCard';
import { VideoReplayer } from '../cv/VideoReplayer';
import type { MatchTracksResponse } from '../../api';
import type { AutoScoutDraftRecord } from '../../pages/scoutingPage.types';

type AutoScoutEvidencePanelProps = {
  open: boolean;
  mobile: boolean;
  fieldName: string | null;
  draft: AutoScoutDraftRecord | null;
  tracksData: MatchTracksResponse | null;
  tracksLoading: boolean;
  tracksError: string;
  onClose: () => void;
};

function formatFieldLabel(fieldName: string): string {
  return fieldName
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (token) => token.toUpperCase());
}

function renderMeta(meta: Record<string, unknown> | null | undefined): Array<[string, string]> {
  if (!meta) return [];
  return Object.entries(meta)
    .map(([key, value]) => [formatFieldLabel(key), typeof value === 'object' ? JSON.stringify(value) : String(value)] as [string, string])
    .slice(0, 12);
}

function EvidenceBody({
  fieldName,
  draft,
  tracksData,
  tracksLoading,
  tracksError,
}: Omit<AutoScoutEvidencePanelProps, 'open' | 'mobile' | 'onClose'>) {
  if (!fieldName || !draft) {
    return <p className="center-callout muted">Select an auto-filled field to inspect its evidence.</p>;
  }
  const confidence = draft.field_confidence?.[fieldName];
  const refs = draft.field_evidence_refs?.[fieldName] || [];
  const primaryTimeSec = refs.length > 0 ? refs[0].t_sec : null;

  return (
    <div className="auto-scout-evidence">
      <div className="auto-scout-evidence__summary">
        <strong>{formatFieldLabel(fieldName)}</strong>
        <span>Confidence {typeof confidence === 'number' ? `${Math.round(confidence * 100)}%` : 'N/A'}</span>
        <span>Provenance {draft.field_provenance?.[fieldName] || 'needs_review'}</span>
      </div>

      {tracksLoading ? <p className="center-callout muted">Loading tracking evidence…</p> : null}
      {tracksError ? <p className="center-callout warning">{tracksError}</p> : null}
      {tracksData ? (
        <VideoReplayer
          className="auto-scout-evidence__replayer"
          data={tracksData}
          videoUrl={tracksData.local_video_url || tracksData.video_url}
          seekToTimeSec={primaryTimeSec}
        />
      ) : null}

      <div className="auto-scout-evidence__refs">
        {refs.length > 0 ? refs.map((ref) => (
          <div key={`${fieldName}-${ref.ref_id}-${ref.t_sec}`} className="auto-scout-evidence__ref">
            <div className="auto-scout-evidence__ref-head">
              <strong>{ref.type}</strong>
              <span>{ref.t_sec.toFixed(1)}s</span>
            </div>
            <div className="auto-scout-evidence__ref-id">{ref.ref_id}</div>
            {renderMeta(ref.meta).map(([label, value]) => (
              <div key={`${ref.ref_id}-${label}`} className="auto-scout-evidence__meta">
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
        )) : (
          <p className="center-callout muted">No evidence refs recorded for this field.</p>
        )}
      </div>
    </div>
  );
}

export function AutoScoutEvidencePanel(props: AutoScoutEvidencePanelProps) {
  const { mobile, open, onClose, ...bodyProps } = props;
  if (!open) return null;
  if (mobile) {
    return (
      <BottomSheet open={open} onClose={onClose} title="Auto-Scout Evidence" snapPoints={[62, 84]}>
        <EvidenceBody {...bodyProps} />
      </BottomSheet>
    );
  }
  return (
    <SurfaceCard title="Auto-Scout Evidence">
      <EvidenceBody {...bodyProps} />
    </SurfaceCard>
  );
}
