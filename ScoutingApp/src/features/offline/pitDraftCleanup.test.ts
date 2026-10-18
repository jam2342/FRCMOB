import { describe, expect, it } from 'vitest';
import { readPitDraft, savePitDraft, startPitDraftCleanup } from './pitDrafts';

const deliver = (body: unknown) => window.dispatchEvent(new CustomEvent('offlinequeue:delivered', {
  detail: { url: '/api/pit-scouting', method: 'POST', body: JSON.stringify(body) },
}));

describe('pit drafts settled by a delivered offline save', () => {
  it('clears the draft that was delivered and keeps one edited afterwards', () => {
    startPitDraftCleanup(() => 7);
    savePitDraft(7, '2026arc', 'frc971', { notes: 'swerve' });
    deliver({ event_key: '2026arc', team_key: 'frc971', payload: { notes: 'swerve' } });
    expect(readPitDraft(7, '2026arc', 'frc971')).toBeNull();

    savePitDraft(7, '2026arc', 'frc254', { notes: 'edited after saving' });
    deliver({ event_key: '2026arc', team_key: 'frc254', payload: { notes: 'what was queued' } });
    expect(readPitDraft(7, '2026arc', 'frc254')).toEqual({ notes: 'edited after saving' });
  });
});
