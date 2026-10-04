// Minimal frame-to-frame association — the in-browser stand-in for ByteTrack. The JS
// pipeline has no port of ByteTrack, but produceTrackPoints expects detections already
// carrying a stable trackId, so this greedily links each frame's boxes to the nearest
// recent track after projecting floor contacts into stabilized field coordinates.

import type { Bbox, Detection } from './trackProduction';
import { type BumperColour } from './bumperColour';
import { type Mat3, projectPoint } from './homography';
import { FIELD_LENGTH_M, FIELD_WIDTH_M } from './fieldZones';
import { trackletAlliance } from './trackStitching';

export type RawDetection = { bbox: Bbox; confidence?: number; colour?: BumperColour; thumb?: Blob };
export type RawFrame = { timeSec: number; homography: Mat3 | null; detections: RawDetection[] };
export type TrackedFrame = { timeSec: number; detections: Detection[] };

export type TrackerOptions = {
  maxSpeedMps?: number;
  positionToleranceM?: number;
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
  // About an FRC robot's top speed. 6 m/s let a track jump to a neighbour: against hand
  // labels on 2026cmptx_sf13m1 (3 fps, stitched) 4.5 put 44/46 labels on the right path
  // with 2 mixed paths, versus 43/46 and 3 at 6 m/s.
  const maxSpeed = opts.maxSpeedMps ?? 4.5;
  const positionTolerance = opts.positionToleranceM ?? 0.75;
  const maxGap = opts.maxGapSec ?? 1.0;

  type Active = { id: number; field: [number, number]; lastTime: number; red: number; blue: number; colourN: number };
  let active: Active[] = [];
  let nextId = 0;

  const out: TrackedFrame[] = [];
  for (const frame of [...frames].sort((a, b) => a.timeSec - b.timeSec)) {
    // retire tracks not seen within maxGap
    active = active.filter((t) => frame.timeSec - t.lastTime <= maxGap);

    // rank all (detection, track) pairs by distance, assign greedily (each side once)
    const contacts = frame.detections.map((d): [number, number] | null => {
      if (!frame.homography) return null;
      const [x, y] = floorContact(d.bbox);
      try {
        const field = projectPoint(frame.homography, x, y);
        if (field.x < -0.5 || field.x > FIELD_LENGTH_M + 0.5 || field.y < -0.5 || field.y > FIELD_WIDTH_M + 0.5) return null;
        return [field.x, field.y];
      } catch {
        return null;
      }
    });
    const pairs: { di: number; ti: number; d: number }[] = [];
    contacts.forEach((c, di) => {
      if (!c) return;
      active.forEach((t, ti) => {
        const elapsed = frame.timeSec - t.lastTime;
        const d = dist(c, t.field);
        const prior = trackletAlliance({ red: t.red / Math.max(1, t.colourN), blue: t.blue / Math.max(1, t.colourN) });
        const current = frame.detections[di].colour && trackletAlliance(frame.detections[di].colour);
        if (prior && current && prior !== current) return;
        if (elapsed > 0 && d <= positionTolerance + maxSpeed * elapsed) {
          pairs.push({ di, ti, d });
        }
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
      active[ti].field = contacts[di] as [number, number];
      active[ti].lastTime = frame.timeSec;
      const colour = frame.detections[di].colour;
      if (colour) {
        active[ti].red += colour.red;
        active[ti].blue += colour.blue;
        active[ti].colourN += 1;
      }
    }

    const detections: Detection[] = frame.detections.flatMap((d, di) => {
      if (!contacts[di]) return [];
      let id = assigned[di];
      if (id === null) {
        id = nextId++;
        const field = contacts[di];
        if (field) active.push({ id, field, lastTime: frame.timeSec, red: d.colour?.red ?? 0, blue: d.colour?.blue ?? 0, colourN: d.colour ? 1 : 0 });
      }
      return [{ trackId: id, bbox: d.bbox, confidence: d.confidence, colour: d.colour, thumb: d.thumb }];
    });
    out.push({ timeSec: frame.timeSec, detections });
  }
  return out;
}
