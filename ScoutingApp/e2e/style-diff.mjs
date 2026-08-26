/* Compare two style snapshots.
 *
 *   node e2e/style-diff.mjs before.json after.json
 *
 * Reports how far each colour moved, not just that a string differs. A codemod
 * that rewrites `#16212c` as `var(--color-bg-elevated)` changes the text of
 * every rule it touches while moving nothing anyone can see; the only question
 * worth answering is how many changes land above the just-noticeable
 * difference of about 2.3.
 */
import { readFileSync } from 'node:fs';
import { parse, de2000 } from './lib/colour.mjs';

const [beforeFile, afterFile] = process.argv.slice(2);
if (!afterFile) {
  console.error('usage: node e2e/style-diff.mjs <before.json> <after.json>');
  process.exit(2);
}
const a = JSON.parse(readFileSync(beforeFile, 'utf8'));
const b = JSON.parse(readFileSync(afterFile, 'utf8'));

/* box-shadow carries colours inside a longer value; compare the colours it
   contains rather than the whole string. */
const COLOUR = /#[0-9a-fA-F]{3,8}\b|\bcolor\(\s*srgb[^)]*\)|\b(?:rgba?)\s*\([^)]*\)/g;

function magnitude(va, vb) {
  const ca = va.match(COLOUR) || [];
  const cb = vb.match(COLOUR) || [];
  if (ca.length !== cb.length || ca.length === 0) return Infinity;
  let worst = 0;
  for (let i = 0; i < ca.length; i++) {
    const pa = parse(ca[i]);
    const pb = parse(cb[i]);
    if (!pa || !pb) return Infinity;
    // An alpha change is not something deltaE measures, so treat it separately:
    // 0.01 of alpha is nothing, more than that is a real change.
    if (Math.abs(pa[3] - pb[3]) > 0.01) return Infinity;
    worst = Math.max(worst, de2000(pa, pb));
  }
  return worst;
}

let common = 0, onlyA = 0, onlyB = 0;
const buckets = { identical: 0, imperceptible: 0, jnd: 0, visible: 0 };
const byProp = new Map();
const visible = [];

/* Type is measured in pixels, not in colour space, so it gets its own budget.
   The colour codemod's pass condition was a diff of zero. The type codemod's is
   that everything moved, but by at most a pixel — that is what makes it a
   rounding pass onto the ladder rather than an accidental redesign. Anything
   deliberately promoted to a hero blows past this, which is the point: it
   should be listed by name and be a short list. */
const TYPE_PROPS = new Set(['fontSize', 'fontWeight']);
const TYPE_BUDGET_PX = 1.0;
const type = { identical: 0, withinBudget: 0, overBudget: 0 };
const typeOver = [];
const px = (v) => { const n = Number.parseFloat(String(v)); return Number.isFinite(n) ? n : null; };

for (const page of new Set([...Object.keys(a), ...Object.keys(b)])) {
  const A = a[page] || {};
  const B = b[page] || {};
  for (const key of new Set([...Object.keys(A), ...Object.keys(B)])) {
    if (!(key in A)) { onlyB++; continue; }
    if (!(key in B)) { onlyA++; continue; }
    common++;
    for (const prop of new Set([...Object.keys(A[key]), ...Object.keys(B[key])])) {
      const va = A[key][prop];
      const vb = B[key][prop];

      if (TYPE_PROPS.has(prop)) {
        if (va === vb) { type.identical++; continue; }
        const na = px(va), nb = px(vb);
        const moved = na === null || nb === null ? Infinity : Math.abs(nb - na);
        if (moved <= TYPE_BUDGET_PX) type.withinBudget++;
        else {
          type.overBudget++;
          if (typeOver.length < 60) typeOver.push({ page, key, prop, va, vb, moved });
        }
        continue;
      }

      if (va === vb) { buckets.identical++; continue; }
      if (va === undefined || vb === undefined) {
        buckets.visible++;
        byProp.set(prop, (byProp.get(prop) || 0) + 1);
        if (visible.length < 40) visible.push({ page, key, prop, va: va ?? '(unset)', vb: vb ?? '(unset)', d: Infinity });
        continue;
      }
      const d = magnitude(va, vb);
      const k = d < 1 ? 'imperceptible' : d < 2.3 ? 'jnd' : 'visible';
      buckets[k]++;
      if (k === 'visible') {
        byProp.set(prop, (byProp.get(prop) || 0) + 1);
        if (visible.length < 40) visible.push({ page, key, prop, va, vb, d });
      }
    }
  }
}

console.log(`elements compared: ${common}   (only-before ${onlyA}, only-after ${onlyB})\n`);
for (const [k, v] of Object.entries(buckets)) console.log(`  ${k.padEnd(15)} ${String(v).padStart(7)}`);

if (buckets.visible === 0) {
  console.log('\nNothing changed above the just-noticeable difference. ✓');
} else {
  console.log(`\n${buckets.visible} visible change(s), by property:`);
  [...byProp.entries()].sort((x, y) => y[1] - x[1]).forEach(([p, n]) => console.log(`  ${p.padEnd(22)} ${n}`));
  console.log('\nsamples:');
  for (const v of visible) {
    const d = v.d === Infinity ? 'n/a' : v.d.toFixed(1);
    console.log(`  ${v.page}`);
    console.log(`      ${v.key.slice(-72)}`);
    console.log(`      ${v.prop}: ${v.va}  ->  ${v.vb}   (ΔE ${d})\n`);
  }
}

/* ── type ─────────────────────────────────────────────────────────── */
console.log('\n' + '─'.repeat(64));
console.log('type (px budget, not colour space)');
console.log(`  unchanged        ${String(type.identical).padStart(7)}`);
console.log(`  moved <= ${TYPE_BUDGET_PX}px   ${String(type.withinBudget).padStart(7)}`);
console.log(`  moved  > ${TYPE_BUDGET_PX}px   ${String(type.overBudget).padStart(7)}`);

if (type.overBudget === 0) {
  console.log('\nNo element moved more than a pixel. ✓');
} else {
  const byEl = new Map();
  for (const t of typeOver) {
    const k = `${t.prop}: ${t.va} -> ${t.vb}`;
    byEl.set(k, (byEl.get(k) || 0) + 1);
  }
  console.log(`\n${type.overBudget} over budget — each should be a deliberate promotion:`);
  for (const [k, n] of [...byEl.entries()].sort((x, y) => y[1] - x[1])) {
    console.log(`  ${String(n).padStart(4)}x  ${k}`);
  }
  console.log('\nsamples:');
  for (const t of typeOver.slice(0, 8)) {
    console.log(`  ${t.page}`);
    console.log(`      ${t.key.slice(-72)}`);
    console.log(`      ${t.prop}: ${t.va} -> ${t.vb}  (${t.moved === Infinity ? 'n/a' : t.moved.toFixed(2) + 'px'})\n`);
  }
}
