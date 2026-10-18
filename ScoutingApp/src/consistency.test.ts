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
    return /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
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
