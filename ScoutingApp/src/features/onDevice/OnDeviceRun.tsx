import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  getEventSchedule,
  syncOnDeviceSession,
  type EventScheduleItem,
} from '../../api';
import { inferMatchCompleted } from '../../pages/matchStatus';
import { RunResults } from './RunResults';
import { useSavedRuns } from './useSavedRuns';
import { FieldCalibration, type CalibrationCapture } from './FieldCalibration';
import { MatchRecorder, type CapturedFrame } from './MatchRecorder';
import { VideoFileProcessor } from './VideoFileProcessor';
import { type InferenceTelemetry } from './benchmark';
import { ON_DEVICE_MODEL_VERSION } from './detector';
import { type Mat3 } from './homography';
import {
  getCalibration,
  markSessionSynced,
  markSessionSyncFailed,
  openDb,
  saveSession,
  type OnDeviceSessionPayload,
  type StoredSession,
} from './offlineStore';
import { classifyRecordingSyncFailure, RECORDING_SYNC_LABELS } from './recordingSyncStatus';
import { recordConfirmedSync } from '../offline/syncReceipt';
import { suggestionGroups, suggestionsFor } from './pathSuggestions';
import { buildRobotPaths } from './robotPaths';
import { type RawFrame } from './simpleTracker';
import { assemblePointsByTeam, type TrackPoint } from './trackProduction';
import { voteTrackIdentity } from './identityVote';
import {
  createCvPoseResolver,
  createLocalPoseResolver,
  grayFromCanvas,
  loadOpenCv,
  registerCalibrationFrame,
  USE_LOCAL_OPTICAL_FLOW,
  type CvPoseResolver,
} from './opticalFlow';
import type { GrayImage } from './opticalFlowCore';
import { flushPendingOnDeviceSessions } from './sync';
import { RecorderOfflineStatus } from '../offline/RecorderOfflineStatus';
import './OnDeviceRun.css';
import { TrackIdentityList, type PathSuggestion } from './TrackIdentityList';
import { getWorkspaceSession, getWorkspaceToken } from '../workspace/workspaceSession';
import { readCenterContextFromSearch } from '../../layout/centerContext';

function initialContext(): { eventKey: string; matchKey: string } {
  if (typeof window === 'undefined') return { eventKey: '', matchKey: '' };
  const query = window.location.hash.split('?')[1] || '';
  const context = readCenterContextFromSearch(query ? `?${query}` : '');
  const eventKey = context.eventKey || '';
  // A remembered match from another event would load the wrong teams.
  const matchKey = context.matchKey && (!eventKey || context.matchKey.startsWith(`${eventKey}_`)) ? context.matchKey : '';
  return { eventKey, matchKey };
}

const MATCH_KEY_PATTERN = /^\d{4}[a-z0-9]+_(?:qm\d+|ef\d+m\d+|qf\d+m\d+|sf\d+m\d+|f\d+m\d+)$/;
const EVENT_KEY_PATTERN = /^\d{4}[a-z0-9]{2,}$/;

function teamsOf(match: EventScheduleItem): MatchTeam[] {
  return [
    ...match.red.map((t) => ({ teamKey: t.team_key, alliance: 'red' as const })),
    ...match.blue.map((t) => ({ teamKey: t.team_key, alliance: 'blue' as const })),
  ];
}

function matchOptionLabel(match: EventScheduleItem, played: boolean): string {
  const name = match.display_name || match.match_key.split('_')[1]?.toUpperCase() || match.match_key;
  return played ? `${name} (played)` : name;
}

// The on-device match-breakdown flow, end to end:
//   setup (which match + its 6 teams) → calibrate (4-tap homography) → capture (camera +
//   in-browser detect) → identify (track + closed-set OCR vote / tap-ID) → result
//   (assemble per-team field tracks, store offline, sync → server shift-play).
// Every step is one of the tested onDevice modules; this screen is the glue + UI.

// Long enough that simply passing through camera mode on the way to "Upload video"
// never starts the blocking OpenCV.js compile, short enough that a scout who stays on
// the camera path still has stabilisation ready before they finish framing the shot.
const OPENCV_LOAD_GRACE_MS = 1500;

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
  alliance: Alliance | null; // from bumper colour
  thumb: Blob | null; // clearest photo of the robot on this path
};

const MIN_TRACK_POINTS = 3;
const STAGES: Stage[] = ['setup', 'calibrate', 'capture', 'identify', 'result'];

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

function formatMatchClock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// Which engine ran the detector, in words: the line a real-phone test is read from.
function engineLabel(provider: string | null | undefined): string {
  if (provider === 'webgpu') return 'the GPU (WebGPU)';
  if (provider === 'wasm') return 'the CPU (WASM)';
  return provider || 'an unknown engine';
}

export function OnDeviceRun() {
  const [stage, setStage] = useState<Stage>('setup');
  // Set when an uploaded video stopped playing before the match ended.
  const [coveredUntilSec, setCoveredUntilSec] = useState<number | null>(null);
  // Start from the match already picked elsewhere in the app; typing a raw
  // match key on a phone in the stands is the slowest part of setup.
  const [eventKey, setEventKey] = useState(() => initialContext().eventKey);
  const [matchKey, setMatchKey] = useState(() => initialContext().matchKey);
  const [teams, setTeams] = useState<MatchTeam[]>([]);
  const [shift1ActiveAlliance, setShift1ActiveAlliance] = useState<Alliance | ''>('');
  const [setupBusy, setSetupBusy] = useState(false);
  const [setupError, setSetupError] = useState('');
  const [loadedContext, setLoadedContext] = useState('');
  const setupSequence = useRef(0);
  const [calibrationError, setCalibrationError] = useState('');

  const baseHomographyRef = useRef<Mat3 | null>(null);
  const calibrationFrameRef = useRef<GrayImage | null>(null);
  const registeredStaticPoseRef = useRef<Mat3 | null>(null);
  const calibrationMetaRef = useRef<{
    version: string;
    rmseM: number | null;
    verified: boolean;
  } | null>(null);
  const capturedRef = useRef<CapturedFrame[]>([]);
  const poseTelemetryRef = useRef({ frames: 0, fallbackFrames: 0, opticalFlowFrames: 0 });
  const inferenceTelemetryRef = useRef<InferenceTelemetry | null>(null);
  const [capturedCount, setCapturedCount] = useState(0);
  const [breakdownTelemetry, setBreakdownTelemetry] = useState<InferenceTelemetry | null>(null);

  // Capture source: live camera (handheld, optical-flow stabilized) or an uploaded clip
  // (desktop, static calibration — one fixed camera view).
  const [captureBusy, setCaptureBusy] = useState(false);
  const captureStateProps = { onRecordingChange: setCaptureBusy };
  const [captureMode, setCaptureMode] = useState<'camera' | 'video'>('camera');
  const mountedRef = useRef(true);
  const [timingAnchorSec, setTimingAnchorSec] = useState(0);

  // Optical-flow camera stabilization (carries the calibrated pose through shake).
  const cvResolverRef = useRef<CvPoseResolver | null>(null);
  const [stabilize, setStabilize] = useState(true);
  const [stabStatus, setStabStatus] = useState<'off' | 'loading' | 'ready' | 'error'>('off');

  const [trackPoints, setTrackPoints] = useState<Record<number, TrackPoint[]>>({});
  const [summaries, setSummaries] = useState<TrackSummary[]>([]);
  // Object URLs for the path photos, released when the list changes or the page closes.
  const photoUrls = useMemo(() => {
    const urls: Record<number, string> = {};
    if (typeof URL.createObjectURL !== 'function') return urls;
    for (const s of summaries) if (s.thumb) urls[s.trackId] = URL.createObjectURL(s.thumb);
    return urls;
  }, [summaries]);
  useEffect(
    () => () => {
      for (const url of Object.values(photoUrls)) URL.revokeObjectURL(url);
    },
    [photoUrls],
  );
  const [identities, setIdentities] = useState<Record<number, string>>({}); // trackId -> teamKey
  const [pathSuggestion, setPathSuggestion] = useState<PathSuggestion | null>(null);

  const [savedSession, setSavedSession] = useState<StoredSession | null>(null);
  const { sessions: savedRuns } = useSavedRuns();
  const [resultBusy, setResultBusy] = useState(false);
  const [resultNote, setResultNote] = useState('');
  const resultRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (stage === 'result') {
      resultRef.current?.scrollIntoView?.({ block: 'start' });
      resultRef.current?.querySelector<HTMLElement>('h3')?.focus({ preventScroll: true });
    }
  }, [stage]);

  // A phone is usually scrolled to the button that moved it on, which left the
  // next step's instructions above the screen.
  const stepsRef = useRef<HTMLDivElement>(null);
  const shownStage = useRef(stage);
  useEffect(() => {
    if (shownStage.current === stage) return;
    shownStage.current = stage;
    if (stage === 'result') return;
    const steps = stepsRef.current;
    if (steps && steps.getBoundingClientRect().top < 64) steps.scrollIntoView?.({ block: 'start' });
  }, [stage]);

  const candidateTeamKeys = useMemo(() => teams.map((t) => t.teamKey), [teams]);

  // ── setup ───────────────────────────────────────────────────────────
  const normalizedMatchKey = matchKey.trim().toLowerCase();
  const normalizedEventKey = (eventKey.trim() || normalizedMatchKey.split('_')[0]).toLowerCase();

  const resetRunState = useCallback(() => {
    setCaptureBusy(false);
    capturedRef.current = [];
    cvResolverRef.current?.dispose();
    cvResolverRef.current = null;
    setCapturedCount(0);
    poseTelemetryRef.current = { frames: 0, fallbackFrames: 0, opticalFlowFrames: 0 };
    inferenceTelemetryRef.current = null;
    setBreakdownTelemetry(null);
    setTrackPoints({});
    setSummaries([]);
    setIdentities({});
    setPathSuggestion(null);
    setSavedSession(null);
    setResultBusy(false);
    setResultNote('');
    setTimingAnchorSec(0);
  }, []);

  const setupContext = `${normalizedEventKey}:${normalizedMatchKey}`;
  const currentSetupContext = useRef(setupContext);
  currentSetupContext.current = setupContext;
  const teamsReady = loadedContext === setupContext && teams.length > 0;

  function changeSetupInput(field: 'match' | 'event', value: string) {
    ++setupSequence.current;
    setSetupBusy(false);
    setSetupError('');
    setLoadedContext('');
    setTeams([]);
    setShift1ActiveAlliance('');
    if (field === 'match') setMatchKey(value);
    else setEventKey(value);
  }

  const loadTeams = useCallback(async () => {
    const sequence = ++setupSequence.current;
    const context = `${normalizedEventKey}:${normalizedMatchKey}`;
    setSetupBusy(true);
    setSetupError('');
    setLoadedContext('');
    setTeams([]);
    try {
      if (!MATCH_KEY_PATTERN.test(normalizedMatchKey)) {
        throw new Error('Enter a match key such as 2026txhou_qm1.');
      }
      if (normalizedMatchKey.split('_')[0] !== normalizedEventKey) {
        throw new Error('The event key must match the event at the start of the match key.');
      }
      const sched = await getEventSchedule(normalizedEventKey, false, { includeTeams: true });
      if (sequence !== setupSequence.current || currentSetupContext.current !== context) return;
      const match = sched.matches.find((m) => m.match_key === normalizedMatchKey);
      if (!match) throw new Error('Match not found in this event schedule.');
      const loaded = teamsOf(match);
      if (loaded.length === 0) throw new Error('No teams listed for this match yet.');
      resetRunState();
      setShift1ActiveAlliance('');
      setTeams(loaded);
      setLoadedContext(context);
    } catch (err) {
      if (sequence === setupSequence.current && currentSetupContext.current === context) {
        setSetupError(err instanceof Error ? err.message : 'Could not load the match.');
      }
    } finally {
      if (sequence === setupSequence.current) setSetupBusy(false);
    }
  }, [normalizedEventKey, normalizedMatchKey, resetRunState]);

  // With the event known, pick the match from its schedule instead of typing
  // "2026txhou_qm1" on a phone; typing stays available for anything else.
  const [eventSchedule, setEventSchedule] = useState<{ eventKey: string; matches: EventScheduleItem[] } | null>(null);
  const [typingMatchKey, setTypingMatchKey] = useState(false);
  const scheduleEventKey = eventKey.trim().toLowerCase();
  useEffect(() => {
    if (!EVENT_KEY_PATTERN.test(scheduleEventKey)) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      getEventSchedule(scheduleEventKey, false, { includeTeams: true })
        .then((sched) => {
          if (cancelled) return;
          const matches = (sched.matches || []).filter((m) => m.red.length + m.blue.length > 0);
          setEventSchedule(matches.length ? { eventKey: scheduleEventKey, matches } : null);
        })
        .catch(() => { if (!cancelled) setEventSchedule(null); });
    }, 400);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [scheduleEventKey]);
  const pickerMatches = eventSchedule?.eventKey === scheduleEventKey ? eventSchedule.matches : null;
  const showMatchPicker = Boolean(pickerMatches) && !typingMatchKey;

  const pickMatch = useCallback((key: string) => {
    const match = pickerMatches?.find((m) => m.match_key === key);
    ++setupSequence.current;
    setSetupBusy(false);
    setSetupError('');
    setShift1ActiveAlliance('');
    setMatchKey(key);
    if (!match) {
      setLoadedContext('');
      setTeams([]);
      return;
    }
    resetRunState();
    setTeams(teamsOf(match));
    setLoadedContext(`${scheduleEventKey}:${key}`);
  }, [pickerMatches, resetRunState, scheduleEventKey]);

  // Start on the match being filmed: the one already picked, else the next unplayed one.
  useEffect(() => {
    if (!showMatchPicker || !pickerMatches || stage !== 'setup' || teamsReady) return;
    const nowMs = Date.now();
    const current = pickerMatches.find((m) => m.match_key === normalizedMatchKey);
    const next = current
      || pickerMatches.find((m) => !inferMatchCompleted(m, nowMs))
      || pickerMatches[pickerMatches.length - 1];
    if (next) pickMatch(next.match_key);
  }, [showMatchPicker, pickerMatches, stage, teamsReady, normalizedMatchKey, pickMatch]);

  // ── calibrate ─────────────────────────────────────────────────────────
  const onCalibrated = useCallback((cal: CalibrationCapture) => {
    baseHomographyRef.current = cal.homography;
    calibrationFrameRef.current = cal.referenceFrame;
    registeredStaticPoseRef.current = null;
    setCalibrationError('');
    calibrationMetaRef.current = {
      version: 'manual_corners_v1',
      rmseM: Number.isFinite(cal.rmseM) ? cal.rmseM : null,
      verified: true,
    };
    setStage('capture');
  }, []);

  const applySavedCalibration = useCallback(async () => {
    let db: IDBDatabase | null = null;
    try {
      db = await openDb();
      const saved = await getCalibration(db, 'current');
      if (saved) {
        if (
          !saved.imageWidth || !saved.imageHeight || !saved.referenceGray ||
          saved.referenceGray.length !== saved.imageWidth * saved.imageHeight
        ) {
          setCalibrationError('Saved calibration has no matching camera frame. Please re-calibrate.');
          return;
        }
        baseHomographyRef.current = saved.homography;
        calibrationFrameRef.current = {
          data: new Float32Array(saved.referenceGray),
          width: saved.imageWidth,
          height: saved.imageHeight,
        };
        registeredStaticPoseRef.current = null;
        setCalibrationError('');
        calibrationMetaRef.current = {
          version: saved.calibrationVersion || 'manual_corners_v1_legacy',
          rmseM: Number.isFinite(saved.rmseM) ? Number(saved.rmseM) : null,
          verified: Boolean(saved.verified),
        };
        setStage('capture');
      }
    } catch {
      setCalibrationError('Could not load the saved calibration. Please re-calibrate.');
    } finally {
      db?.close();
    }
  }, []);

  // ── capture → identify ──────────────────────────────────────────────────
  const captureToMatchOffsetSec = captureMode === 'video' ? -timingAnchorSec : timingAnchorSec;

  const registerStaticPose = useCallback((canvas: HTMLCanvasElement): Mat3 | null => {
    if (registeredStaticPoseRef.current) return registeredStaticPoseRef.current;
    const base = baseHomographyRef.current;
    if (!base) return null;
    const reference = calibrationFrameRef.current;
    if (!reference) return base;
    const registered = registerCalibrationFrame(base, reference, grayFromCanvas(canvas));
    registeredStaticPoseRef.current = registered;
    return registered;
  }, []);

  // Per-frame pose: when stabilization is ready, carry the calibrated homography by
  // optical-flow motion (survives shake); otherwise fall back to the static calibration.
  const resolvePose = useCallback((canvas: HTMLCanvasElement, timeSec: number): Mat3 | null => {
    const base = baseHomographyRef.current;
    if (!base) return null;
    const matchTimeSec = timeSec + captureToMatchOffsetSec;
    if (matchTimeSec < 0 || matchTimeSec > 165) return null;
    poseTelemetryRef.current.frames += 1;
    const resolver = cvResolverRef.current;
    if (resolver) {
      poseTelemetryRef.current.opticalFlowFrames += 1;
      const pose = resolver.resolve(canvas) ?? base;
      if (resolver.lostFrames() > 0) poseTelemetryRef.current.fallbackFrames += 1;
      return pose;
    }
    if (stabilize) poseTelemetryRef.current.fallbackFrames += 1;
    return registerStaticPose(canvas);
  }, [captureToMatchOffsetSec, registerStaticPose, stabilize]);

  // Video clips are one fixed camera view, so each sampled frame uses the base
  // calibration directly (optical-flow carry is for the handheld camera path).
  const resolvePoseStatic = useCallback((_canvas: HTMLCanvasElement, timeSec: number): Mat3 | null => {
    const matchTimeSec = timeSec + captureToMatchOffsetSec;
    if (matchTimeSec < 0 || matchTimeSec > 165) return null;
    if (baseHomographyRef.current) poseTelemetryRef.current.frames += 1;
    return registerStaticPose(_canvas);
  }, [captureToMatchOffsetSec, registerStaticPose]);

  const onInferenceTelemetry = useCallback((telemetry: InferenceTelemetry) => {
    inferenceTelemetryRef.current = telemetry;
  }, []);

  // Lazily load OpenCV.js and build the stabilized resolver on entering the capture
  // stage. Heavy WASM, so only when stabilization is on; degrades to the static pose on
  // failure. Torn down on leaving capture or toggling the option.
  //
  // The load is held behind a short grace period because it is genuinely blocking, not
  // merely slow: OpenCV.js is an ~8 MB emscripten bundle injected as a classic script,
  // and compiling it occupies the main thread. Capture opens in camera mode, so without
  // this delay a scout who lands on the capture step and immediately picks "Upload video"
  // has already started a compile that nothing can cancel. That compile then starves
  // onnxruntime's WebGPU session creation, so the detector never finishes initialising
  // and the upload run hangs before producing a single track -- it also blocks the
  // timers inside loadOpenCv, so its own load timeout cannot fire to release it.
  useEffect(() => {
    cvResolverRef.current?.dispose();
    cvResolverRef.current = null;
    const base = baseHomographyRef.current;
    if (stage !== 'capture' || captureMode !== 'camera' || !stabilize || !base) {
      setStabStatus('off');
      return;
    }
    // The local core needs no download and no compile, so there is nothing to wait for
    // and nothing to stall the main thread -- the grace period below exists only for the
    // OpenCV build it replaces.
    if (USE_LOCAL_OPTICAL_FLOW) {
      cvResolverRef.current = createLocalPoseResolver(base, calibrationFrameRef.current ?? undefined);
      setStabStatus('ready');
      return () => {
        cvResolverRef.current?.dispose();
        cvResolverRef.current = null;
      };
    }

    let cancelled = false;
    setStabStatus('loading');
    const graceTimer = setTimeout(() => {
      if (cancelled) return;
      loadOpenCv()
        .then((cv) => {
          if (cancelled) return;
          cvResolverRef.current = createCvPoseResolver(cv, base, calibrationFrameRef.current ?? undefined);
          setStabStatus('ready');
        })
        .catch(() => {
          if (!cancelled) setStabStatus('error');
        });
    }, OPENCV_LOAD_GRACE_MS);
    return () => {
      cancelled = true;
      clearTimeout(graceTimer);
      cvResolverRef.current?.dispose();
      cvResolverRef.current = null;
    };
  }, [stage, stabilize, captureMode]);

  // The sync flow awaits IndexedDB and the network, so its state updates can land after
  // the screen is gone. Without this guard React schedules an update against a torn-down
  // tree -- which surfaced as an unhandled "window is not defined" in CI, where the
  // slower run let the teardown win the race.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const onFrame = useCallback((frame: CapturedFrame) => {
    // Track points use match-elapsed time. Drop pre-match video frames and carry
    // the numeric anchor as provenance so the server can audit the alignment.
    const matchTimeSec = frame.timeSec + captureToMatchOffsetSec;
    if (matchTimeSec < 0 || matchTimeSec > 165) return;
    capturedRef.current.push({ ...frame, timeSec: matchTimeSec });
    setCapturedCount(capturedRef.current.length);
  }, [captureToMatchOffsetSec]);

  const buildTracks = useCallback((result?: { coveredUntilSec: number | null }) => {
    if (capturedRef.current.length === 0) return;
    setBreakdownTelemetry(inferenceTelemetryRef.current);
    setCoveredUntilSec(result?.coveredUntilSec ?? null);
    const captured = capturedRef.current;
    const rawFrames: RawFrame[] = captured.map((f) => ({
      timeSec: f.timeSec,
      homography: f.homography,
      detections: f.detections,
    }));
    // Fragments of one robot are stitched into a single path, so each robot is ideally
    // one tap; its bumper colour says which alliance's teams to offer first.
    const paths = buildRobotPaths(rawFrames, { minPoints: MIN_TRACK_POINTS });
    const produced: Record<number, TrackPoint[]> = {};
    const rows: TrackSummary[] = [];
    const seedIds: Record<number, string> = {};
    for (const path of paths) {
      const points = path.points;
      const trackId = path.pathId;
      produced[trackId] = points;
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
        alliance: path.alliance,
        thumb: path.thumb,
      });
    }
    rows.sort((a, b) => b.pointCount - a.pointCount);
    setTrackPoints(produced);
    setSummaries(rows);
    setIdentities(seedIds);
    setStage('identify');
  }, [candidateTeamKeys]);

  // Likely continuations of each path (pathSuggestions.ts), offered after an assignment.
  const suggestionPaths = useMemo(
    () => summaries.map((s) => ({ pathId: s.trackId, points: trackPoints[s.trackId] ?? [], alliance: s.alliance })),
    [summaries, trackPoints],
  );
  const pathGroups = useMemo(() => suggestionGroups(suggestionPaths), [suggestionPaths]);

  const assignIdentity = useCallback((trackId: number, teamKey: string) => {
    const next = { ...identities };
    if (teamKey) next[trackId] = teamKey;
    else delete next[trackId];
    setIdentities(next);
    const trackIds = teamKey ? suggestionsFor(suggestionPaths, pathGroups, trackId, teamKey, next) : [];
    setPathSuggestion(trackIds.length ? { teamKey, seedId: trackId, trackIds } : null);
  }, [identities, pathGroups, suggestionPaths]);

  const applyPathSuggestion = useCallback(() => {
    if (!pathSuggestion) return;
    setIdentities((prev) => {
      const next = { ...prev };
      for (const id of pathSuggestion.trackIds) if (!next[id]) next[id] = pathSuggestion.teamKey;
      return next;
    });
    setPathSuggestion(null);
  }, [pathSuggestion]);

  // ── result: assemble → store offline → sync ───────────────────────────
  const finishAndSync = useCallback(async () => {
    setResultBusy(true);
    setResultNote('');
    setSavedSession(null);
    const pointsByTeam = assemblePointsByTeam(trackPoints, identities);
    const eligiblePointCount = summaries.reduce((total, summary) => total + summary.pointCount, 0);
    const assignedPointCount = summaries.reduce(
      (total, summary) => total + (identities[summary.trackId] ? summary.pointCount : 0),
      0,
    );
    const identityConfidence = eligiblePointCount > 0
      ? assignedPointCount / eligiblePointCount
      : 0;
    const pose = poseTelemetryRef.current;
    const poseFallbackRatio = pose.frames > 0 ? pose.fallbackFrames / pose.frames : 1;
    const poseSource: OnDeviceSessionPayload['poseSource'] = captureMode === 'video'
      ? 'static'
      : pose.opticalFlowFrames === 0
        ? 'static'
        : pose.fallbackFrames > 0
          ? 'mixed'
          : 'optical_flow';
    const inference = inferenceTelemetryRef.current;
    const calibration = calibrationMetaRef.current;
    const payload: OnDeviceSessionPayload = {
      pointsByTeam,
      schemaVersion: 'on_device_session_v2',
      modelVersion: inference?.modelVersion || ON_DEVICE_MODEL_VERSION,
      calibrationVersion: calibration?.version || 'manual_corners_v1_legacy',
      calibrationRmseM: calibration?.rmseM ?? null,
      calibrationVerified: Boolean(calibration?.verified),
      captureSource: captureMode,
      poseSource,
      poseFallbackRatio,
      identityConfidence,
      identitySource: 'manual',
      timingSource: captureMode === 'camera' ? 'manual' : 'video_offset',
      captureToMatchOffsetSec,
      shift1ActiveAlliance: shift1ActiveAlliance || null,
      shift1Source: shift1ActiveAlliance ? 'manual_scout_selection' : null,
      executionProvider: inference?.executionProvider ?? null,
      sampledFrameCount: inference?.iterations ?? capturedRef.current.length,
      inferenceMedianMs: inference?.msMedian ?? null,
      inferenceP90Ms: inference?.msP90 ?? null,
      thermalDriftPct: inference?.thermalDriftPct ?? null,
    };
    let session: StoredSession = {
      id: newId(),
      eventKey: normalizedEventKey,
      matchKey: normalizedMatchKey,
      createdAt: Date.now(),
      synced: false,
      workspaceId: getWorkspaceSession()?.workspace.id ?? null,
      workspaceName: getWorkspaceSession()?.workspace.name,
      payload,
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
      // Keep the identification screen and captured data available for another save attempt.
    }

    if (!mountedRef.current) return;
    if (!storedLocally) {
      setResultNote('This recording could not be saved on this phone. Keep this page open and try saving again after checking browser storage.');
      setResultBusy(false);
      return;
    }
    setSavedSession(session);
    setStage('result');
    if (typeof navigator !== 'undefined' && !navigator.onLine) {
      setResultNote('Saved offline. It will sync automatically when you reconnect.');
      setResultBusy(false);
      return;
    }
    if (!getWorkspaceToken()) {
      // The server only accepts runs from a team member; the run stays pending
      // here and goes up with the next sync after the scout joins their team.
      setResultNote('Saved on this device. Join your team on My Team to sync runs to your team.');
      setResultBusy(false);
      return;
    }
    try {
      const workspace = getWorkspaceSession();
      if (!workspace) {
        setResultNote(RECORDING_SYNC_LABELS['join-team']);
        return;
      }
      if (session.workspaceId != null && session.workspaceId !== workspace.workspace.id) {
        setResultNote(RECORDING_SYNC_LABELS['other-team']);
        return;
      }
      if (session.workspaceId == null) {
        session = { ...session, workspaceId: workspace.workspace.id, workspaceName: workspace.workspace.name };
        const db = await openDb();
        try { await saveSession(db, session); }
        finally { db.close(); }
      }
      const current = getWorkspaceSession();
      if (current?.workspace.id !== workspace.workspace.id || current.token !== workspace.token) {
        setResultNote('Saved on this phone. Your team changed during the upload preparation; check My Team to sync.');
        return;
      }
      const res = await syncOnDeviceSession({
        id: session.id,
        eventKey: session.eventKey,
        matchKey: session.matchKey,
        createdAt: session.createdAt,
        workspaceId: session.workspaceId,
        payload: session.payload,
      }, workspace.token);
      recordConfirmedSync();
      if (mountedRef.current) setSavedSession({ ...session, synced: true, syncResult: res });
      if (storedLocally) {
        try {
          const db = await openDb();
          try {
            await markSessionSynced(db, session.id, res);
          } finally {
            db.close();
          }
        } catch {
          // Direct sync succeeded; a later auto-flush can reconcile local state.
        }
      }
      // Flush any earlier pending runs after the current one is marked synced.
      void flushPendingOnDeviceSessions().catch(() => { /* saved locally; sync card offers retry */ });
    } catch (err) {
      try {
        const db = await openDb();
        try { await markSessionSyncFailed(db, session.id, err); }
        finally { db.close(); }
      } catch { /* the original session remains saved; My Team can retry */ }
      if (mountedRef.current) setResultNote(RECORDING_SYNC_LABELS[classifyRecordingSyncFailure(err).kind]);
    } finally {
      if (mountedRef.current) setResultBusy(false);
    }
  }, [captureMode, captureToMatchOffsetSec, identities, normalizedEventKey, normalizedMatchKey, shift1ActiveAlliance, summaries, trackPoints]);

  const resolvedCount = Object.keys(identities).length;
  const persistedSession = savedRuns.find((run) => run.id === savedSession?.id);
  const resultSession = savedSession ? {
    ...savedSession,
    ...persistedSession,
    synced: savedSession.synced || persistedSession?.synced || false,
    syncResult: persistedSession?.syncResult ?? savedSession.syncResult,
  } : null;

  return (
    <div className="on-device-run">
      <div className="odr-steps" role="list" ref={stepsRef}>
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
            Pick the match you&apos;re filming. Its six teams are used to label the robots and
            to tell attack from defense.
          </p>
          <RecorderOfflineStatus />
          {showMatchPicker && pickerMatches ? (
          <div className="odr-form">
            <label className="odr-field">
              <span className="odr-label">Match at {scheduleEventKey}</span>
              <select
                className="odr-select"
                value={pickerMatches.some((m) => m.match_key === normalizedMatchKey) ? normalizedMatchKey : ''}
                onChange={(e) => pickMatch(e.target.value)}
              >
                {pickerMatches.some((m) => m.match_key === normalizedMatchKey) ? null : <option value="">Pick a match</option>}
                {pickerMatches.map((m) => (
                  <option key={m.match_key} value={m.match_key}>
                    {matchOptionLabel(m, inferMatchCompleted(m, Date.now()))}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="center-btn ghost" onClick={() => setTypingMatchKey(true)}>
              Type a match key instead
            </button>
          </div>
          ) : (
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
                onChange={(e) => changeSetupInput('match', e.target.value)}
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
                onChange={(e) => changeSetupInput('event', e.target.value)}
              />
            </label>
          </div>
          )}
          {showMatchPicker ? null : (
          <div className="odr-actions">
            <button
              type="button"
              className="center-btn"
              onClick={() => void loadTeams()}
              disabled={!normalizedMatchKey || setupBusy}
            >
              {setupBusy ? 'Loading…' : 'Load match teams'}
            </button>
            {pickerMatches ? (
              <button type="button" className="center-btn ghost" onClick={() => setTypingMatchKey(false)}>
                Pick from the schedule
              </button>
            ) : null}
          </div>
          )}
          {setupError ? <p className="odr-error" role="alert">{setupError}</p> : null}
          {teamsReady ? (
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
              <label className="odr-field">
                <span className="odr-label">Alliance with the active hub in Shift 1</span>
                <select
                  className="odr-select"
                  aria-label="Alliance with the active hub in Shift 1"
                  value={shift1ActiveAlliance}
                  onChange={(event) => setShift1ActiveAlliance(event.target.value as Alliance | '')}
                >
                  <option value="">From the official results</option>
                  <option value="red">Red</option>
                  <option value="blue">Blue</option>
                </select>
                <span className="odr-hint">
                  Leave this alone: the server reads it from the match's official results,
                  even if you sync before they're posted. Pick a colour only if you saw the
                  field display and the match won't get official results.
                </span>
              </label>
              <div className="odr-actions">
                <button
                  type="button"
                  className="center-btn"
                  onClick={() => setStage('calibrate')}
                >
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
          {calibrationError ? <p className="field-calibration__error">{calibrationError}</p> : null}
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
                disabled={captureBusy}
                className={`segmented-tabs__item${captureMode === mode ? ' active' : ''}`}
                onClick={() => {
                  if (mode === captureMode || captureBusy) return;
                  if (capturedRef.current.length && !window.confirm('Discard these captured frames and switch source?')) return;
                  capturedRef.current = [];
                  setCapturedCount(0);
                  poseTelemetryRef.current = {
                    frames: 0,
                    fallbackFrames: 0,
                    opticalFlowFrames: 0,
                  };
                  inferenceTelemetryRef.current = null;
                  registeredStaticPoseRef.current = null;
                  setCaptureMode(mode);
                  setTimingAnchorSec(0);
                }}
              >
                {mode === 'camera' ? 'Record' : 'Upload video'}
              </button>
            ))}
          </div>

          <label className="odr-field">
            <span className="odr-label">
              {captureMode === 'video'
                ? 'Match starts at video timestamp (seconds)'
                : 'Match clock at recording start (seconds)'}
            </span>
            <input
              className="odr-input"
              type="number"
              inputMode="decimal"
              aria-label={captureMode === 'video'
                ? 'Match starts at video timestamp (seconds)'
                : 'Match clock at recording start (seconds)'}
              min="0"
              max="300"
              step="0.1"
              value={timingAnchorSec}
              onChange={(event) => {
                const next = Number(event.target.value);
                setTimingAnchorSec(Number.isFinite(next) ? Math.max(0, Math.min(300, next)) : 0);
              }}
            />
            <span className="odr-hint">
              {captureMode === 'video'
                ? 'Pregame frames before this timestamp are dropped; remaining timestamps start at match second 0.'
                : 'Use 0 when recording begins with the match, or enter the elapsed match seconds if recording starts late.'}
            </span>
          </label>

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
              <MatchRecorder
                {...captureStateProps}
                resolvePose={resolvePose}
                onFrame={onFrame}
                onTelemetry={onInferenceTelemetry}
                onComplete={buildTracks}
              />
            </>
          ) : (
            <>
              <p className="odr-hint">
                Choose a phone recording of the match (a fixed wide field view works best). Frames are sampled and
                robots detected on-device, then identified and synced — no camera or second screen
                needed.
              </p>
              <VideoFileProcessor
                {...captureStateProps}
                matchStartSec={timingAnchorSec}
                resolvePose={resolvePoseStatic}
                onFrame={onFrame}
                onComplete={buildTracks}
                onTelemetry={onInferenceTelemetry}
              />
            </>
          )}

          <div className="odr-actions">
            <button type="button" className="center-btn" onClick={() => buildTracks()} disabled={capturedCount === 0}>
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
            Found {summaries.length} robot path{summaries.length === 1 ? '' : 's'}. Pick the team
            for each one; any you leave blank are left out.
          </p>
          {breakdownTelemetry ? (
            <p className="muted">
              Ran on {engineLabel(breakdownTelemetry.executionProvider)} ·{' '}
              {breakdownTelemetry.msMedian.toFixed(0)} ms per frame
            </p>
          ) : null}
          {coveredUntilSec !== null ? (
            <p className="field-calibration__error">
              The video stopped playing {formatMatchClock(coveredUntilSec)} into the match, so these
              paths only cover up to there. Upload it again, or trim the clip to just the match.
            </p>
          ) : null}
          {resultNote ? <p className="odr-error" role="alert">{resultNote}</p> : null}
          <TrackIdentityList summaries={summaries} identities={identities} photoUrls={photoUrls} teams={teams} onAssign={assignIdentity} suggestion={pathSuggestion} onApplySuggestion={applyPathSuggestion} onDismissSuggestion={() => setPathSuggestion(null)} />
          <div className="odr-actions">
            <button
              type="button"
              className="center-btn"
              onClick={() => void finishAndSync()}
              disabled={resolvedCount === 0 || resultBusy}
            >
              {resultBusy ? 'Saving…' : `Save & view results${resolvedCount > 0 ? ` (${resolvedCount})` : ''}`}
            </button>
            <button type="button" className="center-btn ghost" onClick={() => setStage('capture')}>
              Back
            </button>
          </div>
        </div>
      ) : null}

      {stage === 'result' ? (
        <div className="odr-section" ref={resultRef}>
          {resultNote ? <p className="odr-hint">{resultNote}</p> : null}
          {resultSession ? (
            <RunResults session={resultSession} syncing={resultBusy} />
          ) : null}
          <p className="odr-hint">Reopen this run below in Saved runs on this device.</p>
          <div className="odr-actions">
            <button
              type="button"
              className="center-btn"
              onClick={() => {
                resetRunState();
                setMatchKey('');
                setLoadedContext('');
                setTeams([]);
                setShift1ActiveAlliance('');
                setStage('setup');
              }}
            >
              Record another match
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
