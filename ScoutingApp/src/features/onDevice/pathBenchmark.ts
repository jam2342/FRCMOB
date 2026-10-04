// Score a breakdown's robot paths against reference paths for the same match. Used by
// scripts/on-device-benchmark.ts to check that a faster pipeline still finds the same
// robots in the same places, and how many paths a scout would have to label.

import { type TrackPoint } from './trackProduction';

export type PathScore = {
  paths: number;
  referencePoints: number; // reference robot positions at the sampled instants
  coverage: number; // share of those the breakdown found (within matchDistM)
  medianErrorM: number | null;
  purity: number; // share of matched points on their path's majority robot
  attributed: number; // share of reference points credited correctly if every path is labelled
  tapsFor90: number; // paths to label, largest first, to credit 90% of what labelling all would
};

type Keyed = { path: number; p: TrackPoint };

function byInstant(paths: Record<number, TrackPoint[]>, tolSec: number): Map<number, Keyed[]> {
  const out = new Map<number, Keyed[]>();
  for (const [id, pts] of Object.entries(paths)) {
    for (const p of pts) {
      const k = Math.round(p.timeSec / tolSec);
      const list = out.get(k) ?? [];
      list.push({ path: Number(id), p });
      out.set(k, list);
    }
  }
  return out;
}

export function scorePaths(
  candidate: Record<number, TrackPoint[]>,
  reference: Record<number, TrackPoint[]>,
  sampledTimes: number[],
  opts: { matchDistM?: number; timeTolSec?: number } = {},
): PathScore {
  const matchDist = opts.matchDistM ?? 1.0;
  const tol = opts.timeTolSec ?? 0.05;
  const cand = byInstant(candidate, tol);
  const ref = byInstant(reference, tol);

  let referencePoints = 0;
  const errors: number[] = [];
  const votes = new Map<number, Map<number, number>>(); // candidate path -> ref robot -> count
  for (const t of new Set(sampledTimes.map((s) => Math.round(s / tol)))) {
    const r = ref.get(t) ?? [];
    const c = cand.get(t) ?? [];
    referencePoints += r.length;
    const pairs: { ci: number; ri: number; d: number }[] = [];
    c.forEach((cp, ci) =>
      r.forEach((rp, ri) => {
        const d = Math.hypot(cp.p.fieldX - rp.p.fieldX, cp.p.fieldY - rp.p.fieldY);
        if (d <= matchDist) pairs.push({ ci, ri, d });
      }),
    );
    pairs.sort((a, b) => a.d - b.d);
    const cTaken = new Set<number>();
    const rTaken = new Set<number>();
    for (const { ci, ri, d } of pairs) {
      if (cTaken.has(ci) || rTaken.has(ri)) continue;
      cTaken.add(ci);
      rTaken.add(ri);
      errors.push(d);
      const v = votes.get(c[ci].path) ?? new Map<number, number>();
      v.set(r[ri].path, (v.get(r[ri].path) ?? 0) + 1);
      votes.set(c[ci].path, v);
    }
  }

  let matched = 0;
  const majorities: number[] = [];
  for (const v of votes.values()) {
    const counts = [...v.values()];
    matched += counts.reduce((a, b) => a + b, 0);
    majorities.push(Math.max(...counts));
  }
  majorities.sort((a, b) => b - a);
  const credited = majorities.reduce((a, b) => a + b, 0);
  let taps = 0;
  for (let acc = 0; taps < majorities.length && acc < 0.9 * credited; taps++) acc += majorities[taps];

  errors.sort((a, b) => a - b);
  return {
    paths: Object.keys(candidate).length,
    referencePoints,
    coverage: referencePoints ? matched / referencePoints : 0,
    medianErrorM: errors.length ? errors[Math.floor(errors.length / 2)] : null,
    purity: matched ? credited / matched : 0,
    attributed: referencePoints ? credited / referencePoints : 0,
    tapsFor90: taps,
  };
}

export type RobotLabel = { timeSec: number; fieldX: number; fieldY: number; team: string };

export type LabelScore = {
  labels: number;
  found: number; // labels that landed on some path (within matchDistM, timeTolSec)
  correct: number; // found labels whose path's majority team is theirs
  identityAccuracy: number; // correct / found
  mixedPaths: number; // paths carrying labels of two or more teams
};

// Hand labels (a team per robot at a few instants) against a breakdown's paths: a scout
// who labels each path with its majority team gets `identityAccuracy` of these right.
export function scoreAgainstLabels(
  candidate: Record<number, TrackPoint[]>,
  labels: RobotLabel[],
  opts: { matchDistM?: number; timeTolSec?: number } = {},
): LabelScore {
  const matchDist = opts.matchDistM ?? 1.0;
  const tol = opts.timeTolSec ?? 0.2;
  const onPath = new Map<number, string[]>();
  let found = 0;
  for (const label of labels) {
    let best: { path: number; d: number } | null = null;
    for (const [id, pts] of Object.entries(candidate)) {
      for (const p of pts) {
        if (Math.abs(p.timeSec - label.timeSec) > tol) continue;
        const d = Math.hypot(p.fieldX - label.fieldX, p.fieldY - label.fieldY);
        if (d <= matchDist && (!best || d < best.d)) best = { path: Number(id), d };
      }
    }
    if (!best) continue;
    found += 1;
    const list = onPath.get(best.path) ?? [];
    list.push(label.team);
    onPath.set(best.path, list);
  }
  let correct = 0;
  let mixedPaths = 0;
  for (const teams of onPath.values()) {
    const counts = new Map<string, number>();
    for (const t of teams) counts.set(t, (counts.get(t) ?? 0) + 1);
    correct += Math.max(...counts.values());
    if (counts.size > 1) mixedPaths += 1;
  }
  return { labels: labels.length, found, correct, identityAccuracy: found ? correct / found : 0, mixedPaths };
}
