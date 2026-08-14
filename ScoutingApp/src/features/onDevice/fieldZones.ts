// 2026 REBUILT field zones + point-in-polygon classify — the in-browser mirror of the
// backend game_config zones + classify_point. Hardcoded for the current season so zone
// tagging works fully offline; should come from a game-config endpoint when generalized.

export const FIELD_LENGTH_M = 16.541;
export const FIELD_WIDTH_M = 8.0693;

export type Zone = { key: string; kind: string; polygon: [number, number][] };

export const FIELD_ZONES: Zone[] = [
  { key: 'red_alliance_scoring_zone', kind: 'scoring', polygon: [[10.6883, 2.8082], [13.1148, 2.8082], [13.1148, 5.2344], [10.6883, 5.2344]] },
  { key: 'red_loading_depot_zone', kind: 'loading', polygon: [[13.1374, 6.2659], [16.541, 6.2659], [16.541, 8.0693], [13.1374, 8.0693]] },
  { key: 'neutral_transition_zone', kind: 'neutral', polygon: [[4.6632, 0.0], [11.8781, 0.0], [11.8781, 8.0693], [4.6632, 8.0693]] },
  { key: 'red_tower_endgame_zone', kind: 'endgame', polygon: [[13.1374, 3.3726], [16.541, 3.3726], [16.541, 4.8204], [13.1374, 4.8204]] },
  { key: 'blue_alliance_scoring_zone', kind: 'scoring', polygon: [[3.3983, 2.8082], [5.8247, 2.8082], [5.8247, 5.2344], [3.3983, 5.2344]] },
  { key: 'blue_loading_depot_zone', kind: 'loading', polygon: [[0.0, 0.0], [3.4036, 0.0], [3.4036, 1.8034], [0.0, 1.8034]] },
  { key: 'blue_tower_endgame_zone', kind: 'endgame', polygon: [[0.0, 3.2222], [3.4036, 3.2222], [3.4036, 4.67], [0.0, 4.67]] },
];

// Ray-casting point-in-polygon (matches backend game_config.point_in_polygon).
export function pointInPolygon(x: number, y: number, polygon: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    const intersects = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

export function classifyZone(x: number, y: number): string | null {
  for (const zone of FIELD_ZONES) {
    if (pointInPolygon(x, y, zone.polygon)) return zone.key;
  }
  return null;
}
