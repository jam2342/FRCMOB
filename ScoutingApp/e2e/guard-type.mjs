/* Guard 6 — type budget.
 *
 *   node e2e/guard-type.mjs            # check
 *   node e2e/guard-type.mjs --update   # re-record the baseline
 *
 * Guard 5 says a colour may only enter through a custom property. This says the
 * same about size, for the same reason and with worse numbers: the app had 546
 * raw font-size literals against 112 token references, in 59 distinct spellings
 * whose twelve commonest all landed between 9.9px and 13.4px. `0.72rem` and
 * `0.74rem` differ by a third of a pixel. Those were not decisions.
 *
 * Two budgets, because there are two things to drive to zero.
 *
 *   literals  — a raw length in a font-size declaration. The ladder lives in
 *               tokens.css; everything else references it.
 *   legacy    — uses of --font-size-legacy-10 / -11. Those two steps exist only
 *               so the codemod could move 546 declarations while provably
 *               keeping every element within a pixel of where it was. They are
 *               not part of the ladder and the count only goes down.
 *
 * `em` is deliberately not counted. `0.85em` means "smaller than my parent",
 * which is a relationship rather than a size, and a token would change what it
 * says. clamp()/calc()/max()/min() are likewise left alone — they are
 * compositions, and the tokens inside them are already counted by being tokens.
 */
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const SRC = join(ROOT, 'src');
const TOKENS = join(SRC, 'styles', 'tokens.css');
const BASELINE = join(HERE, 'baselines', 'type-budget.json');
const UPDATE = process.argv.includes('--update');

function cssFiles(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) cssFiles(p, out);
    else if (entry.name.endsWith('.css')) out.push(p);
  }
  return out;
}

/* Mask comments first. A `{` inside one corrupts any depth counting, and that
   exact bug silently skipped rules in an earlier sweep of this codebase. */
function scan(css) {
  const masked = css.replace(/\/\*[\s\S]*?\*\//g, (m) => ' '.repeat(m.length));
  let literals = 0;
  let legacy = 0;
  const samples = [];

  const decl = /(^|[;{}])\s*font-size\s*:\s*([^;{}]+)/g;
  let m;
  while ((m = decl.exec(masked)) !== null) {
    const value = m[2].trim();
    if (/--font-size-legacy-/.test(value)) legacy += 1;
    // Compositions, not sizes. clamp()/calc() build a size from tokens, and
    // the two max(16px, …) uses are the iOS zoom-on-focus threshold — a
    // platform constant that stops Safari zooming the page when a scout taps a
    // field mid-match. Neither is a design decision this guard can improve.
    if (value.startsWith('var(') || /\b(clamp|calc|max|min)\(/.test(value)) continue;
    // A bare `em` is a relationship, not a size. `rem` and `px` are sizes.
    if (/(^|[^r\w])[0-9.]+em\b/.test(value) && !/[0-9.]+(rem|px)\b/.test(value)) continue;
    if (!/[0-9.]+(rem|px)\b/.test(value)) continue;
    literals += 1;
    if (samples.length < 5) samples.push(`font-size: ${value.slice(0, 44)}`);
  }
  return { literals, legacy, samples };
}

const current = {};
const samples = {};
for (const file of cssFiles(SRC)) {
  // tokens.css is where a size is allowed to be a number. That is the point of it.
  if (file === TOKENS) continue;
  const rel = relative(ROOT, file);
  const { literals, legacy, samples: s } = scan(readFileSync(file, 'utf8'));
  if (literals || legacy) current[rel] = { literals, legacy };
  if (s.length) samples[rel] = s;
}

const sum = (o, k) => Object.values(o).reduce((a, v) => a + (v[k] || 0), 0);
const totals = { literals: sum(current, 'literals'), legacy: sum(current, 'legacy') };

if (UPDATE || !existsSync(BASELINE)) {
  writeFileSync(BASELINE, `${JSON.stringify(current, null, 2)}\n`);
  console.log(`Baseline written: ${Object.keys(current).length} files, ${totals.literals} raw sizes, ${totals.legacy} legacy-token uses`);
  console.log(`  ${relative(ROOT, BASELINE)}`);
  process.exit(0);
}

const baseline = JSON.parse(readFileSync(BASELINE, 'utf8'));
const over = [];
const under = [];
for (const file of new Set([...Object.keys(baseline), ...Object.keys(current)])) {
  const was = baseline[file] || { literals: 0, legacy: 0 };
  const now = current[file] || { literals: 0, legacy: 0 };
  for (const kind of ['literals', 'legacy']) {
    if (now[kind] > (was[kind] || 0)) over.push({ file, kind, was: was[kind] || 0, now: now[kind] });
    else if (now[kind] < (was[kind] || 0)) under.push({ file, kind, was: was[kind] || 0, now: now[kind] });
  }
}

const baseTotals = { literals: sum(baseline, 'literals'), legacy: sum(baseline, 'legacy') };
console.log('\n' + '─'.repeat(64));
console.log(`Guard 6 — type budget`);
console.log(`  raw font-size literals : ${totals.literals}  (baseline ${baseTotals.literals})`);
console.log(`  --font-size-legacy-*   : ${totals.legacy}  (baseline ${baseTotals.legacy})`);

if (under.length) {
  console.log(`\n${under.length} improvement(s) — re-run with --update to lock the gain in:`);
  for (const u of under) console.log(`  ${u.file}  ${u.kind} ${u.was} -> ${u.now}`);
}

if (!over.length) {
  console.log('\nNo file gained a raw size or a legacy step. ✓');
  process.exit(0);
}

console.log(`\n${over.length} BUDGET(S) EXCEEDED:\n`);
for (const o of over) {
  console.log(`  ${o.file}  ${o.kind}  ${o.was} -> ${o.now}`);
  if (o.kind === 'literals') for (const line of samples[o.file] || []) console.log(`      ${line}`);
  console.log();
}
console.log('Sizes come from the ladder in src/styles/tokens.css — see src/styles/README.md,');
console.log('which gives every step a job. If a size does not have one of those jobs, it does');
console.log('not get a size. --font-size-legacy-10 and -11 are transitional and only shrink.');
process.exit(1);
