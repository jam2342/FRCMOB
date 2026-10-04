import type { TeamHeatmapResponse } from '../../api';
import { FIELD_LENGTH_M, FIELD_WIDTH_M } from './fieldZones';
import type { TrackPoint } from './trackProduction';

// Wrap a raw count grid (analyze_match_shift_play heatmap) in the TeamHeatmapResponse
// shape FieldHeatmap renders.
export function rawGridToHeatmap(grid: number[][], teamKey: string): TeamHeatmapResponse {
  const rows = grid.length;
  const cols = grid[0]?.length ?? 0;
  let peak = 0;
  let total = 0;
  for (const row of grid) {
    for (const v of row) {
      total += v;
      if (v > peak) peak = v;
    }
  }
  return {
    ok: true,
    team_key: teamKey,
    event_key: '',
    match_key: null,
    total_points: total,
    match_count: 1,
    field_length_m: FIELD_LENGTH_M,
    field_width_m: FIELD_WIDTH_M,
    grid_cols: cols,
    grid_rows: rows,
    sigma: 0,
    grid: grid.map((row) => row.map((v) => (peak ? Math.round((v / peak) * 1e4) / 1e4 : 0))),
  };
}


// Count observations, not inferred dwell time: gaps and missed detections stay unknown.
export function positionHeatmap(points: TrackPoint[], teamKey: string): TeamHeatmapResponse {
  const rows = 16;
  const cols = 32;
  const grid = Array.from({ length: rows }, () => Array<number>(cols).fill(0));
  for (const point of points) {
    const { fieldX: x, fieldY: y } = point;
    if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x > FIELD_LENGTH_M || y > FIELD_WIDTH_M) continue;
    const col = Math.min(cols - 1, Math.floor(x / FIELD_LENGTH_M * cols));
    const row = Math.min(rows - 1, Math.floor(y / FIELD_WIDTH_M * rows));
    grid[row][col] += 1;
  }
  return rawGridToHeatmap(grid, teamKey);
}

