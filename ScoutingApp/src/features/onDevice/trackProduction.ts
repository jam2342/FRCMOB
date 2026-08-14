// In-browser track production — mirror of on_device.produce_track_points /
// assemble_points_by_team. Turns per-frame detections + a per-frame homography into the
// per-team field-coordinate TrackPoint stream the shift-play analysis consumes. Pure
// (no ort/canvas), so it unit-tests cleanly; the same shape syncs to the server worker.

import { type Mat3, projectPoint } from './homography';
import { classifyZone } from './fieldZones';

export type TrackPoint = {
  timeSec: number;
  fieldX: number;
  fieldY: number;
  zoneKey: string | null;
  speedMps: number | null;
};

export type Bbox = [number, number, number, number]; // x1,y1,x2,y2 image px
export type Detection = { trackId: number; bbox: Bbox; confidence?: number };
export type Frame = { timeSec: number; homography: Mat3 | null; detections: Detection[] };

export type ProduceOptions = {
  minDetectionConfidence?: number;
  maxPoseStalenessSec?: number | null;
  maxSpeedMps?: number | null;
  smoothWindow?: number;
};

const MAX_SPEED_DT_SEC = 1.0;

function floorContact(bbox: Bbox): [number, number] {
  return [(bbox[0] + bbox[2]) / 2, bbox[3]];
}

function median(values: number[]): number {
  const s = [...values].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function dist(a: TrackPoint, b: TrackPoint): number {
  return Math.hypot(b.fieldX - a.fieldX, b.fieldY - a.fieldY);
}

// Drop a one-frame there-and-back projection glitch (jumps off both neighbours faster
// than a robot can move while the neighbours stay mutually reachable). Sustained moves
// are kept.
function rejectVelocitySpikes(points: TrackPoint[], maxSpeedMps: number): TrackPoint[] {
  if (points.length < 3) return points;
  const kept: TrackPoint[] = [points[0]];
  for (let i = 1; i < points.length - 1; i++) {
    const prev = kept[kept.length - 1];
    const curr = points[i];
    const next = points[i + 1];
    const dtIn = curr.timeSec - prev.timeSec;
    const dtOut = next.timeSec - curr.timeSec;
    const dtSpan = next.timeSec - prev.timeSec;
    if (
      dtIn > 0 &&
      dtOut > 0 &&
      dtSpan > 0 &&
      dist(prev, curr) / dtIn > maxSpeedMps &&
      dist(curr, next) / dtOut > maxSpeedMps &&
      dist(prev, next) / dtSpan <= maxSpeedMps
    ) {
      continue; // spike: skip curr, keep prev as anchor
    }
    kept.push(curr);
  }
  kept.push(points[points.length - 1]);
  return kept;
}

// Median-filter each point's field position to damp jitter; re-tag zone from the
// smoothed position. Coordinates are snapshotted first so the window isn't self-polluting.
function medianSmooth(points: TrackPoint[], window: number): void {
  if (window < 3 || points.length < 3) return;
  const xs = points.map((p) => p.fieldX);
  const ys = points.map((p) => p.fieldY);
  const half = Math.floor(window / 2);
  for (let i = 0; i < points.length; i++) {
    const lo = Math.max(0, i - half);
    const hi = Math.min(points.length, i + half + 1);
    points[i].fieldX = median(xs.slice(lo, hi));
    points[i].fieldY = median(ys.slice(lo, hi));
    points[i].zoneKey = classifyZone(points[i].fieldX, points[i].fieldY);
  }
}

function fillSpeeds(points: TrackPoint[]): void {
  points.sort((a, b) => a.timeSec - b.timeSec);
  for (let i = 1; i < points.length; i++) {
    const dt = points[i].timeSec - points[i - 1].timeSec;
    if (dt <= 0 || dt > MAX_SPEED_DT_SEC) continue;
    points[i].speedMps = dist(points[i - 1], points[i]) / dt;
  }
}

export function produceTrackPoints(
  frames: Frame[],
  opts: ProduceOptions = {},
): Record<number, TrackPoint[]> {
  const minConf = opts.minDetectionConfidence ?? 0;
  const maxStale = opts.maxPoseStalenessSec === undefined ? 0.5 : opts.maxPoseStalenessSec;
  const maxSpeed = opts.maxSpeedMps === undefined ? 6 : opts.maxSpeedMps;
  const smoothWindow = opts.smoothWindow ?? 3;

  const byTrack: Record<number, TrackPoint[]> = {};
  let lastH: Mat3 | null = null;
  let lastHTime: number | null = null;

  for (const frame of [...frames].sort((a, b) => a.timeSec - b.timeSec)) {
    let h = frame.homography;
    if (h === null) {
      if (maxStale !== null && lastH !== null && lastHTime !== null && frame.timeSec - lastHTime <= maxStale) {
        h = lastH;
      } else {
        continue;
      }
    } else {
      lastH = h;
      lastHTime = frame.timeSec;
    }
    for (const det of frame.detections) {
      if ((det.confidence ?? 1) < minConf) continue;
      const [cx, by] = floorContact(det.bbox);
      let p;
      try {
        p = projectPoint(h, cx, by);
      } catch {
        continue;
      }
      (byTrack[det.trackId] ??= []).push({
        timeSec: frame.timeSec,
        fieldX: p.x,
        fieldY: p.y,
        zoneKey: classifyZone(p.x, p.y),
        speedMps: null,
      });
    }
  }

  for (const trackId of Object.keys(byTrack)) {
    let pts = byTrack[+trackId];
    pts.sort((a, b) => a.timeSec - b.timeSec);
    if (maxSpeed !== null) pts = rejectVelocitySpikes(pts, maxSpeed);
    if (smoothWindow >= 3) medianSmooth(pts, smoothWindow);
    fillSpeeds(pts);
    byTrack[+trackId] = pts;
  }
  return byTrack;
}

// Relabel resolved tracks by team_key (merging track fragments of the same robot);
// tracks with no resolved identity are dropped (the UI surfaces them for tap-ID).
export function assemblePointsByTeam(
  trackPoints: Record<number, TrackPoint[]>,
  identities: Record<number, string | null>,
): Record<string, TrackPoint[]> {
  const byTeam: Record<string, TrackPoint[]> = {};
  for (const [trackId, points] of Object.entries(trackPoints)) {
    const teamKey = identities[+trackId];
    if (!teamKey) continue;
    const key = String(teamKey).trim().toLowerCase();
    (byTeam[key] ??= []).push(...points);
  }
  for (const key of Object.keys(byTeam)) byTeam[key].sort((a, b) => a.timeSec - b.timeSec);
  return byTeam;
}
