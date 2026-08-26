import { useCallback, useEffect, useRef, useState, useMemo } from 'react';
import { useCanvasTokens } from '../hooks/useCanvasTokens';
import { getTeamBreakdown } from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { SCOUTING_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { normalizeTeamKeyInput, teamNumberFromTeamKey } from './centerUtils';

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'autopath_event_key';
const MY_TEAM_STORAGE = 'scouting_manual_my_team_v1';
const PATHS_STORAGE = 'autopath_saved_v1';

/* field dimensions from game config (meters) */
const FIELD_W = 16.541;
const FIELD_H = 8.0693;

/* The field's own chrome, resolved at draw time — canvas cannot read var().
   The zone fills and PATH_COLORS below stay literal on purpose: they are
   categorical identity, not chrome, and a zone that changed hue between themes
   would stop matching the season config it mirrors. */
const FIELD_TOKENS = [
  '--field-canvas-bg',
  '--field-canvas-grid',
  '--field-canvas-line',
  '--field-canvas-label',
  '--field-canvas-note',
] as const;

/* default zones from season_template.json */
const DEFAULT_ZONES: Zone[] = [
  {
    key: 'red_alliance_scoring_zone',
    label: 'Red Scoring',
    kind: 'scoring',
    color: 'rgba(239, 68, 68, 0.18)',
    border: '#ef4444',
    polygon: [
      { x: 10.7427, y: 2.8082 },
      { x: 13.143, y: 2.8082 },
      { x: 13.143, y: 5.2344 },
      { x: 10.7427, y: 5.2344 },
    ],
  },
  {
    key: 'red_loading_depot_zone',
    label: 'Red Loading',
    kind: 'loading',
    color: 'rgba(249, 115, 22, 0.15)',
    border: '#f97316',
    polygon: [
      { x: 13.1374, y: 6.2659 },
      { x: 16.541, y: 6.2659 },
      { x: 16.541, y: 8.0693 },
      { x: 13.1374, y: 8.0693 },
    ],
  },
  {
    key: 'neutral_transition_zone',
    label: 'Neutral',
    kind: 'neutral',
    color: 'rgba(107, 114, 128, 0.1)',
    border: '#6b7280',
    polygon: [
      { x: 6.2705, y: 0.0 },
      { x: 10.2705, y: 0.0 },
      { x: 10.2705, y: 8.0693 },
      { x: 6.2705, y: 8.0693 },
    ],
  },
  {
    key: 'red_tower_endgame_zone',
    label: 'Red Tower',
    kind: 'endgame',
    color: 'rgba(168, 85, 247, 0.15)',
    border: '#a855f7',
    polygon: [
      { x: 13.1374, y: 3.2222 },
      { x: 16.541, y: 3.2222 },
      { x: 16.541, y: 4.67 },
      { x: 13.1374, y: 4.67 },
    ],
  },
  {
    key: 'blue_alliance_scoring_zone',
    label: 'Blue Scoring',
    kind: 'scoring',
    color: 'rgba(59, 130, 246, 0.18)',
    border: '#3b82f6',
    polygon: [
      { x: 3.3983, y: 2.8082 },
      { x: 5.8247, y: 2.8082 },
      { x: 5.8247, y: 5.2344 },
      { x: 3.3983, y: 5.2344 },
    ],
  },
  {
    key: 'blue_loading_depot_zone',
    label: 'Blue Loading',
    kind: 'loading',
    color: 'rgba(6, 182, 212, 0.15)',
    border: '#06b6d4',
    polygon: [
      { x: 0.0, y: 0.0 },
      { x: 3.4036, y: 0.0 },
      { x: 3.4036, y: 1.8034 },
      { x: 0.0, y: 1.8034 },
    ],
  },
  {
    key: 'blue_tower_endgame_zone',
    label: 'Blue Tower',
    kind: 'endgame',
    color: 'rgba(139, 92, 246, 0.15)',
    border: '#8b5cf6',
    polygon: [
      { x: 0.0, y: 3.2222 },
      { x: 3.4036, y: 3.2222 },
      { x: 3.4036, y: 4.67 },
      { x: 0.0, y: 4.67 },
    ],
  },
];

type Pt = { x: number; y: number };
type Zone = {
  key: string;
  label: string;
  kind: string;
  color: string;
  border: string;
  polygon: Pt[];
};
type SavedPath = {
  id: string;
  teamKey: string;
  matchKey: string;
  label: string;
  color: string;
  points: Pt[];
  createdAt: number;
};

const PATH_COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4'];

/* ── Path simplification (Ramer-Douglas-Peucker) ──────────────────── */
function perpendicularDistance(pt: Pt, lineStart: Pt, lineEnd: Pt): number {
  const dx = lineEnd.x - lineStart.x;
  const dy = lineEnd.y - lineStart.y;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(pt.x - lineStart.x, pt.y - lineStart.y);
  const t = Math.max(0, Math.min(1, ((pt.x - lineStart.x) * dx + (pt.y - lineStart.y) * dy) / lenSq));
  const projX = lineStart.x + t * dx;
  const projY = lineStart.y + t * dy;
  return Math.hypot(pt.x - projX, pt.y - projY);
}

function rdpSimplify(points: Pt[], epsilon: number): Pt[] {
  if (points.length <= 2) return points;
  let maxDist = 0;
  let maxIdx = 0;
  const first = points[0];
  const last = points[points.length - 1];
  for (let i = 1; i < points.length - 1; i++) {
    const d = perpendicularDistance(points[i], first, last);
    if (d > maxDist) { maxDist = d; maxIdx = i; }
  }
  if (maxDist > epsilon) {
    const left = rdpSimplify(points.slice(0, maxIdx + 1), epsilon);
    const right = rdpSimplify(points.slice(maxIdx), epsilon);
    return [...left.slice(0, -1), ...right];
  }
  return [first, last];
}

/* ── Catmull-Rom spline interpolation ─────────────────────────────── */
function catmullRomSpline(points: Pt[], segments: number = 8): Pt[] {
  if (points.length < 2) return points;
  if (points.length === 2) return points;
  const result: Pt[] = [points[0]];
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[Math.min(points.length - 1, i + 1)];
    const p3 = points[Math.min(points.length - 1, i + 2)];
    for (let s = 1; s <= segments; s++) {
      const t = s / segments;
      const tt = t * t;
      const ttt = tt * t;
      result.push({
        x: 0.5 * (
          (2 * p1.x) +
          (-p0.x + p2.x) * t +
          (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * tt +
          (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * ttt
        ),
        y: 0.5 * (
          (2 * p1.y) +
          (-p0.y + p2.y) * t +
          (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * tt +
          (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * ttt
        ),
      });
    }
  }
  return result;
}

/** Clean up a raw drawn path: simplify with RDP, then smooth with Catmull-Rom */
function cleanPath(raw: Pt[]): Pt[] {
  if (raw.length < 3) return raw;
  // RDP epsilon in meters — filters jitter but keeps intentional turns
  const simplified = rdpSimplify(raw, 0.08);
  // Keep a minimum of key waypoints for accurate representation
  if (simplified.length < 3) return simplified;
  return simplified;
}

/** Generate a smooth display curve from key waypoints */
function smoothForDisplay(keyPoints: Pt[]): Pt[] {
  if (keyPoints.length < 3) return keyPoints;
  // Use fewer interpolation segments for shorter paths
  const segs = keyPoints.length > 8 ? 6 : 8;
  return catmullRomSpline(keyPoints, segs);
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function AutoPathPage() {
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent } = useEventKeyParam(STORAGE_KEY);
  const [teamInput, setTeamInput] = useState(() => {
    try {
      const saved = localStorage.getItem(MY_TEAM_STORAGE);
      return saved ? JSON.parse(saved) : '';
    } catch {
      return '';
    }
  });
  const [matchInput, setMatchInput] = useState('');

  /* canvas & drawing state */
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [isDrawing, setIsDrawing] = useState(false);
  const [currentStroke, setCurrentStroke] = useState<Pt[]>([]);
  const [drawMode, setDrawMode] = useState<'draw' | 'view'>('view');

  /* saved paths */
  const [savedPaths, setSavedPaths] = useState<SavedPath[]>(() => {
    try {
      const raw = localStorage.getItem(PATHS_STORAGE);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  });
  const [pathLabel, setPathLabel] = useState('Auto Path');
  const [selectedPathIds, setSelectedPathIds] = useState<Set<string>>(new Set());

  /* track points from API */
  const [trackPoints, setTrackPoints] = useState<
    Array<{ field_x: number | null; field_y: number | null; time_sec: number; match_key: string }>
  >([]);
  const [showTrackOverlay, setShowTrackOverlay] = useState(true);
  const [loadedTrackRequestKey, setLoadedTrackRequestKey] = useState('');

  const teamKey = useMemo(() => normalizeTeamKeyInput(teamInput), [teamInput]);
  const activeTrackRequestKey = teamKey && eventKey ? `${eventKey}|${teamKey}` : '';

  /* persist saved paths */
  useEffect(() => {
    try {
      localStorage.setItem(PATHS_STORAGE, JSON.stringify(savedPaths));
    } catch { /* ignore quota errors */ }
  }, [savedPaths]);

  /* load track points from API */
  useEffect(() => {
    if (!teamKey || !eventKey) return;
    let cancelled = false;
    const requestKey = `${eventKey}|${teamKey}`;
    getTeamBreakdown(teamKey, eventKey)
      .then((resp) => {
        if (cancelled) return;
        if (resp.ok && resp.recent_track_points) {
          setTrackPoints(
            resp.recent_track_points.map((tp) => ({
              field_x: tp.field_x,
              field_y: tp.field_y,
              time_sec: tp.time_sec,
              match_key: tp.match_key,
            })),
          );
        } else {
          setTrackPoints([]);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoadedTrackRequestKey(requestKey);
      });
    return () => { cancelled = true; };
  }, [teamKey, eventKey]);

  const activeTrackPoints = useMemo(
    () => (activeTrackRequestKey && loadedTrackRequestKey === activeTrackRequestKey ? trackPoints : []),
    [activeTrackRequestKey, loadedTrackRequestKey, trackPoints],
  );
  const loadingTracks = Boolean(activeTrackRequestKey) && loadedTrackRequestKey !== activeTrackRequestKey;

  /* filtered paths for current team/match */
  const filteredPaths = useMemo(() => {
    return savedPaths.filter((p) => {
      if (teamKey && p.teamKey !== teamKey) return false;
      if (matchInput && p.matchKey !== matchInput) return false;
      return true;
    });
  }, [savedPaths, teamKey, matchInput]);

  /* auto-only track points (time_sec <= 20s = auto period) */
  const autoTrackPts = useMemo(() => {
    return activeTrackPoints.filter((tp) => tp.field_x != null && tp.field_y != null && tp.time_sec <= 20);
  }, [activeTrackPoints]);

  /* ── canvas coordinate mapping ─────────────── */
  function getCanvasSize(): { w: number; h: number } {
    const el = containerRef.current;
    if (!el) return { w: 600, h: 293 };
    const w = el.clientWidth;
    const h = w * (FIELD_H / FIELD_W);
    return { w, h };
  }

  function fieldToCanvas(pt: Pt, canvasW: number, canvasH: number): { cx: number; cy: number } {
    return {
      cx: (pt.x / FIELD_W) * canvasW,
      cy: (1 - pt.y / FIELD_H) * canvasH, // flip y – field origin bottom-left
    };
  }

  function canvasToField(cx: number, cy: number, canvasW: number, canvasH: number): Pt {
    return {
      x: (cx / canvasW) * FIELD_W,
      y: (1 - cy / canvasH) * FIELD_H,
    };
  }

  /* ── draw the canvas ───────────────────────── */
  // New object on theme change, so drawCanvas changes identity and the effect
  // below repaints. A canvas keeps its pixels when the stylesheet swaps.
  const tokens = useCanvasTokens(FIELD_TOKENS);

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { w, h } = getCanvasSize();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    /* background */
    ctx.fillStyle = tokens['--field-canvas-bg'];
    ctx.fillRect(0, 0, w, h);

    /* grid lines */
    ctx.strokeStyle = tokens['--field-canvas-grid'];
    ctx.lineWidth = 1;
    for (let mx = 1; mx < FIELD_W; mx++) {
      const x = (mx / FIELD_W) * w;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
    for (let my = 1; my < FIELD_H; my++) {
      const y = (1 - my / FIELD_H) * h;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    /* zones */
    for (const zone of DEFAULT_ZONES) {
      ctx.fillStyle = zone.color;
      ctx.strokeStyle = zone.border;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      zone.polygon.forEach((pt, i) => {
        const { cx, cy } = fieldToCanvas(pt, w, h);
        if (i === 0) ctx.moveTo(cx, cy);
        else ctx.lineTo(cx, cy);
      });
      ctx.closePath();
      ctx.fill();
      ctx.stroke();

      /* zone label */
      const centroid = zone.polygon.reduce(
        (acc, p) => ({ x: acc.x + p.x / zone.polygon.length, y: acc.y + p.y / zone.polygon.length }),
        { x: 0, y: 0 },
      );
      const { cx: lcx, cy: lcy } = fieldToCanvas(centroid, w, h);
      ctx.fillStyle = tokens['--field-canvas-label'];
      ctx.font = `${Math.max(9, w / 60)}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(zone.label, lcx, lcy);
    }

    /* center line */
    const midX = w / 2;
    ctx.strokeStyle = tokens['--field-canvas-line'];
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(midX, 0);
    ctx.lineTo(midX, h);
    ctx.stroke();
    ctx.setLineDash([]);

    /* track point overlay */
    if (showTrackOverlay && autoTrackPts.length > 0) {
      ctx.globalAlpha = 0.5;
      for (const tp of autoTrackPts) {
        const { cx, cy } = fieldToCanvas({ x: tp.field_x!, y: tp.field_y! }, w, h);
        ctx.fillStyle = tokens['--field-canvas-note'];
        ctx.beginPath();
        ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    /* saved paths */
    for (const path of filteredPaths) {
      if (path.points.length < 2) continue;
      const highlight = selectedPathIds.has(path.id);
      ctx.strokeStyle = path.color;
      ctx.lineWidth = highlight ? 3.5 : 2;
      ctx.globalAlpha = highlight ? 1 : 0.65;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      const smooth = smoothForDisplay(path.points);
      ctx.beginPath();
      smooth.forEach((pt, i) => {
        const { cx, cy } = fieldToCanvas(pt, w, h);
        if (i === 0) ctx.moveTo(cx, cy);
        else ctx.lineTo(cx, cy);
      });
      ctx.stroke();

      /* start marker */
      const start = fieldToCanvas(path.points[0], w, h);
      ctx.fillStyle = path.color;
      ctx.beginPath();
      ctx.arc(start.cx, start.cy, 5, 0, Math.PI * 2);
      ctx.fill();

      /* end arrow – use last two smooth points for accurate tangent */
      const last = smooth[smooth.length - 1];
      const prev = smooth[Math.max(0, smooth.length - 2)];
      const endC = fieldToCanvas(last, w, h);
      const prevC = fieldToCanvas(prev, w, h);
      const angle = Math.atan2(endC.cy - prevC.cy, endC.cx - prevC.cx);
      const arrowLen = 10;
      ctx.beginPath();
      ctx.moveTo(endC.cx, endC.cy);
      ctx.lineTo(
        endC.cx - arrowLen * Math.cos(angle - Math.PI / 6),
        endC.cy - arrowLen * Math.sin(angle - Math.PI / 6),
      );
      ctx.moveTo(endC.cx, endC.cy);
      ctx.lineTo(
        endC.cx - arrowLen * Math.cos(angle + Math.PI / 6),
        endC.cy - arrowLen * Math.sin(angle + Math.PI / 6),
      );
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    /* active drawing stroke – show smoothed preview */
    if (currentStroke.length > 1) {
      ctx.strokeStyle = '#10b981';
      ctx.lineWidth = 3;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      const liveSmooth = smoothForDisplay(cleanPath(currentStroke));
      ctx.beginPath();
      liveSmooth.forEach((pt, i) => {
        const { cx, cy } = fieldToCanvas(pt, w, h);
        if (i === 0) ctx.moveTo(cx, cy);
        else ctx.lineTo(cx, cy);
      });
      ctx.stroke();
    }
  }, [filteredPaths, currentStroke, autoTrackPts, showTrackOverlay, selectedPathIds, tokens]);

  useEffect(() => { drawCanvas(); }, [drawCanvas]);

  /* redraw on resize */
  useEffect(() => {
    const handler = () => drawCanvas();
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, [drawCanvas]);

  /* ── pointer events ────────────────────────── */
  const getFieldPt = useCallback((e: React.PointerEvent<HTMLCanvasElement>): Pt => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    return canvasToField(cx, cy, rect.width, rect.height);
  }, []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (drawMode !== 'draw') return;
      e.preventDefault();
      canvasRef.current?.setPointerCapture(e.pointerId);
      const pt = getFieldPt(e);
      setIsDrawing(true);
      setCurrentStroke([pt]);
    },
    [drawMode, getFieldPt],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (!isDrawing || drawMode !== 'draw') return;
      e.preventDefault();
      const pt = getFieldPt(e);
      setCurrentStroke((prev) => {
        /* downsample: skip points too close together */
        const last = prev[prev.length - 1];
        if (last) {
          const dx = pt.x - last.x;
          const dy = pt.y - last.y;
          if (dx * dx + dy * dy < 0.01) return prev; // ~10cm threshold
        }
        return [...prev, pt];
      });
    },
    [drawMode, getFieldPt, isDrawing],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (!isDrawing) return;
      canvasRef.current?.releasePointerCapture(e.pointerId);
      setIsDrawing(false);
      if (currentStroke.length >= 2) {
        const cleaned = cleanPath(currentStroke);
        const newPath: SavedPath = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          teamKey: teamKey || 'unknown',
          matchKey: matchInput || 'unspecified',
          label: pathLabel || 'Auto Path',
          color: PATH_COLORS[savedPaths.length % PATH_COLORS.length],
          points: cleaned,
          createdAt: Date.now(),
        };
        setSavedPaths((prev) => [...prev, newPath]);
      }
      setCurrentStroke([]);
    },
    [isDrawing, currentStroke, teamKey, matchInput, pathLabel, savedPaths.length],
  );

  /* ── path management ───────────────────────── */
  function deletePath(id: string) {
    setSavedPaths((prev) => prev.filter((p) => p.id !== id));
    setSelectedPathIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }

  function togglePathSelection(id: string) {
    setSelectedPathIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearAllPaths() {
    setSavedPaths([]);
    setSelectedPathIds(new Set());
  }

  const surfaceGroupId = 'autopath-main';

  /* ── render ─────────────────────────────────── */
  return (
    <div className="center-page-container autopath-page">
      <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />

      <div className="center-page-header">
        <h1 className="center-page-title">Auto Path Drawer</h1>
        <p className="center-page-subtitle">
          Draw and compare auto paths
        </p>
      </div>

      {/* controls */}
      <EventPicker
        value={eventKey}
        onSelect={selectEvent}
        inputValue={eventInput}
        onInputChange={setEventInput}
        onSubmit={commitInput}
      />

      <div className="center-input-row" style={{ marginTop: '0.5rem', gridTemplateColumns: 'auto auto 1fr' }}>
        <input
          id="ap-team"
          className="center-input"
          type="text"
          inputMode="numeric"
          placeholder="Team # — e.g. 254"
          value={teamInput}
          onChange={(e) => setTeamInput(e.target.value)}
          style={{ maxWidth: 180 }}
        />
        <input
          id="ap-match"
          className="center-input"
          type="text"
          placeholder="Match — e.g. qm1"
          value={matchInput}
          onChange={(e) => setMatchInput(e.target.value)}
          style={{ maxWidth: 180 }}
        />
        <span />
      </div>

      <SurfaceCardGroup groupId={surfaceGroupId}>
        {/* Field Canvas — no fullscreen/minimize: those remount the <canvas>
           via portal and wipe the in-progress drawing (same fix as OnDeviceRun
           / FieldCalibration). */}
        <SurfaceCard title="Field View" expandable={false} mobileCollapsible={false}>
          <div className="autopath-toolbar">
            <button
              className={`autopath-mode-btn ${drawMode === 'view' ? 'active' : ''}`}
              onClick={() => setDrawMode('view')}
            >
              View
            </button>
            <button
              className={`autopath-mode-btn ${drawMode === 'draw' ? 'active' : ''}`}
              onClick={() => setDrawMode('draw')}
            >
              Draw
            </button>
            <label className="autopath-toggle-label">
              <input
                type="checkbox"
                checked={showTrackOverlay}
                onChange={(e) => setShowTrackOverlay(e.target.checked)}
              />
              Show Tracks
            </label>
            {loadingTracks && <span className="autopath-loading">Loading tracks...</span>}
          </div>

          {drawMode === 'draw' && (
            <div className="autopath-draw-controls">
              <input
                type="text"
                placeholder="Path label"
                value={pathLabel}
                onChange={(e) => setPathLabel(e.target.value)}
                className="autopath-label-input"
              />
              <span className="autopath-draw-hint">
                Click and drag on the field to draw a path
              </span>
            </div>
          )}

          <div className="autopath-canvas-container" ref={containerRef}>
            <canvas
              ref={canvasRef}
              className="autopath-canvas"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              style={{ touchAction: drawMode === 'draw' ? 'none' : 'auto', cursor: drawMode === 'draw' ? 'crosshair' : 'default' }}
            />
          </div>
        </SurfaceCard>

        {/* Saved Paths List */}
        <SurfaceCard
          title="Saved Paths"
          subtitle={`${filteredPaths.length} path${filteredPaths.length !== 1 ? 's' : ''}`}
        >
          {filteredPaths.length === 0 ? (
            <p className="dviz-empty">No paths saved yet. Switch to Draw mode and draw on the field.</p>
          ) : (
            <div className="autopath-list">
              {filteredPaths.map((p) => (
                <div
                  key={p.id}
                  className={`autopath-list-item ${selectedPathIds.has(p.id) ? 'selected' : ''}`}
                  onClick={() => togglePathSelection(p.id)}
                >
                  <span className="autopath-list-swatch" style={{ background: p.color }} />
                  <div className="autopath-list-info">
                    <span className="autopath-list-label">{p.label}</span>
                    <span className="autopath-list-meta">
                      {teamNumberFromTeamKey(p.teamKey) ?? p.teamKey}
                      {p.matchKey ? ` - ${p.matchKey}` : ''}
                      {' - '}
                      {p.points.length} pts
                    </span>
                  </div>
                  <button
                    className="autopath-list-delete"
                    onClick={(e) => { e.stopPropagation(); deletePath(p.id); }}
                    title="Delete path"
                  >
                    X
                  </button>
                </div>
              ))}
              <button className="autopath-clear-btn" onClick={clearAllPaths}>
                Clear All Paths
              </button>
            </div>
          )}
        </SurfaceCard>

        {/* Track Points Info */}
        {autoTrackPts.length > 0 && (
          <SurfaceCard
            title="Detected Auto Tracks"
            subtitle={`${autoTrackPts.length} points from video analysis`}
            collapsible
          >
            <p className="autopath-track-info">
              Yellow dots on the field show actual robot positions detected during the autonomous period
              (first 20 seconds) from video analysis. These help you understand the real path compared
              to your drawn paths.
            </p>
          </SurfaceCard>
        )}
      </SurfaceCardGroup>
    </div>
  );
}
