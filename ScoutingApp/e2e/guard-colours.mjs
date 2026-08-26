/* Guard 5 — raw colour budget.
 *
 *   node e2e/guard-colours.mjs            # check
 *   node e2e/guard-colours.mjs --update   # re-record the baseline
 *
 * The rule: a colour may only enter the system through a custom property.
 * `--color-danger: #ef4444` is how a colour gets defined; `color: #ef4444` is
 * how it gets copied, and copying is what produced 878 distinct colour values
 * in a product with 74 tokens.
 *
 * It is a budget rather than a flat ban because a flat ban would have failed
 * 809 times the day it landed, and a rule everyone switches off enforces
 * nothing. Each file gets its current count as a ceiling. Adding a literal
 * fails; removing one is reported so the ceiling can be lowered. A file at
 * zero is banned outright, which is where they all end up.
 *
 * Deliberately not covered: literals inside url() — a data-URI SVG carries its
 * own colours and those are image bytes, not CSS values.
 */
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const SRC = join(ROOT, 'src');
const BASELINE = join(HERE, 'baselines', 'colour-budget.json');
const UPDATE = process.argv.includes('--update');

const LITERAL = /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\s*\(/g;

function cssFiles(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) cssFiles(p, out);
    else if (entry.name.endsWith('.css')) out.push(p);
  }
  return out;
}

/* Mask comments and url() payloads, then walk declarations with a simple
   depth counter. Comments are masked FIRST because a `{` inside one otherwise
   corrupts the depth — that exact bug silently skipped rules in an earlier
   sweep of this codebase. */
function scan(css) {
  const masked = css
    .replace(/\/\*[\s\S]*?\*\//g, (m) => ' '.repeat(m.length))
    .replace(/url\([^)]*\)/g, (m) => ' '.repeat(m.length));

  let count = 0;
  const lines = [];
  // Declarations are `prop: value;` inside a block. Custom properties are the
  // definitions, so they are where a literal is allowed to live.
  const decl = /(--)?([-a-zA-Z][\w-]*)\s*:\s*([^;{}]*)[;}]/g;
  let m;
  while ((m = decl.exec(masked)) !== null) {
    if (m[1] === '--') continue;
    const hits = m[3].match(LITERAL);
    if (!hits) continue;
    count += hits.length;
    if (lines.length < 6) {
      lines.push(`${m[2]}: ${m[3].trim().slice(0, 54)}`);
    }
  }
  return { count, lines };
}

const current = {};
const examples = {};
for (const file of cssFiles(SRC)) {
  const rel = relative(ROOT, file);
  const { count, lines } = scan(readFileSync(file, 'utf8'));
  if (count > 0) { current[rel] = count; examples[rel] = lines; }
}
const total = Object.values(current).reduce((a, b) => a + b, 0);

if (UPDATE || !existsSync(BASELINE)) {
  writeFileSync(BASELINE, `${JSON.stringify(current, null, 2)}\n`);
  console.log(`Baseline written: ${Object.keys(current).length} files, ${total} raw colours`);
  console.log(`  ${relative(ROOT, BASELINE)}`);
  process.exit(0);
}

const baseline = JSON.parse(readFileSync(BASELINE, 'utf8'));
const over = [];
const under = [];
for (const file of new Set([...Object.keys(baseline), ...Object.keys(current)])) {
  const was = baseline[file] ?? 0;
  const now = current[file] ?? 0;
  if (now > was) over.push({ file, was, now });
  else if (now < was) under.push({ file, was, now });
}

const baseTotal = Object.values(baseline).reduce((a, b) => a + b, 0);
console.log('\n' + '─'.repeat(64));
console.log(`Guard 5 — raw colour budget: ${total} raw colours across ${Object.keys(current).length} files (baseline ${baseTotal})`);

if (under.length) {
  console.log(`\n${under.length} file(s) improved — re-run with --update to lock the gain in:`);
  for (const u of under) console.log(`  ${u.file}  ${u.was} -> ${u.now}`);
}

if (!over.length) {
  console.log('\nNo file gained a raw colour. ✓');
  process.exit(0);
}

console.log(`\n${over.length} FILE(S) OVER BUDGET:\n`);
for (const o of over) {
  console.log(`  ${o.file}  ${o.was} -> ${o.now}`);
  for (const line of examples[o.file] || []) console.log(`      ${line}`);
  console.log();
}
console.log('Define the colour as a token in src/styles/tokens.css and reference it');
console.log('with var(), or color-mix(in srgb, var(--token) N%, transparent) for a wash.');
console.log();
console.log(`If a file was renamed or split, the budget follows the old path — check the`);
console.log(`total above (${total} now vs ${baseTotal} at baseline) and re-run with --update.`);
process.exit(1);
