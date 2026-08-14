// Minimal frame-to-frame association — the in-browser stand-in for ByteTrack. The JS
// pipeline has no port of ByteTrack, but produceTrackPoints expects detections already
// carrying a stable trackId, so this greedily links each frame's boxes to the nearest
// recent track by floor-contact pixel distance. Honest and light: enough to stitch a
// stabilized handheld clip's per-frame detections into per-robot tracks; OCR voting /
// tap-ID resolve identity afterwards, and fragmentation is expected (hence both).

import type { Bbox, Detection } from './trackProduction';

export type RawDetection = { bbox: Bbox; confidence?: number };
export type RawFrame = { timeSec: number; detections: RawDetection[] };
export type TrackedFrame = { timeSec: number; detections: Detection[] };

export type TrackerOptions = {
  maxDistPx?: number; // max floor-contact jump to keep the same track between samples
  maxGapSec?: number; // a track unseen longer than this is retired (new id on return)
};

function floorContact(bbox: Bbox): [number, number] {
  return [(bbox[0] + bbox[2]) / 2, bbox[3]]; // bottom-centre, on the field plane
}

function dist(a: [number, number], b: [number, number]): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

// Assign a stable trackId to every detection across the capture. Greedy nearest-contact
// matching per frame: each detection claims the closest still-unclaimed active track
// within maxDistPx, else starts a new track. Deterministic given sorted input.
export function assignTrackIds(frames: RawFrame[], opts: TrackerOptions = {}): TrackedFrame[] {
  const maxDist = opts.maxDistPx ?? 150;
  const maxGap = opts.maxGapSec ?? 1.0;

  type Active = { id: number; contact: [number, number]; lastTime: number };
  let active: Active[] = [];
  let nextId = 0;

  const out: TrackedFrame[] = [];
  for (const frame of [...frames].sort((a, b) => a.timeSec - b.timeSec)) {
    // retire tracks not seen within maxGap
    active = active.filter((t) => frame.timeSec - t.lastTime <= maxGap);

    // rank all (detection, track) pairs by distance, assign greedily (each side once)
    const contacts = frame.detections.map((d) => floorContact(d.bbox));
    const pairs: { di: number; ti: number; d: number }[] = [];
    contacts.forEach((c, di) => {
      active.forEach((t, ti) => {
        const d = dist(c, t.contact);
        if (d <= maxDist) pairs.push({ di, ti, d });
      });
    });
    pairs.sort((a, b) => a.d - b.d);

    const detTaken = new Set<number>();
    const trackTaken = new Set<number>();
    const assigned: (number | null)[] = contacts.map(() => null);
    for (const { di, ti } of pairs) {
      if (detTaken.has(di) || trackTaken.has(ti)) continue;
      detTaken.add(di);
      trackTaken.add(ti);
      assigned[di] = active[ti].id;
      active[ti].contact = contacts[di];
      active[ti].lastTime = frame.timeSec;
    }

    const detections: Detection[] = frame.detections.map((d, di) => {
      let id = assigned[di];
      if (id === null) {
        id = nextId++;
        active.push({ id, contact: contacts[di], lastTime: frame.timeSec });
      }
      return { trackId: id, bbox: d.bbox, confidence: d.confidence };
    });
    out.push({ timeSec: frame.timeSec, detections });
  }
  return out;
}
