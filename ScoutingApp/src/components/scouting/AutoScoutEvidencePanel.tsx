import './AutoScoutEvidencePanel.css';

import { BottomSheet } from '../ui/BottomSheet';
import { SurfaceCard } from '../ui/SurfaceCard';
import { FieldHeatmap } from '../cv/FieldHeatmap';
import type { TeamHeatmapResponse } from '../../api';
import type { AutoScoutDraftRecord } from '../../pages/scoutingPage.types';

type AutoScoutEvidencePanelProps = {
  open: boolean;
  mobile: boolean;
  fieldName: string | null;
  draft: AutoScoutDraftRecord | null;
  heatmapData: TeamHeatmapResponse | null;
  heatmapLoading: boolean;
  heatmapError: string;
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

function fieldSourceLabel(source: string | undefined): string {
  if (source === 'auto') return 'Phone recording';
  if (source === 'manual') return 'Scout entry';
  if (source === 'blank') return 'Not filled';
  return 'Needs review';
}

function EvidenceBody({
  fieldName,
  draft,
  heatmapData,
  heatmapLoading,
  heatmapError,
}: Omit<AutoScoutEvidencePanelProps, 'open' | 'mobile' | 'onClose'>) {
  if (!fieldName || !draft) {
    return <p className="center-callout muted">Select an auto-filled field to inspect its evidence.</p>;
  }
  const confidence = draft.field_confidence?.[fieldName];
  const refs = draft.field_evidence_refs?.[fieldName] || [];

  return (
    <div className="auto-scout-evidence">
      <div className="auto-scout-evidence__summary">
        <strong>{formatFieldLabel(fieldName)}</strong>
        <span>Confidence {typeof confidence === 'number' ? `${Math.round(confidence * 100)}%` : 'N/A'}</span>
        <span>Source: {fieldSourceLabel(draft.field_provenance?.[fieldName])}</span>
      </div>

      {heatmapLoading ? <p className="center-callout muted">Loading recorded positions…</p> : null}
      {heatmapError ? <p className="center-callout warning">{heatmapError}</p> : null}
      {heatmapData ? (
        heatmapData.total_points > 0 ? (
          <FieldHeatmap data={heatmapData} />
        ) : (
          <p className="center-callout muted">No recorded positions for this robot in this match.</p>
        )
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
          <p className="center-callout muted">No supporting details were recorded for this field. Check it manually before saving.</p>
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
