// Captured frames -> one field path per robot, ready for tap-ID. Shared by the
// breakdown screen and the offline benchmark (scripts/on-device-benchmark.ts), so what
// is measured is exactly what ships.

import { trackletAlliance, stitchTracklets, type Alliance, type StitchOptions } from './trackStitching';
import { assignTrackIds, type RawFrame, type TrackerOptions } from './simpleTracker';
import { produceTrackPoints, type Frame, type TrackPoint } from './trackProduction';

export type RobotPath = {
  pathId: number;
  points: TrackPoint[];
  alliance: Alliance | null; // from bumper colour; null when it never read clearly
  redShare: number;
  blueShare: number;
  fragments: number; // tracker fragments joined into this path
  thumb: Blob | null; // the clearest photo of the robot: biggest, most confident box
};

export type RobotPathOptions = {
  minPoints?: number; // shorter paths are dropped (after stitching)
  stitch?: boolean | StitchOptions;
  tracker?: TrackerOptions;
};

export function buildRobotPaths(rawFrames: RawFrame[], opts: RobotPathOptions = {}): RobotPath[] {
  const minPoints = opts.minPoints ?? 3;
  const tracked = assignTrackIds(rawFrames, opts.tracker);
  const homographyAt = new Map(rawFrames.map((f) => [f.timeSec, f.homography]));
  const frames: Frame[] = tracked.map((tf) => ({
    timeSec: tf.timeSec,
    homography: homographyAt.get(tf.timeSec) ?? null,
    detections: tf.detections,
  }));
  const produced = produceTrackPoints(frames);

  // mean bumper colour and clearest photo per tracker fragment
  const colourSum = new Map<number, { red: number; blue: number; n: number }>();
  const bestThumb = new Map<number, { thumb: Blob; score: number }>();
  for (const frame of tracked) {
    for (const d of frame.detections) {
      if (d.thumb) {
        const score = (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]) * (d.confidence ?? 1);
        const best = bestThumb.get(d.trackId);
        if (!best || score > best.score) bestThumb.set(d.trackId, { thumb: d.thumb, score });
      }
      if (!d.colour) continue;
      const c = colourSum.get(d.trackId) ?? { red: 0, blue: 0, n: 0 };
      c.red += d.colour.red;
      c.blue += d.colour.blue;
      c.n += 1;
      colourSum.set(d.trackId, c);
    }
  }
  const colourOf = (id: number) => {
    const c = colourSum.get(id);
    return c && c.n ? { red: c.red / c.n, blue: c.blue / c.n } : { red: 0, blue: 0 };
  };

  const ids = Object.keys(produced).map(Number);
  const stitchOpts = typeof opts.stitch === 'object' ? opts.stitch : {};
  const robotOf =
    opts.stitch === false
      ? new Map(ids.map((id) => [id, id]))
      : stitchTracklets(
          ids.map((id) => ({
            id,
            points: produced[id].map((p) => ({ timeSec: p.timeSec, x: p.fieldX, y: p.fieldY })),
            ...colourOf(id),
          })),
          stitchOpts,
        );

  type Entry = { points: TrackPoint[]; red: number; blue: number; n: number; fragments: number; thumb: { thumb: Blob; score: number } | null };
  const byRobot = new Map<number, Entry>();
  for (const id of ids) {
    const robot = robotOf.get(id) ?? id;
    const entry = byRobot.get(robot) ?? { points: [], red: 0, blue: 0, n: 0, fragments: 0, thumb: null };
    const pts = produced[id];
    const c = colourOf(id);
    entry.points.push(...pts);
    entry.red += c.red * pts.length;
    entry.blue += c.blue * pts.length;
    entry.n += pts.length;
    entry.fragments += 1;
    const thumb = bestThumb.get(id);
    if (thumb && (!entry.thumb || thumb.score > entry.thumb.score)) entry.thumb = thumb;
    byRobot.set(robot, entry);
  }

  const paths: RobotPath[] = [];
  for (const [pathId, e] of byRobot) {
    if (e.points.length < minPoints) continue;
    e.points.sort((a, b) => a.timeSec - b.timeSec);
    paths.push({
      pathId,
      points: e.points,
      alliance: e.n ? trackletAlliance({ red: e.red / e.n, blue: e.blue / e.n }, stitchOpts) : null,
      redShare: e.n ? e.red / e.n : 0,
      blueShare: e.n ? e.blue / e.n : 0,
      fragments: e.fragments,
      thumb: e.thumb?.thumb ?? null,
    });
  }
  return paths.sort((a, b) => b.points.length - a.points.length);
}
