import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  getEventSchedule,
  syncOnDeviceSession,
  type OnDeviceSessionSyncResponse,
  type TeamHeatmapResponse,
} from '../../api';
import { FieldHeatmap } from '../../components/cv/FieldHeatmap';
import { FieldCalibration } from './FieldCalibration';
import { MatchRecorder, type CapturedFrame } from './MatchRecorder';
import { VideoFileProcessor } from './VideoFileProcessor';
import { type Calibration, type Mat3 } from './homography';
import { getCalibration, markSessionSynced, openDb, saveSession, type StoredSession } from './offlineStore';
import { assignTrackIds, type RawFrame } from './simpleTracker';
import {
  assemblePointsByTeam,
  produceTrackPoints,
  type Frame,
  type TrackPoint,
} from './trackProduction';
import { voteTrackIdentity } from './identityVote';
import { createCvPoseResolver, loadOpenCv, type CvPoseResolver } from './opticalFlow';
import { flushPendingOnDeviceSessions } from './sync';
import './OnDeviceRun.css';

// The on-device match-breakdown flow, end to end:
//   setup (which match + its 6 teams) → calibrate (4-tap homography) → capture (camera +
//   in-browser detect) → identify (track + closed-set OCR vote / tap-ID) → result
//   (assemble per-team field tracks, store offline, sync → server shift-play).
// Every step is one of the tested onDevice modules; this screen is the glue + UI.

type Stage = 'setup' | 'calibrate' | 'capture' | 'identify' | 'result';
type Alliance = 'red' | 'blue';
type MatchTeam = { teamKey: string; alliance: Alliance };
type TrackSummary = {
  trackId: number;
  pointCount: number;
  dominantZone: string | null;
  startSec: number;
  endSec: number;
  suggestedTeam: string | null; // from OCR vote when reads exist (none yet → null)
};

const MIN_TRACK_POINTS = 3;
const STAGES: Stage[] = ['setup', 'calibrate', 'capture', 'identify', 'result'];

// 1–5 segmented level bar for offense/defense.
function LevelMeter({
  label,
  level,
  confidence,
  assessable = true,
  variant,
}: {
  label: string;
  level: number;
  confidence?: number;
  assessable?: boolean;
  variant: 'offense' | 'defense';
}) {
  return (
    <div className={`odr-meter odr-meter--${variant}`}>
      <span className="odr-meter__label">
        <span>{label}</span>
        {assessable ? (
          <span className="odr-meter__value">
            {level}/5{confidence != null ? ` · ${Math.round(confidence * 100)}%` : ''}
          </span>
        ) : (
          <span className="odr-meter__na">n/a</span>
        )}
      </span>
      <div className="odr-meter__bar" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((n) => (
          <span key={n} className={`odr-meter__seg${assessable && n <= level ? ' is-on' : ''}`} />
        ))}
      </div>
    </div>
  );
}

function newId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

function dominantZone(points: TrackPoint[]): string | null {
  const counts = new Map<string, number>();
  for (const p of points) {
    if (!p.zoneKey) continue;
    counts.set(p.zoneKey, (counts.get(p.zoneKey) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestN = 0;
  for (const [zone, n] of counts) {
    if (n > bestN) {
      best = zone;
      bestN = n;
    }
  }
  return best;
}

// Wrap a raw count grid (analyze_match_shift_play heatmap) in the TeamHeatmapResponse
// shape FieldHeatmap renders.
function rawGridToHeatmap(grid: number[][], teamKey: string): TeamHeatmapResponse {
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
    field_length_m: 16.541,
    field_width_m: 8.0693,
    grid_cols: cols,
    grid_rows: rows,
    sigma: 0,
    grid: grid.map((row) => row.map((v) => (peak ? Math.round((v / peak) * 1e4) / 1e4 : 0))),
  };
}

export function OnDeviceRun() {
  const [stage, setStage] = useState<Stage>('setup');
  const [eventKey, setEventKey] = useState('');
  const [matchKey, setMatchKey] = useState('');
  const [teams, setTeams] = useState<MatchTeam[]>([]);
  const [setupBusy, setSetupBusy] = useState(false);
  const [setupError, setSetupError] = useState('');

  const baseHomographyRef = useRef<Mat3 | null>(null);
  const capturedRef = useRef<CapturedFrame[]>([]);
  const [capturedCount, setCapturedCount] = useState(0);

  // Capture source: live camera (handheld, optical-flow stabilized) or an uploaded clip
  // (desktop, static calibration — one fixed camera view).
  const [captureMode, setCaptureMode] = useState<'camera' | 'video'>('camera');

  // Optical-flow camera stabilization (carries the calibrated pose through shake).
  const cvResolverRef = useRef<CvPoseResolver | null>(null);
  const [stabilize, setStabilize] = useState(true);
  const [stabStatus, setStabStatus] = useState<'off' | 'loading' | 'ready' | 'error'>('off');

  const [trackPoints, setTrackPoints] = useState<Record<number, TrackPoint[]>>({});
  const [summaries, setSummaries] = useState<TrackSummary[]>([]);
  const [identities, setIdentities] = useState<Record<number, string>>({}); // trackId -> teamKey

  const [syncResult, setSyncResult] = useState<OnDeviceSessionSyncResponse | null>(null);
  const [resultBusy, setResultBusy] = useState(false);
  const [resultNote, setResultNote] = useState('');

  const candidateTeamKeys = useMemo(() => teams.map((t) => t.teamKey), [teams]);

  // ── setup ───────────────────────────────────────────────────────────
  const normalizedMatchKey = matchKey.trim().toLowerCase();
  const normalizedEventKey = (eventKey.trim() || normalizedMatchKey.split('_')[0]).toLowerCase();

  const resetRunState = useCallback(() => {
    capturedRef.current = [];
    cvResolverRef.current?.dispose();
    cvResolverRef.current = null;
    setCapturedCount(0);
    setTrackPoints({});
    setSummaries([]);
    setIdentities({});
    setSyncResult(null);
    setResultBusy(false);
    setResultNote('');
  }, []);

  const loadTeams = useCallback(async () => {
    setSetupBusy(true);
    setSetupError('');
    try {
      const sched = await getEventSchedule(normalizedEventKey, false, { includeTeams: true });
      const match = sched.matches.find((m) => m.match_key === normalizedMatchKey);
      if (!match) throw new Error('Match not found in this event schedule.');
      const loaded: MatchTeam[] = [
        ...match.red.map((t) => ({ teamKey: t.team_key, alliance: 'red' as const })),
        ...match.blue.map((t) => ({ teamKey: t.team_key, alliance: 'blue' as const })),
      ];
      if (loaded.length === 0) throw new Error('No teams listed for this match yet.');
      resetRunState();
      setTeams(loaded);
    } catch (err) {
      setSetupError(err instanceof Error ? err.message : 'Could not load the match.');
    } finally {
      setSetupBusy(false);
    }
  }, [normalizedEventKey, normalizedMatchKey, resetRunState]);

  // ── calibrate ─────────────────────────────────────────────────────────
  const onCalibrated = useCallback((cal: Calibration) => {
    baseHomographyRef.current = cal.homography;
    setStage('capture');
  }, []);

  const applySavedCalibration = useCallback(async () => {
    let db: IDBDatabase | null = null;
    try {
      db = await openDb();
      const saved = await getCalibration(db, 'current');
      if (saved) {
        baseHomographyRef.current = saved.homography;
        setStage('capture');
      }
    } catch {
      // no saved calibration; stay on the calibrate stage
    } finally {
      db?.close();
    }
  }, []);

  // ── capture → identify ──────────────────────────────────────────────────
  // Per-frame pose: when stabilization is ready, carry the calibrated homography by
  // optical-flow motion (survives shake); otherwise fall back to the static calibration.
  const resolvePose = useCallback((canvas: HTMLCanvasElement): Mat3 | null => {
    const base = baseHomographyRef.current;
    if (!base) return null;
    const resolver = cvResolverRef.current;
    if (resolver) return resolver.resolve(canvas) ?? base;
    return base;
  }, []);

  // Video clips are one fixed camera view, so each sampled frame uses the base
  // calibration directly (optical-flow carry is for the handheld camera path).
  const resolvePoseStatic = useCallback((): Mat3 | null => baseHomographyRef.current, []);

  // Lazily load OpenCV.js and build the stabilized resolver on entering the capture
  // stage. Heavy WASM, so only when stabilization is on; degrades to the static pose on
  // failure. Torn down on leaving capture or toggling the option.
  useEffect(() => {
    cvResolverRef.current?.dispose();
    cvResolverRef.current = null;
    const base = baseHomographyRef.current;
    if (stage !== 'capture' || captureMode !== 'camera' || !stabilize || !base) {
      setStabStatus('off');
      return;
    }
    let cancelled = false;
    setStabStatus('loading');
    loadOpenCv()
      .then((cv) => {
        if (cancelled) return;
        cvResolverRef.current = createCvPoseResolver(cv, base);
        setStabStatus('ready');
      })
      .catch(() => {
        if (!cancelled) setStabStatus('error');
      });
    return () => {
      cancelled = true;
      cvResolverRef.current?.dispose();
      cvResolverRef.current = null;
    };
  }, [stage, stabilize, captureMode]);

  const onFrame = useCallback((frame: CapturedFrame) => {
    capturedRef.current.push(frame);
    setCapturedCount(capturedRef.current.length);
  }, []);

  const buildTracks = useCallback(() => {
    const captured = capturedRef.current;
    const rawFrames: RawFrame[] = captured.map((f) => ({
      timeSec: f.timeSec,
      detections: f.detections,
    }));
    const tracked = assignTrackIds(rawFrames);
    const homoByTime = new Map(captured.map((f) => [f.timeSec, f.homography]));
    const frames: Frame[] = tracked.map((tf) => ({
      timeSec: tf.timeSec,
      homography: homoByTime.get(tf.timeSec) ?? null,
      detections: tf.detections,
    }));
    const produced = produceTrackPoints(frames);

    const rows: TrackSummary[] = [];
    const seedIds: Record<number, string> = {};
    for (const [idStr, points] of Object.entries(produced)) {
      if (points.length < MIN_TRACK_POINTS) continue;
      const trackId = Number(idStr);
      // Closed-set OCR vote (no in-browser reads yet → unresolved); tap-ID resolves it.
      const vote = voteTrackIdentity([], candidateTeamKeys);
      if (vote.resolved && vote.teamKey) seedIds[trackId] = vote.teamKey;
      rows.push({
        trackId,
        pointCount: points.length,
        dominantZone: dominantZone(points),
        startSec: points[0].timeSec,
        endSec: points[points.length - 1].timeSec,
        suggestedTeam: vote.resolved ? vote.teamKey : null,
      });
    }
    rows.sort((a, b) => b.pointCount - a.pointCount);
    setTrackPoints(produced);
    setSummaries(rows);
    setIdentities(seedIds);
    setStage('identify');
  }, [candidateTeamKeys]);

  const assignIdentity = useCallback((trackId: number, teamKey: string) => {
    setIdentities((prev) => {
      const next = { ...prev };
      if (teamKey) next[trackId] = teamKey;
      else delete next[trackId];
      return next;
    });
  }, []);

  // ── result: assemble → store offline → sync ───────────────────────────
  const finishAndSync = useCallback(async () => {
    setResultBusy(true);
    setResultNote('');
    setSyncResult(null);
    const pointsByTeam = assemblePointsByTeam(trackPoints, identities);
    const session: StoredSession = {
      id: newId(),
      eventKey: normalizedEventKey,
      matchKey: normalizedMatchKey,
      createdAt: Date.now(),
      synced: false,
      payload: { points_by_team: pointsByTeam },
    };

    // Always persist locally first, so an offline run is never lost.
    let storedLocally = false;
    try {
      const db = await openDb();
      try {
        await saveSession(db, session);
        storedLocally = true;
      } finally {
        db.close();
      }
    } catch {
      // IndexedDB unavailable — fall through and still attempt the network sync
    }

    setStage('result');
    if (typeof navigator !== 'undefined' && !navigator.onLine) {
      setResultNote('Saved offline. It will sync automatically when you reconnect.');
      setResultBusy(false);
      return;
    }
    try {
      const res = await syncOnDeviceSession({
        id: session.id,
        eventKey: session.eventKey,
        matchKey: session.matchKey,
        createdAt: session.createdAt,
        payload: session.payload,
      });
      setSyncResult(res);
      if (storedLocally) {
        try {
          const db = await openDb();
          try {
            await markSessionSynced(db, session.id);
          } finally {
            db.close();
          }
        } catch {
          // Direct sync succeeded; a later auto-flush can reconcile local state.
        }
      }
      // Flush any earlier pending runs after the current one is marked synced.
      void flushPendingOnDeviceSessions();
    } catch (err) {
      setResultNote(
        `Saved offline (sync failed: ${err instanceof Error ? err.message : 'network error'}). It will retry when you reconnect.`,
      );
    } finally {
      setResultBusy(false);
    }
  }, [identities, normalizedEventKey, normalizedMatchKey, trackPoints]);

  const resolvedCount = Object.keys(identities).length;

  return (
    <div className="on-device-run">
      <div className="odr-steps" role="list">
        {STAGES.map((s, i) => {
          const currentIdx = STAGES.indexOf(stage);
          const done = i < currentIdx;
          const active = s === stage;
          return (
            <button
              key={s}
              type="button"
              role="listitem"
              className={`odr-step${active ? ' is-active' : ''}${done ? ' is-done is-clickable' : ''}`}
              disabled={!done}
              aria-current={active ? 'step' : undefined}
              onClick={() => done && setStage(s)}
            >
              <span className="odr-step__bubble">{done ? '✓' : i + 1}</span>
              <span className="odr-step__label">{s}</span>
            </button>
          );
        })}
      </div>

      {stage === 'setup' ? (
        <div className="odr-section">
          <p className="odr-hint">
            Pick the match you&apos;re filming. Its six teams become the closed set for bumper-OCR
            identity and set each robot&apos;s alliance for the offense/defense split.
          </p>
          <div className="odr-form">
            <label className="odr-field">
              <span className="odr-label">Match key</span>
              <input
                className="odr-input"
                type="text"
                inputMode="text"
                autoCapitalize="none"
                placeholder="e.g. 2026txhou_qm1"
                value={matchKey}
                onChange={(e) => setMatchKey(e.target.value)}
              />
            </label>
            <label className="odr-field">
              <span className="odr-label">Event key (optional)</span>
              <input
                className="odr-input"
                type="text"
                autoCapitalize="none"
                placeholder="inferred from match key"
                value={eventKey}
                onChange={(e) => setEventKey(e.target.value)}
              />
            </label>
          </div>
          <div className="odr-actions">
            <button
              type="button"
              className="center-btn"
              onClick={() => void loadTeams()}
              disabled={!normalizedMatchKey || setupBusy}
            >
              {setupBusy ? 'Loading…' : 'Load match teams'}
            </button>
          </div>
          {setupError ? <p className="odr-error">{setupError}</p> : null}
          {teams.length > 0 ? (
            <>
              <div className="odr-teams">
                {(['red', 'blue'] as const).map((alliance) => (
                  <div key={alliance} className={`odr-alliance odr-alliance--${alliance}`}>
                    <span className="odr-alliance__label">{alliance.toUpperCase()}</span>
                    {teams
                      .filter((t) => t.alliance === alliance)
                      .map((t) => (
                        <span key={t.teamKey} className="odr-team-chip">
                          {t.teamKey.replace(/^frc/i, '')}
                        </span>
                      ))}
                  </div>
                ))}
              </div>
              <div className="odr-actions">
                <button type="button" className="center-btn" onClick={() => setStage('calibrate')}>
                  Continue to calibration
                </button>
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      {stage === 'calibrate' ? (
        <div className="odr-section">
          <FieldCalibration onCalibrated={onCalibrated} />
          <div className="odr-actions">
            <button type="button" className="center-btn ghost" onClick={() => void applySavedCalibration()}>
              Use saved calibration
            </button>
            <button type="button" className="center-btn ghost" onClick={() => setStage('setup')}>
              Back
            </button>
          </div>
        </div>
      ) : null}

      {stage === 'capture' ? (
        <div className="odr-section">
          <div className="segmented-tabs odr-mode" role="tablist" aria-label="Capture source">
            {(['camera', 'video'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                role="tab"
                aria-selected={captureMode === mode}
                className={`segmented-tabs__item${captureMode === mode ? ' active' : ''}`}
                onClick={() => {
                  capturedRef.current = [];
                  setCapturedCount(0);
                  setCaptureMode(mode);
                }}
              >
                {mode === 'camera' ? 'Record (camera)' : 'Upload video'}
              </button>
            ))}
          </div>

          {captureMode === 'camera' ? (
            <>
              <p className="odr-hint">
                Point the phone at the field and record the match. Frames are sampled and robots
                detected on-device — nothing leaves the phone until you sync.
              </p>
              <label className="odr-switch">
                <input
                  type="checkbox"
                  checked={stabilize}
                  onChange={(e) => setStabilize(e.target.checked)}
                />
                Stabilize for camera shake (optical flow)
                {stabStatus === 'loading' ? <span className="odr-switch__status">loading…</span> : null}
                {stabStatus === 'ready' ? <span className="odr-switch__status is-ready">ready</span> : null}
                {stabStatus === 'error' ? (
                  <span className="odr-switch__status is-error">static fallback</span>
                ) : null}
              </label>
              <MatchRecorder resolvePose={resolvePose} onFrame={onFrame} />
            </>
          ) : (
            <>
              <p className="odr-hint">
                Upload a match clip (a fixed wide-camera view works best). Frames are sampled and
                robots detected on-device, then identified and synced — no camera or second screen
                needed.
              </p>
              <VideoFileProcessor
                resolvePose={resolvePoseStatic}
                onFrame={onFrame}
                onComplete={buildTracks}
              />
            </>
          )}

          <div className="odr-actions">
            <button type="button" className="center-btn" onClick={buildTracks} disabled={capturedCount === 0}>
              Identify robots{capturedCount > 0 ? ` (${capturedCount} frames)` : ''}
            </button>
            <button type="button" className="center-btn ghost" onClick={() => setStage('calibrate')}>
              Back
            </button>
          </div>
        </div>
      ) : null}

      {stage === 'identify' ? (
        <div className="odr-section">
          <p className="odr-hint">
            {summaries.length} track{summaries.length === 1 ? '' : 's'} produced. Bumper-OCR auto-ID
            isn&apos;t available on-device yet, so assign each track to a team (tap-ID — the
            documented fallback). Unassigned tracks are dropped.
          </p>
          <div className="odr-tracks">
            {summaries.map((t) => (
              <div
                key={t.trackId}
                className={`odr-track${identities[t.trackId] ? ' odr-track--assigned' : ''}`}
              >
                <div className="odr-track__info">
                  <span className="odr-track__title">
                    Track {t.trackId} · {t.pointCount} pts
                  </span>
                  <span className="odr-track__meta">
                    {t.dominantZone ? t.dominantZone.replace(/_/g, ' ') : 'no zone'} ·{' '}
                    {t.startSec.toFixed(1)}–{t.endSec.toFixed(1)}s
                    {t.suggestedTeam ? ` · OCR → ${t.suggestedTeam}` : ''}
                  </span>
                </div>
                <select
                  className="odr-select"
                  aria-label={`Assign track ${t.trackId} to a team`}
                  value={identities[t.trackId] ?? ''}
                  onChange={(e) => assignIdentity(t.trackId, e.target.value)}
                >
                  <option value="">— ignore —</option>
                  {teams.map((team) => (
                    <option key={team.teamKey} value={team.teamKey}>
                      {team.teamKey.replace(/^frc/i, '')} ({team.alliance})
                    </option>
                  ))}
                </select>
              </div>
            ))}
            {summaries.length === 0 ? (
              <p className="odr-error">
                No tracks met the minimum length. Re-record with a steadier, closer view.
              </p>
            ) : null}
          </div>
          <div className="odr-actions">
            <button
              type="button"
              className="center-btn"
              onClick={() => void finishAndSync()}
              disabled={resolvedCount === 0 || resultBusy}
            >
              {resultBusy ? 'Saving…' : `Finish & sync${resolvedCount > 0 ? ` (${resolvedCount})` : ''}`}
            </button>
            <button type="button" className="center-btn ghost" onClick={() => setStage('capture')}>
              Back
            </button>
          </div>
        </div>
      ) : null}

      {stage === 'result' ? (
        <div className="odr-section">
          {resultNote ? <p className="odr-hint">{resultNote}</p> : null}
          {syncResult ? (
            <>
              <p className="odr-summary">
                Synced <strong>{syncResult.points_persisted}</strong> points across{' '}
                <strong>{syncResult.team_count}</strong> robots (run #{syncResult.run_id}
                {syncResult.reused_run ? ', updated' : ''}).
                {syncResult.skipped_unknown_teams.length > 0
                  ? ` Skipped unknown: ${syncResult.skipped_unknown_teams.join(', ')}.`
                  : ''}
              </p>
              {syncResult.shift_play ? (
                <div className="odr-robots">
                  {Object.entries(syncResult.shift_play).map(([teamKey, sp]) => (
                    <div key={teamKey} className="odr-robot">
                      <div className="odr-robot__head">
                        <span className="odr-robot__team">{teamKey.replace(/^frc/i, 'Team ')}</span>
                        <span className={`odr-badge odr-badge--${sp.alliance}`}>{sp.alliance}</span>
                      </div>
                      <div className="odr-meters">
                        <LevelMeter
                          variant="offense"
                          label="Offense"
                          level={sp.offense.level_1_5}
                          confidence={sp.offense.confidence_0_1}
                        />
                        <LevelMeter
                          variant="defense"
                          label="Defense"
                          level={sp.defense.level_1_5}
                          confidence={sp.defense.confidence_0_1}
                          assessable={sp.defense.assessable}
                        />
                      </div>
                      {sp.heatmaps ? (
                        <div className="odr-heatmaps">
                          <div>
                            <p className="odr-heatmap__title">Attack (own shifts)</p>
                            <FieldHeatmap data={rawGridToHeatmap(sp.heatmaps.attack, teamKey)} />
                          </div>
                          <div>
                            <p className="odr-heatmap__title">Defense (opponent shifts)</p>
                            <FieldHeatmap data={rawGridToHeatmap(sp.heatmaps.defense, teamKey)} />
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
          <div className="odr-actions">
            <button
              type="button"
              className="center-btn"
              onClick={() => {
                resetRunState();
                setStage('capture');
              }}
            >
              Record another
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
