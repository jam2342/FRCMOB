import { describe, expect, it } from 'vitest';

import { recorderDocumentRedirect } from './recorderDocument';

const canIsolate = { supported: true, active: false };

describe('recorderDocumentRedirect', () => {
  it('opens the recorder in the isolated document', () => {
    expect(recorderDocumentRedirect('/', '/scouting/record', '?event=2026arc', canIsolate)).toBe(
      '/record.html#/scouting/record?event=2026arc',
    );
  });

  it('sends every other page back to the main app', () => {
    expect(recorderDocumentRedirect('/record.html', '/team-center', '?team=frc254', { supported: true, active: true })).toBe(
      '/#/team-center?team=frc254',
    );
  });

  it('stays put when already isolated, or when the browser cannot isolate', () => {
    expect(recorderDocumentRedirect('/record.html', '/scouting/record', '', { supported: true, active: true })).toBeNull();
    expect(recorderDocumentRedirect('/', '/scouting/record', '', { supported: false, active: false })).toBeNull();
    expect(recorderDocumentRedirect('/', '/team-center', '', canIsolate)).toBeNull();
  });
});
