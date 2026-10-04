import { afterEach, describe, expect, it } from 'vitest';
import { readScoutDraft, scoutDraftKey, writeScoutDraft } from './scoutingPage.helpers';

describe('scout drafts', () => {
  afterEach(() => window.localStorage.clear());

  it('keeps one unsaved report per workspace, match and team', () => {
    const a = scoutDraftKey(3, '2026ARC_QM12', 'frc254');
    const otherTeam = scoutDraftKey(3, '2026arc_qm12', 'frc1678');
    const otherWorkspace = scoutDraftKey(4, '2026arc_qm12', 'frc254');
    expect(a).toBe(scoutDraftKey(3, '2026arc_qm12', 'FRC254'));

    writeScoutDraft(a, { form: { auto_scored: 3 }, rp: {}, notes: 'fast intake', saved_at_ms: 1 });
    expect(readScoutDraft<{ auto_scored: number }, object>(a)?.form.auto_scored).toBe(3);
    expect(readScoutDraft(otherTeam)).toBeNull();
    expect(readScoutDraft(otherWorkspace)).toBeNull();

    writeScoutDraft(a, null);
    expect(readScoutDraft(a)).toBeNull();
  });

  it('ignores a corrupted draft instead of crashing the page', () => {
    const key = scoutDraftKey(null, '2026arc_qm1', 'frc1');
    window.localStorage.setItem(key, '{not json');
    expect(readScoutDraft(key)).toBeNull();
  });
});
