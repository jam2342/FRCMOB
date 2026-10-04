// "Probably the same robot" suggestions for the identify step. Automatic stitching stays
// conservative: joining fragments across longer gaps mislabels about one position in
// six (hand labels, 2026cmptx_sf13m1). A scout looking at the photos can tell a wrong
// guess at a glance, so those looser links are offered instead of applied: assign one
// path and the app proposes its likely continuations, one tap to accept or dismiss.
// Robot colour signatures were tried as extra evidence and don't separate same-alliance
// robots at stands-camera resolution (AUC ~0.65), so this is position, time and bumper
// colour only.

export type Alliance = 'red' | 'blue';

export type SuggestPath = {
  pathId: number;
  points: { timeSec: number; fieldX: number; fieldY: number }[]; // time-ordered
  alliance: Alliance | null;
};

export type SuggestOptions = {
  maxGapSec?: number; // longest disappearance bridged
  maxSpeedMps?: number; // fastest a robot is assumed to drive while unseen
  positionToleranceM?: number; // projection jitter on top of the reachable distance
  // a link is suggested only when no rival candidate is within this much cost of it
  ambiguityMarginM?: number;
};

type Limits = Required<SuggestOptions>;

const DEFAULTS: Limits = { maxGapSec: 6, maxSpeedMps: 4.5, positionToleranceM: 1, ambiguityMarginM: 1 };

function cost(a: SuggestPath, b: SuggestPath, o: Limits): number | null {
  const end = a.points[a.points.length - 1];
  const start = b.points[0];
  const gap = start.timeSec - end.timeSec;
  if (gap <= 0 || gap > o.maxGapSec) return null;
  if (a.alliance && b.alliance && a.alliance !== b.alliance) return null;
  const d = Math.hypot(start.fieldX - end.fieldX, start.fieldY - end.fieldY);
  if (d > o.positionToleranceM + o.maxSpeedMps * gap) return null;
  return d + 0.25 * gap;
}

// The single clear best of `costs` (lowest), or null when empty or a rival is too close.
function clearBest(costs: { id: number; cost: number }[], margin: number): number | null {
  if (costs.length === 0) return null;
  const sorted = [...costs].sort((x, y) => x.cost - y.cost || x.id - y.id);
  if (sorted.length > 1 && sorted[1].cost - sorted[0].cost < margin) return null;
  return sorted[0].id;
}

// Chains of paths that look like one robot continuing: a links to b only when b is a's
// clear best successor *and* a is b's clear best predecessor. Each chain is time-ordered.
export function suggestionGroups(paths: SuggestPath[], opts: SuggestOptions = {}): number[][] {
  const o: Limits = { ...DEFAULTS, ...Object.fromEntries(Object.entries(opts).filter(([, v]) => v !== undefined)) };
  const usable = paths.filter((p) => p.points.length > 0);
  const succ = new Map<number, { id: number; cost: number }[]>();
  const pred = new Map<number, { id: number; cost: number }[]>();
  for (const a of usable) {
    for (const b of usable) {
      if (a.pathId === b.pathId) continue;
      const c = cost(a, b, o);
      if (c === null) continue;
      (succ.get(a.pathId) ?? succ.set(a.pathId, []).get(a.pathId)!).push({ id: b.pathId, cost: c });
      (pred.get(b.pathId) ?? pred.set(b.pathId, []).get(b.pathId)!).push({ id: a.pathId, cost: c });
    }
  }
  const next = new Map<number, number>();
  const hasPrev = new Set<number>();
  for (const a of usable) {
    const b = clearBest(succ.get(a.pathId) ?? [], o.ambiguityMarginM);
    if (b === null) continue;
    if (clearBest(pred.get(b) ?? [], o.ambiguityMarginM) !== a.pathId) continue;
    next.set(a.pathId, b);
    hasPrev.add(b);
  }
  const groups: number[][] = [];
  for (const p of usable) {
    if (hasPrev.has(p.pathId)) continue;
    const chain = [p.pathId];
    let cur = next.get(p.pathId);
    while (cur !== undefined && !chain.includes(cur)) {
      chain.push(cur);
      cur = next.get(cur);
    }
    groups.push(chain);
  }
  return groups;
}

// Paths to offer after `seedId` is assigned `teamKey`: the rest of its chain that is still
// unassigned, minus any that would put the team in two places at once.
export function suggestionsFor(
  paths: SuggestPath[],
  groups: number[][],
  seedId: number,
  teamKey: string,
  identities: Record<number, string>,
): number[] {
  const group = groups.find((g) => g.includes(seedId));
  if (!group || !teamKey) return [];
  const byId = new Map(paths.map((p) => [p.pathId, p]));
  const span = (id: number): [number, number] => {
    const pts = byId.get(id)?.points ?? [];
    return pts.length ? [pts[0].timeSec, pts[pts.length - 1].timeSec] : [0, -1];
  };
  const teamSpans = Object.entries(identities)
    .filter(([id, team]) => team === teamKey && Number(id) !== seedId)
    .map(([id]) => span(Number(id)))
    .concat([span(seedId)]);
  const out: number[] = [];
  for (const id of group) {
    if (id === seedId || identities[id]) continue;
    const [s, e] = span(id);
    if (teamSpans.some(([ts, te]) => s <= te && ts <= e)) continue;
    out.push(id);
    teamSpans.push([s, e]);
  }
  return out;
}
