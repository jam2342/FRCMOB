import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AutoScoutDraftRecord } from '../../pages/scoutingPage.types';
import { AutoScoutEvidencePanel } from './AutoScoutEvidencePanel';

describe('AutoScoutEvidencePanel wording', () => {
  it('uses a plain-language source label and an actionable empty state', () => {
    const draft = {
      field_confidence: { auto_points: 0.8 },
      field_provenance: { auto_points: 'auto' },
      field_evidence_refs: { auto_points: [] },
    } as unknown as AutoScoutDraftRecord;

    render(
      <AutoScoutEvidencePanel
        open
        mobile={false}
        fieldName="auto_points"
        draft={draft}
        heatmapData={null}
        heatmapLoading={false}
        heatmapError=""
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('Source: Phone recording')).toBeInTheDocument();
    expect(screen.getByText(/check it manually before saving/i)).toBeInTheDocument();
    expect(screen.queryByText(/provenance|evidence refs/i)).toBeNull();
  });
});
