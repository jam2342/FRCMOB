import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// The app said the same thing several ways: "QM 12" and "Qual 12", "07:59 AM" and "7:59 AM",
// "Loading..." and "Loading…", four wordings for a team-number box, event boxes labelled on some
// pages and bare on others. Each was fixed once and then drifted back one page at a time, so this
// reads every source file for the shared helpers instead. The browser side (menus, titles, widths
// at phone and desktop size) is e2e/guard-consistency.mjs.
const SRC = dirname(fileURLToPath(import.meta.url));

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    // *.testkit.* files are shared test setup (fixtures like `Qual ${n}`), not UI.
    return /\.(ts|tsx)$/.test(name) && !/\.(test|testkit)\.tsx?$/.test(name) ? [path] : [];
  });
}

const files = sourceFiles(SRC).map((path) => ({ path: relative(SRC, path), source: readFileSync(path, 'utf8') }));

function offenders(pattern: RegExp, allow: readonly string[] = []): string[] {
  return files
    .filter((f) => !allow.includes(f.path))
    .flatMap((f) => f.source.split('\n').map((line, i) => ({ line, at: `${f.path}:${i + 1}` })))
    .filter(({ line }) => pattern.test(line) && !line.trim().startsWith('//'))
    .map(({ at, line }) => `${at}  ${line.trim().slice(0, 100)}`);
}

describe('one way to say each thing', () => {
  it('labels every event box', () => {
    const bare = files.flatMap((f) =>
      (f.source.match(/<EventPicker\b[\s\S]*?\/>/g) ?? [])
        .filter((picker) => !/\blabel=|inputId=/.test(picker))
        .map(() => f.path),
    );
    expect(bare, 'give EventPicker label="Event" (or a FieldFrame label with inputId)').toEqual([]);
  });

  it('writes matches the way the schedule does (QM 12, SF 3-1)', () => {
    expect(offenders(/`Qual \$\{|'Qual '|"Qual "|Semifinal \$\{/), 'use matchLabel / matchLabelFromKey').toEqual([]);
  });

  it('writes times one way', () => {
    expect(offenders(/hour:\s*['"]2-digit['"]/), 'use formatTime / formatDayTime').toEqual([]);
    expect(offenders(/new Date\([^)]*\)\.toLocaleString\(\)/), 'use formatDayTime').toEqual([]);
  });

  it('uses one ellipsis character', () => {
    expect(offenders(/[A-Za-z)]\.\.\.(?=['"`<])/), 'write … not ...').toEqual([]);
  });

  it('asks for a team number one way', () => {
    expect(offenders(/placeholder=["'][^"']*team[^"']*e\.g\./i), 'use TEAM_NUMBER_PLACEHOLDER').toEqual([]);
  });

  it('picks matches with the shared MatchSelect', () => {
    expect(offenders(/<select\b[^>]*(match-select|ap-match)/i), 'use <MatchSelect>').toEqual([]);
  });
});

// One look per kind of thing. Each rule was broken in several places by the 2026-10-18 sweep of
// every page at phone and desktop size, always the same way: a page built its own copy of a
// shared piece. The browser half (what the copies actually render as) is e2e/guard-uniformity.mjs.
describe('one look per kind of thing', () => {
  const tsx = files.filter((f) => f.path.endsWith('.tsx'));

  it('switches views with SegmentedTabs, which picks its own look', () => {
    expect(offenders(/role=["']tablist["']/, ['components/ui/SegmentedTabs.tsx']), 'use <SegmentedTabs>').toEqual([]);
    const styled = tsx.flatMap((f) =>
      (f.source.match(/<SegmentedTabs\b[\s\S]*?\/>/g) ?? [])
        .filter((tag) => /(center-tabs|center-tab-btn|fm-tab-bar|fm-tab)\b/.test(tag) || (/itemClassName=/.test(tag) && !/variant="board"/.test(tag)))
        .map(() => f.path),
    );
    expect(styled, 'SegmentedTabs decides the tab look; pass className for layout only').toEqual([]);
  });

  it('searches events with EventPicker and shows results in its one list', () => {
    expect(offenders(/className=[^>]*\b[a-z-]*search-(dropdown|result)/, ['components/EventPicker.tsx']), 'use <EventPicker>').toEqual([]);
    expect(offenders(/role=["']listbox["']/, ['components/EventPicker.tsx', 'features/palette/CommandPalette.tsx']), 'use <EventPicker>').toEqual([]);
  });

  it('shows empty states with EmptyState', () => {
    expect(offenders(/\bCardEmpty\b/), 'use <EmptyState compact>').toEqual([]);
  });

  it('writes a missing value as "—"', () => {
    // A 1–5 scouting scale has a real "N/A" (not applicable) answer; that is the one exception.
    expect(offenders(/['"`>]N\/A['"`<]/).filter((line) => !/label: 'N\/A', muted/.test(line)), 'use MISSING from utils/formatValue').toEqual([]);
  });

  it('signs a change with signedChange (never "-0.0")', () => {
    expect(offenders(/[<>]=? ?0 \? '\+'/, ['utils/formatValue.ts']), 'use signedChange from utils/formatValue').toEqual([]);
  });

  it('shows stats with one decimal', () => {
    // TBA's ranking score is published with two decimals, so it keeps them.
    expect(offenders(/\bmetric\([^)]*, ?[2-9]\)/).filter((line) => !/ranking_score/.test(line)), 'metric(value) or metric(value, 1)').toEqual([]);
  });

  it('puts a page\'s change/back actions in MobileViewActions', () => {
    // Match Center had its own "Change match" inside the score card and its own "Back to QM 1" in
    // the sidebar, where four other pages use the one row at the top.
    expect(offenders(/>[^<{]*Back to \{|\bmc-(change|back-to)-match\b/), 'use <MobileViewActions>').toEqual([]);
  });

  it('offers fullscreen only on cards that can outgrow themselves', () => {
    expect(offenders(/expandable=\{false\}/), 'fullscreen is off by default; drop the prop').toEqual([]);
    // A card that opts in must hold something wider or taller than a card: a table, a chart, a
    // heat map, a video. Read from the source, because an empty table renders as no table.
    const wide = /<Table\b|Heatmap|LiteStreamEmbed|<iframe|<svg\b|Chart\b|Sparkline|<canvas|DistributionBars|<table\b|ShiftPlay/;
    const plain = tsx.flatMap((f) => {
      const found: string[] = [];
      const open = /<SurfaceCard\b/g;
      for (let match = open.exec(f.source); match; match = open.exec(f.source)) {
        // The opening tag ends at the first '>' outside braces.
        let end = match.index;
        for (let depth = 0; end < f.source.length; end += 1) {
          const c = f.source[end];
          if (c === '{') depth += 1;
          else if (c === '}') depth -= 1;
          else if (c === '>' && depth === 0) break;
        }
        const tag = f.source.slice(match.index, end + 1);
        if (!/\bexpandable\b(?!=\{false\})/.test(tag) || tag.endsWith('/>')) continue;
        // Its body runs to the matching close tag.
        let depth = 1;
        let at = end + 1;
        while (depth > 0) {
          const nextOpen = f.source.indexOf('<SurfaceCard', at);
          const nextClose = f.source.indexOf('</SurfaceCard>', at);
          if (nextClose < 0) break;
          if (nextOpen >= 0 && nextOpen < nextClose) {
            const selfClosing = /^<SurfaceCard\b[^]*?\/>/.test(f.source.slice(nextOpen, f.source.indexOf('>', nextOpen) + 1));
            if (!selfClosing) depth += 1;
            at = nextOpen + 1;
          } else {
            depth -= 1;
            at = nextClose + 1;
          }
        }
        if (!wide.test(f.source.slice(end, at))) found.push(`${f.path}:${f.source.slice(0, match.index).split('\n').length}`);
      }
      return found;
    });
    expect(plain, 'drop `expandable`: nothing in this card outgrows it').toEqual([]);
  });
});
