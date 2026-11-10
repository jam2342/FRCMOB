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

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return cssFiles(path);
    return name.endsWith('.css') ? [path] : [];
  });
}

// Every style rule in a stylesheet, with the at-rule preludes it sits inside.
type CssRule = { selectors: string[]; decls: Map<string, string>; within: string[]; at: string };

function cssRules(path: string): CssRule[] {
  const css = readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));
  const rules: CssRule[] = [];
  const walk = (from: number, to: number, within: string[]) => {
    let i = from;
    while (i < to) {
      const open = css.indexOf('{', i);
      const semi = css.indexOf(';', i);
      if (open === -1 || open >= to) return;
      if (semi !== -1 && semi < open && css.slice(i, semi).trim().startsWith('@')) {
        i = semi + 1;
        continue;
      }
      let depth = 1;
      let close = open + 1;
      for (; depth > 0; close += 1) {
        if (css[close] === '{') depth += 1;
        else if (css[close] === '}') depth -= 1;
      }
      close -= 1;
      const prelude = css.slice(i, open).trim();
      if (prelude.startsWith('@')) {
        walk(open + 1, close, [...within, prelude]);
      } else {
        const decls = new Map<string, string>();
        for (const decl of css.slice(open + 1, close).split(';')) {
          const colon = decl.indexOf(':');
          if (colon > 0) decls.set(decl.slice(0, colon).trim(), decl.slice(colon + 1).trim());
        }
        const selectors: string[] = [];
        let depthParen = 0;
        let current = '';
        for (const ch of prelude) {
          if (ch === '(') depthParen += 1;
          if (ch === ')') depthParen -= 1;
          if (ch === ',' && depthParen === 0) {
            selectors.push(current.trim());
            current = '';
          } else current += ch;
        }
        selectors.push(current.trim());
        const start = i + Math.max(0, css.slice(i, open).search(/\S/));
        rules.push({ selectors, decls, within, at: `${relative(SRC, path)}:${css.slice(0, start).split('\n').length}` });
      }
      i = close + 1;
    }
  };
  walk(0, css.length, []);
  return rules;
}

// The 2026-11-10 sweep found 27 hover looks in the dark theme: buttons lifting 1px, 2px or not
// at all, rows sweeping a shine, cards nobody can click lifting under the pointer, a hover that
// looked like selection. Four looks remain (styles/README.md rule 5, uniform.css "One hover"),
// and the browser half is e2e/guard-uniformity.mjs's hover check.
describe('one hover per kind of thing', () => {
  const hoverRules = cssFiles(SRC)
    .flatMap(cssRules)
    .filter((rule) => rule.selectors.some((s) => s.includes(':hover')));
  const ROW_TINT = 'linear-gradient(var(--row-bg-hover), var(--row-bg-hover))';

  it('only hovers where there is a pointer that can', () => {
    const bare = hoverRules
      .filter((rule) => !rule.within.some((at) => /^@media\b.*\(hover:\s*hover\)/.test(at)))
      .map((rule) => `${rule.at}  ${rule.selectors.join(', ').slice(0, 90)}`);
    expect(bare, 'a phone keeps :hover on what was tapped last; wrap hover in @media (hover: hover)').toEqual([]);
  });

  it('keeps hover apart from selection and focus', () => {
    const mixed = hoverRules
      .filter((rule) => rule.selectors.some((s) => !s.includes(':hover') || s.includes(':focus-visible')))
      .map((rule) => `${rule.at}  ${rule.selectors.join(', ').slice(0, 90)}`);
    expect(mixed, 'a hover rule styles only :hover; selected items and the focus ring have their own').toEqual([]);
  });

  it('changes fill, text or underline, nothing else', () => {
    const moving = hoverRules.flatMap((rule) =>
      [...rule.decls].flatMap(([prop, value]) => {
        const bg = rule.decls.get('background-color') ?? rule.decls.get('background');
        const bad =
          /^(transform|translate|scale|rotate|box-shadow|filter|animation|outline|width|height|margin|padding)$/.test(prop) ||
          (prop.startsWith('border') && !(prop === 'border-color' && value === bg)) ||
          (/gradient\(/.test(value) && value.replace(/\s*!important$/, '') !== ROW_TINT);
        return bad ? [`${rule.at}  ${prop}: ${value.slice(0, 60)}`] : [];
      }),
    );
    expect(moving, 'hover steps a fill (--control-bg-hover, --row-bg-hover), firms text or an underline').toEqual([]);
  });
});

// The same sweep found a green glow under every active nav link and tab, live dots pulsing out
// halo rings, a pulsing "pending" badge, a winner flashing a ring, glowing mascot LEDs and
// coloured light blobs on the page background. Selection is a fill, live is a steady green dot,
// and a shadow says "this floats above", in a neutral colour.
describe('no glows', () => {
  const all = cssFiles(SRC).flatMap(cssRules);
  const GLOW_TOKEN = /var\(--(color|ui)-(accent|success|alliance|warning|danger|info|live)/;

  function colourful(value: string): boolean {
    if (GLOW_TOKEN.test(value)) return true;
    const rgb = value.match(/rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/);
    const hex = value.match(/#([0-9a-f]{6})\b/i);
    const [r, g, b] = rgb
      ? rgb.slice(1, 4).map(Number)
      : hex
        ? [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16))
        : [0, 0, 0];
    return (Math.max(r, g, b) - Math.min(r, g, b)) / 255 > 0.3;
  }

  // Each comma-separated shadow with a blur, minus its colour function.
  function blurredLayers(value: string): string[] {
    const layers: string[] = [];
    let depth = 0;
    let current = '';
    for (const ch of value) {
      if (ch === '(') depth += 1;
      if (ch === ')') depth -= 1;
      if (ch === ',' && depth === 0) {
        layers.push(current);
        current = '';
      } else current += ch;
    }
    layers.push(current);
    return layers.filter((layer) => {
      const lengths = layer.replace(/[a-z-]+\([^)]*(\([^)]*\)[^)]*)*\)/gi, '').match(/-?[\d.]+(px|rem|em)?/g) ?? [];
      return parseFloat(lengths[2] ?? '0') > 0;
    });
  }

  it('casts no coloured shadow', () => {
    const glows = all.flatMap((rule) =>
      [...rule.decls].flatMap(([prop, value]) => {
        const shadows = prop === 'box-shadow' ? blurredLayers(value) : prop === 'filter' ? (value.match(/drop-shadow\([^)]*(\([^)]*\))?[^)]*\)/g) ?? []) : [];
        return shadows.some(colourful) ? [`${rule.at}  ${prop}: ${value.slice(0, 70)}`] : [];
      }),
    );
    expect(glows, 'selection is a fill and a shadow is neutral; no accent or status-coloured glow').toEqual([]);
  });

  // A ring that grows (spread) or a blur that breathes. The win sparks are crisp offset dots.
  it('animates no halo', () => {
    const ringOrBlur = (value: string) =>
      value.split(/,(?![^(]*\))/).some((layer) => {
        const lengths = layer.replace(/[a-z-]+\([^)]*(\([^)]*\)[^)]*)*\)/gi, '').match(/-?[\d.]+(px|rem|em)?/g) ?? [];
        return parseFloat(lengths[2] ?? '0') > 0 || parseFloat(lengths[3] ?? '0') > 0;
      });
    const halos = all
      .filter((rule) => rule.within.some((at) => at.startsWith('@keyframes')))
      .filter((rule) => ringOrBlur(rule.decls.get('box-shadow') ?? '') || /drop-shadow/.test(rule.decls.get('filter') ?? ''))
      .map((rule) => `${rule.at}  ${rule.within.find((at) => at.startsWith('@keyframes'))}`);
    expect([...new Set(halos)], 'a live or pending state is a steady dot, not a pulsing ring').toEqual([]);
  });

  it('paints no light blobs', () => {
    const blobs = all.flatMap((rule) =>
      [...rule.decls].filter(([, value]) => value.includes('radial-gradient(')).map(([prop]) => `${rule.at}  ${prop}`),
    );
    expect(blobs, 'backgrounds are flat surfaces').toEqual([]);
  });
});

// Pages rendered nine weights: 400 to 900, with 620, 760 and 820 among them, each picked for one
// label. Three carry the hierarchy: 400 for reading, 600 for labels and headings, 700 for what
// should stand out. @font-face ranges ("100 700") describe the font file, not text.
describe('three font weights', () => {
  it('sets text at 400, 600 or 700', () => {
    const allowed = /^(400|600|700|normal|bold|inherit|var\(--(font-weight-(normal|semibold|bold)|type-[a-z-]+-weight)\))$/;
    const odd = cssFiles(SRC).flatMap((path) =>
      readFileSync(path, 'utf8').split('\n').flatMap((line, i) => {
        const value = line.match(/font-weight:\s*([^;]+);/)?.[1].trim();
        return value && !allowed.test(value) && !/^\d+ \d+$/.test(value) ? [`${relative(SRC, path)}:${i + 1}  ${value}`] : [];
      }),
    );
    expect(odd, 'use 400, 600 or 700 (or a --font-weight token)').toEqual([]);
  });
});
