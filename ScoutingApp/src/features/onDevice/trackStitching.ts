// Join track fragments into robots after the capture. The frame-to-frame tracker has to
// decide online, so a robot hidden behind another for a second -- or simply missed by
// the detector -- comes back as a new track: a full match produced 66-97 paths for six
// robots, each one a tap for the scout. With the whole match in hand we can do better:
// link a fragment to one that starts after it ends when the robot could have driven the
// distance in the gap and the bumper colour doesn't say otherwise.

export type Alliance = 'red' | 'blue';

type TrackletPoint = { timeSec: number; x: number; y: number }; // field metres

export type Tracklet = {
  id: number;
  points: TrackletPoint[]; // time-ordered
  // mean share of red / blue bumper pixels across the fragment's detections
  red: number;
  blue: number;
};

export type StitchOptions = {
  maxGapSec?: number; // never bridge a longer disappearance
  maxSpeedMps?: number; // robots rarely sustain more; bounds the reachable distance
  positionToleranceM?: number; // projection jitter allowed on top of the reachable distance
  // a fragment's colour counts only when one colour clearly dominates
  minColourShare?: number;
  colourMargin?: number;
};

// Which alliance a fragment's bumpers say it belongs to, or null when unclear (dark
// frames, a bumper facing away, a robot mostly occluded).
export function trackletAlliance(t: Pick<Tracklet, 'red' | 'blue'>, opts: StitchOptions = {}): Alliance | null {
  const minShare = opts.minColourShare ?? 0.04;
  const margin = opts.colourMargin ?? 2;
  if (t.red >= minShare && t.red >= margin * t.blue) return 'red';
  if (t.blue >= minShare && t.blue >= margin * t.red) return 'blue';
  return null;
}

// Link cost of `a` (ending) followed by `b` (starting), or null when implausible. Lower is
// better: distance beyond what the gap alone explains, plus a little for the gap itself
// so that of two equally close candidates the sooner one wins.
function linkCost(a: Tracklet, b: Tracklet, opts: Required<Omit<StitchOptions, 'minColourShare' | 'colourMargin'>>, colour: StitchOptions): number | null {
  const end = a.points[a.points.length - 1];
  const start = b.points[0];
  const gap = start.timeSec - end.timeSec;
  if (gap <= 0 || gap > opts.maxGapSec) return null;
  const d = Math.hypot(start.x - end.x, start.y - end.y);
  if (d > opts.positionToleranceM + opts.maxSpeedMps * gap) return null;
  const ca = trackletAlliance(a, colour);
  const cb = trackletAlliance(b, colour);
  if (ca && cb && ca !== cb) return null;
  return d + 0.25 * gap;
}

// Chain fragments: returns fragment id -> robot id (the id of the chain's first fragment).
// Links are taken cheapest first, each fragment gaining at most one predecessor and one
// successor, so a chain never forks. A chain takes its colour from its members, and a
// link that would join a red chain to a blue one is refused.
export function stitchTracklets(tracklets: Tracklet[], opts: StitchOptions = {}): Map<number, number> {
  // Conservative on purpose: only a robot that reappears within 2 s, near where it
  // vanished -- stricter than the frame-to-frame tracker. Hand labels on 2026cmptx_sf13m1
  // (3 fps) back it: 126 -> 78 paths with 93.5% of labels on the right team (95.1%
  // unstitched), while 4 s / 2.5 m/s (46 paths) fell to 81% and 6 s / 4.5 m/s (28) to 83%.
  const o = {
    maxGapSec: opts.maxGapSec ?? 2,
    maxSpeedMps: opts.maxSpeedMps ?? 2,
    positionToleranceM: opts.positionToleranceM ?? 0.5,
  };
  const usable = tracklets.filter((t) => t.points.length > 0);
  const byId = new Map(usable.map((t) => [t.id, t]));

  const links: { from: number; to: number; cost: number }[] = [];
  for (const a of usable) {
    for (const b of usable) {
      if (a.id === b.id) continue;
      const cost = linkCost(a, b, o, opts);
      if (cost !== null) links.push({ from: a.id, to: b.id, cost });
    }
  }
  links.sort((x, y) => x.cost - y.cost || x.from - y.from || x.to - y.to);

  // union-find over chains, tracking each chain's accumulated colour evidence
  const parent = new Map(usable.map((t) => [t.id, t.id]));
  const chainStart = new Map(usable.map((t) => [t.id, t.points[0].timeSec]));
  const chainEnd = new Map(usable.map((t) => [t.id, t.points[t.points.length - 1].timeSec]));
  const colour = new Map(usable.map((t) => [t.id, { red: t.red * t.points.length, blue: t.blue * t.points.length, n: t.points.length }]));
  const find = (id: number): number => {
    let root = id;
    while (parent.get(root) !== root) root = parent.get(root)!;
    parent.set(id, root);
    return root;
  };
  const chainAlliance = (root: number) => {
    const c = colour.get(root)!;
    return trackletAlliance({ red: c.red / c.n, blue: c.blue / c.n }, opts);
  };

  const hasNext = new Set<number>();
  const hasPrev = new Set<number>();
  for (const { from, to } of links) {
    if (hasNext.has(from) || hasPrev.has(to)) continue;
    const ra = find(from);
    const rb = find(to);
    if (ra === rb) continue;
    // A link must join the current tail to the current head; otherwise two robots
    // can be merged through an internal fragment even while their paths overlap.
    if (chainEnd.get(ra) !== byId.get(from)!.points.at(-1)!.timeSec) continue;
    if (chainStart.get(rb) !== byId.get(to)!.points[0].timeSec) continue;
    if (chainEnd.get(ra)! >= chainStart.get(rb)!) continue;
    const ca = chainAlliance(ra);
    const cb = chainAlliance(rb);
    if (ca && cb && ca !== cb) continue;
    hasNext.add(from);
    hasPrev.add(to);
    // keep the earlier fragment's id as the robot id
    const [keep, drop] = byId.get(ra)!.points[0].timeSec <= byId.get(rb)!.points[0].timeSec ? [ra, rb] : [rb, ra];
    parent.set(drop, keep);
    const ck = colour.get(keep)!;
    const cd = colour.get(drop)!;
    colour.set(keep, { red: ck.red + cd.red, blue: ck.blue + cd.blue, n: ck.n + cd.n });
    chainStart.set(keep, Math.min(chainStart.get(ra)!, chainStart.get(rb)!));
    chainEnd.set(keep, Math.max(chainEnd.get(ra)!, chainEnd.get(rb)!));
  }

  const out = new Map<number, number>();
  for (const t of tracklets) out.set(t.id, byId.has(t.id) ? find(t.id) : t.id);
  return out;
}
