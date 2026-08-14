import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const sourcePath = resolve(process.cwd(), 'src', 'pages', 'MatchCenterPage.tsx');
const source = readFileSync(sourcePath, 'utf8');

describe('MatchCenter polling regressions', () => {
  it('uses shared single-flight polling hook instead of interval overlap', () => {
    expect(source).toContain('useSingleFlightPolling');
    expect(source).toContain('run: loadEventData');
    expect(source).toContain('intervalMs: refreshSec * 1000');
    expect(source).not.toContain('window.setInterval(() => {\n      void run();');
  });

  it('keeps URL match query in sync with selected match state', () => {
    expect(source).toContain("normalizeMatchKey(searchParams.get('match')");
    expect(source).toContain('eventKeyFromMatchKey(urlMatchKey);');
    expect(source).toContain('setSelectedMatchKey((prev) => (prev === resolvedMatchKey ? prev : resolvedMatchKey));');
    expect(source).toContain('setActiveTab((prev) => (prev === urlTab ? prev : urlTab));');
    expect(source).not.toContain("if (selectedMatchKey) setSelectedMatchKey('');");
  });

  it('attempts suffix-based match recovery before defaulting to first row', () => {
    expect(source).toContain('const selectedSuffix = normalizedSelectedMatchKey.includes(\'_\')');
    expect(source).toContain('const suffixMatch = scheduleRows.find((row) => {');
    expect(source).toContain('if (suffixMatch) {');
    expect(source).toContain('setSelectedMatchKey(normalizeMatchKey(suffixMatch.match_key, selectedEventKey));');
  });
});
