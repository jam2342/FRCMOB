import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AutoScoutDraftRecord } from '../../pages/scoutingPage.types';
import { AutoScoutReviewPanel } from './AutoScoutReviewPanel';

const noop = vi.fn();

function renderPanel(draft: AutoScoutDraftRecord | null) {
  return render(
    <AutoScoutReviewPanel
      enabled
      canGenerate
      loading={false}
      approving={false}
      error=""
      draft={draft}
      onEnableAuto={noop}
      onDisableAuto={noop}
      onGenerate={noop}
      onRegenerate={noop}
      onReject={noop}
      onSave={noop}
    />,
  );
}

describe('AutoScoutReviewPanel wording', () => {
  it('explains old recording states without exposing worker or pipeline jargon', () => {
    const draft = {
      status: 'pending',
      missing_reasons: ['analysis_pending', 'analysis_stale', 'no_video', 'unknown_internal_code'],
    } as unknown as AutoScoutDraftRecord;

    renderPanel(draft);

    expect(screen.getByText(/still being processed on this device/i)).toBeInTheDocument();
    expect(screen.getByText(/older app version/i)).toBeInTheDocument();
    expect(screen.getByText(/accept a phone recording/i)).toBeInTheDocument();
    expect(screen.getByText(/scout this match manually/i)).toBeInTheDocument();
    expect(screen.queryByText(/pipeline|queued|run_kind|fallback model/i)).toBeNull();
  });
});
