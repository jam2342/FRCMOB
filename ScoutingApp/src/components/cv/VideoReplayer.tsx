import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { TrackRow, MatchTracksResponse } from '../../api';
import './VideoReplayer.css';

/* ── constants ────────────────────────────────────────────────────── */

const SPEEDS = [0.25, 0.5, 1, 1.5, 2] as const;

/** Colours assigned to track IDs so each robot gets a stable colour. */
const TRACK_COLORS: string[] = [
  '#ef4444', '#3b82f6', '#22c55e', '#eab308', '#a855f7',
  '#f97316', '#06b6d4', '#ec4899', '#84cc16', '#6366f1',
];

function colorForTrack(trackId: number): string {
  return TRACK_COLORS[trackId % TRACK_COLORS.length];
}

function teamNumberFromKey(key: string | null): string {
  if (!key) return '?';
  const m = key.match(/frc(\d+)/);
  return m ? m[1] : key;
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/* ── types ────────────────────────────────────────────────────────── */

type Props = {
  data: MatchTracksResponse;
  /** YouTube embed or local /media URL — if null, shows tracks-only mode. */
  videoUrl?: string | null;
  className?: string;
  seekToTimeSec?: number | null;
  highlightWindow?: { start: number; end: number } | null;
};

/* ── component ────────────────────────────────────────────────────── */

export function VideoReplayer({ data, videoUrl, className, seekToTimeSec }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number>(0);
  const trackOnlyTickRef = useRef<number | null>(null);
  const lastExternalSeekRef = useRef<number | null>(null);

  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [speedIdx, setSpeedIdx] = useState(2); // default 1×
  const [activeTeams, setActiveTeams] = useState<Set<string>>(new Set(data.team_keys));

  /* ── derived: index tracks by rounded time (100ms buckets) ──── */
  const trackIndex = useMemo(() => {
    const idx = new Map<number, TrackRow[]>();
    for (const t of data.tracks) {
      const bucket = Math.round(t.time_sec * 10); // 100ms buckets
      const arr = idx.get(bucket);
      if (arr) arr.push(t);
      else idx.set(bucket, [t]);
    }
    return idx;
  }, [data.tracks]);

  const timeRange = useMemo(() => {
    if (data.tracks.length === 0) return { min: 0, max: 0 };
    let min = Infinity, max = -Infinity;
    for (const t of data.tracks) {
      if (t.time_sec < min) min = t.time_sec;
      if (t.time_sec > max) max = t.time_sec;
    }
    return { min, max };
  }, [data.tracks]);
  const hasPlayableVideo = Boolean(videoUrl);
  const effectiveDuration = duration || timeRange.max;

  /* ── toggle team filter ─────────────────────────────────────── */
  const toggleTeam = useCallback((tk: string) => {
    setActiveTeams(prev => {
      const next = new Set(prev);
      if (next.has(tk)) next.delete(tk);
      else next.add(tk);
      return next;
    });
  }, []);

  /* ── draw overlay ───────────────────────────────────────────── */
  const drawOverlay = useCallback((time: number) => {
    const canvas = canvasRef.current;
    const viewport = viewportRef.current;
    if (!canvas || !viewport) return;

    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = vw * dpr;
    canvas.height = vh * dpr;
    canvas.style.width = `${vw}px`;
    canvas.style.height = `${vh}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, vw, vh);

    const cal = data.calibration;
    if (!cal) return; // can't project without calibration

    const scaleX = vw / cal.image_width;
    const scaleY = vh / cal.image_height;

    // Collect rows within ±150ms of the current time
    const bucket = Math.round(time * 10);
    const rows: TrackRow[] = [];
    for (let b = bucket - 1; b <= bucket + 1; b++) {
      const arr = trackIndex.get(b);
      if (arr) rows.push(...arr);
    }

    for (const row of rows) {
      if (row.team_key && !activeTeams.has(row.team_key)) continue;

      const color = colorForTrack(row.track_id);
      const x1 = row.bbox[0] * scaleX;
      const y1 = row.bbox[1] * scaleY;
      const x2 = row.bbox[2] * scaleX;
      const y2 = row.bbox[3] * scaleY;
      const bw = x2 - x1;
      const bh = y2 - y1;

      // Bounding box
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(x1, y1, bw, bh);

      // Label background
      const label = teamNumberFromKey(row.team_key);
      ctx.font = `bold ${Math.max(10, Math.min(14, bw * 0.2))}px Inter, sans-serif`;
      const metrics = ctx.measureText(label);
      const labelH = 16;
      const labelW = metrics.width + 8;

      ctx.fillStyle = color;
      ctx.fillRect(x1, y1 - labelH, labelW, labelH);

      // Label text
      ctx.fillStyle = '#fff';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, x1 + 4, y1 - labelH / 2);

      // Confidence dot (optional)
      if (row.confidence != null && row.confidence < 0.5) {
        ctx.fillStyle = 'rgba(234, 179, 8, 0.7)';
        ctx.beginPath();
        ctx.arc(x2 - 4, y1 + 4, 3, 0, Math.PI * 2);
        ctx.fill();
      }

      // Speed indicator
      if (row.speed_mps != null) {
        const speedLabel = `${row.speed_mps.toFixed(1)} m/s`;
        ctx.font = `${Math.max(9, Math.min(11, bw * 0.15))}px Inter, sans-serif`;
        ctx.fillStyle = 'rgba(255,255,255,0.6)';
        ctx.textBaseline = 'top';
        ctx.fillText(speedLabel, x1 + 2, y2 + 2);
      }
    }
  }, [data.calibration, trackIndex, activeTeams]);

  /* ── animation loop tied to video time ──────────────────────── */
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !hasPlayableVideo) return;

    const onTimeUpdate = () => {
      setCurrentTime(video.currentTime);
    };

    const onLoaded = () => {
      setDuration(video.duration);
    };

    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnded = () => setPlaying(false);

    video.addEventListener('timeupdate', onTimeUpdate);
    video.addEventListener('loadedmetadata', onLoaded);
    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    video.addEventListener('ended', onEnded);

    // High-frequency RAF loop while playing for smooth overlay
    let active = true;
    const tick = () => {
      if (!active) return;
      if (!video.paused) {
        drawOverlay(video.currentTime);
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    tick();

    return () => {
      active = false;
      cancelAnimationFrame(rafRef.current);
      video.removeEventListener('timeupdate', onTimeUpdate);
      video.removeEventListener('loadedmetadata', onLoaded);
      video.removeEventListener('play', onPlay);
      video.removeEventListener('pause', onPause);
      video.removeEventListener('ended', onEnded);
    };
  }, [drawOverlay, hasPlayableVideo]);

  useEffect(() => {
    if (hasPlayableVideo || !playing) {
      trackOnlyTickRef.current = null;
      return;
    }

    let active = true;
    const tick = (now: number) => {
      if (!active) return;
      const last = trackOnlyTickRef.current ?? now;
      const elapsedSec = ((now - last) / 1000) * SPEEDS[speedIdx];
      trackOnlyTickRef.current = now;
      setCurrentTime((prev) => {
        const next = Math.min(effectiveDuration, prev + elapsedSec);
        if (next >= effectiveDuration) {
          setPlaying(false);
          trackOnlyTickRef.current = null;
        }
        return next;
      });
      if (active) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      active = false;
      trackOnlyTickRef.current = null;
      cancelAnimationFrame(rafRef.current);
    };
  }, [effectiveDuration, hasPlayableVideo, playing, speedIdx]);

  useEffect(() => {
    drawOverlay(currentTime);
  }, [currentTime, drawOverlay]);

  /* ── redraw on resize ───────────────────────────────────────── */
  useEffect(() => {
    const onResize = () => drawOverlay(currentTime);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [drawOverlay, currentTime]);

  useEffect(() => {
    if (seekToTimeSec == null || Number.isNaN(seekToTimeSec)) return;
    if (lastExternalSeekRef.current === seekToTimeSec) return;
    lastExternalSeekRef.current = seekToTimeSec;
    const clamped = Math.max(0, Math.min(effectiveDuration || seekToTimeSec, seekToTimeSec));
    const video = videoRef.current;
    if (hasPlayableVideo && video) {
      video.currentTime = clamped;
    } else {
      requestAnimationFrame(() => {
        setCurrentTime(clamped);
      });
    }
  }, [effectiveDuration, hasPlayableVideo, seekToTimeSec]);

  /* ── play / pause ───────────────────────────────────────────── */
  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (hasPlayableVideo && video) {
      if (video.paused) {
        void video.play().catch(() => setPlaying(false));
      } else {
        video.pause();
      }
      return;
    }

    if (playing) {
      setPlaying(false);
      return;
    }
    if (currentTime >= effectiveDuration) {
      setCurrentTime(timeRange.min);
    }
    setPlaying(true);
  }, [currentTime, effectiveDuration, hasPlayableVideo, playing, timeRange.min]);

  const handleScrub = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const video = videoRef.current;
    const t = parseFloat(e.target.value);
    if (hasPlayableVideo && video) {
      video.currentTime = t;
    }
    setCurrentTime(t);
  }, [hasPlayableVideo]);

  const cycleSpeed = useCallback(() => {
    setSpeedIdx(prev => {
      const next = (prev + 1) % SPEEDS.length;
      const video = videoRef.current;
      if (hasPlayableVideo && video) video.playbackRate = SPEEDS[next];
      return next;
    });
  }, [hasPlayableVideo]);

  /* ── no data ────────────────────────────────────────────────── */
  if (data.total_rows === 0) {
    return (
      <div className={`video-replayer ${className ?? ''}`}>
        <div className="video-replayer__empty">
          <div className="video-replayer__empty-title">No Tracking Data</div>
          <div>Run a CV analysis on this match to generate robot tracks.</div>
        </div>
      </div>
    );
  }

  return (
    <div className={`video-replayer ${className ?? ''}`}>
      {/* viewport: video + canvas overlay */}
      <div className="video-replayer__viewport" ref={viewportRef}>
        {videoUrl ? (
            <video
              ref={videoRef}
              src={videoUrl}
            crossOrigin="anonymous"
            playsInline
            preload="metadata"
          />
        ) : (
          <div
            style={{
              width: '100%',
              aspectRatio: `${data.calibration?.image_width ?? 1280} / ${data.calibration?.image_height ?? 720}`,
              background: '#0a0f14',
            }}
          >
            {/* fallback: hidden video element still needed for time control */}
            <video ref={videoRef} style={{ display: 'none' }} />
          </div>
        )}
        <canvas ref={canvasRef} className="video-replayer__overlay" />
      </div>

      {/* transport controls */}
      <div className="video-replayer__controls">
        <button className="video-replayer__play-btn" onClick={togglePlay} aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? (
            <svg width="18" height="18" viewBox="0 0 18 18"><rect x="4" y="3" width="4" height="12" rx="1" fill="currentColor" /><rect x="10" y="3" width="4" height="12" rx="1" fill="currentColor" /></svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 18 18"><path d="M5 3l10 6-10 6V3z" fill="currentColor" /></svg>
          )}
        </button>

        <input
          type="range"
          className="video-replayer__scrubber"
          min={0}
          max={effectiveDuration || 1}
          step={0.1}
          value={currentTime}
          onChange={handleScrub}
        />

        <span className="video-replayer__time">
          {formatTime(currentTime)} / {formatTime(effectiveDuration)}
        </span>

        <button className="video-replayer__speed-btn" onClick={cycleSpeed}>
          {SPEEDS[speedIdx]}×
        </button>
      </div>

      {!hasPlayableVideo ? (
        <div className="video-replayer__mode-note">
          <span>Showing track timeline only.</span>
          {data.video_url ? (
            <a href={data.video_url} target="_blank" rel="noreferrer">
              Open source video
            </a>
          ) : null}
        </div>
      ) : null}

      {/* team filter chips */}
      {data.team_keys.length > 1 && (
        <div className="video-replayer__team-filters">
          {data.team_keys.map(tk => (
            <button
              key={tk}
              type="button"
              className={`video-replayer__team-chip ${activeTeams.has(tk) ? 'video-replayer__team-chip--active' : ''}`}
              onClick={() => toggleTeam(tk)}
            >
              {teamNumberFromKey(tk)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
