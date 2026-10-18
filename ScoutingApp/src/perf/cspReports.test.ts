import { describe, expect, it } from 'vitest';
import { describeViolation } from './cspReports';

const event = (overrides: Partial<Record<'effectiveDirective' | 'violatedDirective' | 'blockedURI' | 'sourceFile', string>>) => ({
  effectiveDirective: 'frame-src',
  violatedDirective: 'frame-src',
  blockedURI: 'https://custom-player.example/live/abc?token=secret',
  sourceFile: '',
  ...overrides,
});

describe('describeViolation', () => {
  it('keeps only the route pattern, directive and blocked origin', () => {
    expect(describeViolation(event({}), '#/match-center?event=2026arc&match=2026arc_qm1')).toEqual({
      page: '/match-center',
      directive: 'frame-src',
      blocked: 'https://custom-player.example',
    });
  });

  it('names inline, data and blob blocks without their contents', () => {
    expect(describeViolation(event({ effectiveDirective: 'script-src-elem', blockedURI: 'inline' }), '#/home')?.blocked).toBe('inline');
    expect(describeViolation(event({ effectiveDirective: 'img-src', blockedURI: 'data:image/png;base64,AAAA' }), '#/home')?.blocked).toBe('data');
    expect(describeViolation(event({ effectiveDirective: 'worker-src', blockedURI: 'blob:https://x/1-2' }), '#/home')?.blocked).toBe('blob');
  });

  it("ignores browser extensions, whose blocks aren't the site's to fix", () => {
    expect(describeViolation(event({ blockedURI: 'chrome-extension://abc/inject.js' }), '#/home')).toBeNull();
    expect(describeViolation(event({ blockedURI: 'inline', sourceFile: 'moz-extension://abc/content.js' }), '#/home')).toBeNull();
  });
});
