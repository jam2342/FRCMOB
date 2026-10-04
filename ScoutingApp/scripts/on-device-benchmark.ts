// Speed/accuracy benchmark for the on-device breakdown.
//
//   npx vite-node scripts/on-device-benchmark.ts <detections.json> [reference-labels.json]
//
// Data for one match is committed in scripts/benchmark-data/ (2026cmptx_sf13m1: dense
// detections from the shipped GPU model, v4_s, down to a 0.10 cutoff, plus 48 hand labels across 5181, 7769, 1690 and 2046, read from 1080p frames;
// red robots other than 2046 were left unlabelled -- their numbers weren't legible):
//   BENCH_ROBOT_LABELS=scripts/benchmark-data/2026cmptx_sf13m1.labels.json \
//     npx vite-node scripts/on-device-benchmark.ts scripts/benchmark-data/2026cmptx_sf13m1.dense10.json
//
// <detections.json> comes from backend/scripts/on_device_benchmark_detections.py: the
// shipped detector run densely (10 fps) over one match, with bumper colours. The dense
// run, tracked and stitched, is the reference -- detector and tracker agree far more
// often when robots move 0.4 m between frames than 1.3 m, so it is the best automatic
// answer available, not ground truth. [reference-labels.json] maps reference path ids to
// robot labels after checking them against the video ({"12": "A", "40": "A", ...});
// paths it leaves out are ignored.
//
// Each configuration then re-runs the shipped pipeline (buildRobotPaths) on a sparser
// sample of the same detections and is scored against the reference.
//
// BENCH_ROBOT_LABELS=<file> adds hand labels from the labelling page (a team per robot box
// at a few instants): idAcc is the share a scout labelling each path by its majority
// team gets right, and mixed counts paths that carry two teams. This is the only real
// identity check here -- the automatic reference cannot tell a right join from a wrong one.

import { readFileSync, writeFileSync } from 'node:fs';

import { scoreAgainstLabels, scorePaths, type RobotLabel } from '../src/features/onDevice/pathBenchmark';
import { projectPoint, type Mat3 } from '../src/features/onDevice/homography';
import { suggestionGroups } from '../src/features/onDevice/pathSuggestions';
import { buildRobotPaths, type RobotPath } from '../src/features/onDevice/robotPaths';
import { type RawFrame } from '../src/features/onDevice/simpleTracker';
import { type TrackPoint } from '../src/features/onDevice/trackProduction';

type DenseFile = {
  sample_fps: number;
  H: number[][];
  frames: { t: number; dets: { bbox: [number, number, number, number]; score: number; red: number; blue: number }[] }[];
};

const CONF = Number(process.env.BENCH_CONF ?? 0.35); // GPU_MODEL.confThreshold

function toRawFrames(dense: DenseFile, fps: number): RawFrame[] {
  const intervalSec = 1 / fps;
  let nextSec = dense.frames[0]?.t ?? 0;
  return dense.frames
    .filter((f) => {
      if (f.t + 0.5 / dense.sample_fps < nextSec) return false;
      nextSec += intervalSec;
      return true;
    })
    .map((f) => ({
      timeSec: f.t,
      homography: dense.H,
      detections: f.dets
        .filter((d) => d.score >= CONF)
        .map((d) => ({ bbox: d.bbox, confidence: d.score, colour: { red: d.red, blue: d.blue } })),
    }));
}

function asRecord(paths: RobotPath[]): Record<number, TrackPoint[]> {
  return Object.fromEntries(paths.map((p) => [p.pathId, p.points]));
}

const [densePath, labelsPath] = process.argv.slice(2);
const dense = JSON.parse(readFileSync(densePath, 'utf8')) as DenseFile;

const referencePaths = buildRobotPaths(toRawFrames(dense, dense.sample_fps), { minPoints: 20 });
if (process.env.BENCH_EXPORT) writeFileSync(process.env.BENCH_EXPORT, JSON.stringify(referencePaths));
if (process.env.BENCH_DETAIL === '1') {
  for (const p of referencePaths) {
    console.log(`path ${p.pathId}: ${p.points.length} pts, ${p.points[0].timeSec.toFixed(1)}-${p.points.at(-1)!.timeSec.toFixed(1)}s, ${p.alliance ?? '?'}; red=${p.redShare.toFixed(3)} blue=${p.blueShare.toFixed(3)}; ${p.fragments} fragments; ends ${p.points[0].fieldX.toFixed(1)},${p.points[0].fieldY.toFixed(1)} -> ${p.points.at(-1)!.fieldX.toFixed(1)},${p.points.at(-1)!.fieldY.toFixed(1)}`);
  }
}
let reference = asRecord(referencePaths);

type LabelFile = { labels: { t: number; bbox: [number, number, number, number]; label: string }[] };
const robotLabels: RobotLabel[] = process.env.BENCH_ROBOT_LABELS
  ? (JSON.parse(readFileSync(process.env.BENCH_ROBOT_LABELS, 'utf8')) as LabelFile).labels
      .filter((l) => l.label.startsWith('frc'))
      .map((l) => {
        const p = projectPoint(dense.H as Mat3, (l.bbox[0] + l.bbox[2]) / 2, l.bbox[3]);
        return { timeSec: l.t, fieldX: p.x, fieldY: p.y, team: l.label };
      })
  : [];
if (labelsPath) {
  const labels = JSON.parse(readFileSync(labelsPath, 'utf8')) as Record<string, string>;
  const merged: Record<string, TrackPoint[]> = {};
  for (const [id, label] of Object.entries(labels)) (merged[label] ??= []).push(...(reference[Number(id)] ?? []));
  const names = Object.keys(merged);
  reference = Object.fromEntries(names.map((n, i) => [i, merged[n].sort((a, b) => a.timeSec - b.timeSec)]));
}
console.log(
  `reference: ${Object.keys(reference).length} ${labelsPath ? 'labelled paths' : 'automatic paths (not robot ground truth)'}, ` +
    Object.values(reference)
      .map((pts) => `${pts.length}`)
      .join('/') +
    ' points',
);

// BENCH_SUGGEST=1: also score the identify step's "same robot" suggestions in the worst
// case, a scout who accepts every one: paths in a suggestion chain are merged, and taps
// becomes the number of chains. BENCH_SUG_GAP / _SPEED / _TOL / _MARGIN override them.
function acceptAllSuggestions(paths: RobotPath[]): { record: Record<number, TrackPoint[]>; taps: number } {
  const n = (k: string) => (process.env[k] !== undefined ? Number(process.env[k]) : undefined);
  const groups = suggestionGroups(
    paths.map((p) => ({ pathId: p.pathId, points: p.points, alliance: p.alliance })),
    { maxGapSec: n('BENCH_SUG_GAP'), maxSpeedMps: n('BENCH_SUG_SPEED'), positionToleranceM: n('BENCH_SUG_TOL'), ambiguityMarginM: n('BENCH_SUG_MARGIN') },
  );
  const byId = new Map(paths.map((p) => [p.pathId, p.points]));
  const record: Record<number, TrackPoint[]> = {};
  for (const g of groups) record[g[0]] = g.flatMap((id) => byId.get(id) ?? []).sort((a, b) => a.timeSec - b.timeSec);
  return { record, taps: groups.length };
}

const rows: Record<string, string | number>[] = [];
for (const fps of [10, 5, 3, 2, 1, 0.5]) {
  for (const stitch of [false, true]) {
    const frames = toRawFrames(dense, fps);
    const started = performance.now();
    const stitchOpts = process.env.BENCH_STITCH_GAP
      ? { maxGapSec: Number(process.env.BENCH_STITCH_GAP), maxSpeedMps: Number(process.env.BENCH_STITCH_SPEED ?? 4.5), positionToleranceM: Number(process.env.BENCH_STITCH_TOL ?? 1) }
      : true;
    const tracker = process.env.BENCH_TRACK_SPEED
      ? { maxSpeedMps: Number(process.env.BENCH_TRACK_SPEED), positionToleranceM: Number(process.env.BENCH_TRACK_TOL ?? 0.75) }
      : undefined;
    const paths = buildRobotPaths(frames, { stitch: stitch ? stitchOpts : false, tracker });
    const ms = performance.now() - started;
    const s = scorePaths(asRecord(paths), reference, frames.map((f) => f.timeSec));
    rows.push({
      fps,
      stitch: stitch ? 'yes' : 'no',
      frames: frames.length,
      paths: s.paths,
      tapsFor90: s.tapsFor90,
      coverage: s.coverage.toFixed(3),
      errM: s.medianErrorM?.toFixed(2) ?? '-',
      purity: s.purity.toFixed(3),
      attributed: s.attributed.toFixed(3),
      ...(robotLabels.length
        ? (() => {
            const l = scoreAgainstLabels(asRecord(paths), robotLabels);
            return { idAcc: l.identityAccuracy.toFixed(3), labelsFound: `${l.found}/${l.labels}`, mixed: l.mixedPaths };
          })()
        : {}),
      buildMs: Math.round(ms),
    });
    if (process.env.BENCH_SUGGEST === '1' && stitch) {
      const merged = acceptAllSuggestions(paths);
      const l = robotLabels.length ? scoreAgainstLabels(merged.record, robotLabels) : null;
      rows.push({
        fps,
        stitch: 'yes+suggest',
        frames: frames.length,
        paths: merged.taps,
        ...(l ? { idAcc: l.identityAccuracy.toFixed(3), labelsFound: `${l.found}/${l.labels}`, mixed: l.mixedPaths } : {}),
      });
    }
  }
}
console.table(rows);
