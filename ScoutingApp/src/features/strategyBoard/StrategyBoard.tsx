import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { getStrategyBoard, saveStrategyBoard, type SaveStrategyBoardResult, type StrategyStroke } from '../../api';
import { Button, FieldRadioGroup } from '../../components/ui/primitives';
import { showToast } from '../../components/ui/toast';
import { SEASON } from '../../config/season';
import { useCanvasTokens } from '../../hooks/useCanvasTokens';
import { drawStroke, extendStroke, fraction, keepStroke, MAX_STROKES, startStroke, type StrokeColor, type Tool } from './board';
import styles from './StrategyBoard.module.css';

const TOKENS = [
  '--field-canvas-bg',
  '--field-canvas-grid',
  '--field-canvas-line',
  '--field-canvas-label',
  '--color-alliance-red',
  '--color-alliance-blue',
  '--field-canvas-note',
] as const;

// Teammates' changes show up this often while the board is open and idle.
const REFRESH_MS = 15_000;
// A burst of strokes saves once; a failed save tries again after a pause.
const SAVE_AFTER_MS = 700;
const RETRY_AFTER_MS = 5000;

type Props = { matchKey: string; ourAlliance: 'red' | 'blue' };
type Status = 'loading' | 'saved' | 'saving' | 'error';

// The match's plan, drawn on the field and shared with the team: paths, arrows and zones in
// our colour, theirs, or a note colour. Our drivers are on the left. Saved as field fractions,
// so a phone and a laptop draw the same plan.
export function StrategyBoard({ matchKey, ourAlliance }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const tokens = useCanvasTokens(TOKENS);
  const [width, setWidth] = useState(0);
  const [strokes, setStrokes] = useState<StrategyStroke[]>([]);
  const [current, setCurrent] = useState<StrategyStroke | null>(null);
  const [tool, setTool] = useState<Tool>('pen');
  const [color, setColor] = useState<StrokeColor>('ours');
  const [status, setStatus] = useState<Status>('loading');
  const [updatedBy, setUpdatedBy] = useState<string | null>(null);
  const version = useRef(0);
  const dirty = useRef(false);
  const saveTimer = useRef<number | null>(null);
  // Strokes waiting to be saved: what a retry sends, and what leaving the page flushes.
  const pending = useRef<StrategyStroke[] | null>(null);
  // One save at a time: a second sent before the first answers would carry a stale version.
  const inFlight = useRef(false);
  // The save on its way, what it carries, and what it resolves to (null if it failed).
  const flight = useRef<Promise<SaveStrategyBoardResult | null> | null>(null);
  const sendingRef = useRef<StrategyStroke[] | null>(null);
  // Bumped when the board closes or changes match: a request from before leaves this one alone.
  const generation = useRef(0);
  // The finger drawing; a second finger or a resting palm doesn't join its stroke.
  const drawingPointer = useRef<number | null>(null);
  const height = width * (SEASON.fieldWidthM / SEASON.fieldLengthM);

  const adopt = useCallback((board: { strokes: StrategyStroke[]; version: number; updated_by: string | null }) => {
    version.current = board.version;
    setStrokes(board.strokes);
    setUpdatedBy(board.updated_by);
  }, []);

  useEffect(() => {
    let live = true;
    setStatus('loading');
    getStrategyBoard(matchKey)
      .then((board) => { if (live) { adopt(board); setStatus('saved'); } })
      .catch(() => { if (live) setStatus('error'); });
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible' || dirty.current) return;
      getStrategyBoard(matchKey)
        .then((board) => { if (live && !dirty.current && board.version > version.current) adopt(board); })
        .catch(() => undefined);
    }, REFRESH_MS);
    return () => { live = false; window.clearInterval(timer); };
  }, [matchKey, adopt]);

  // Leaving with a stroke not yet saved sends it on the way out, after any save already going,
  // with the version that save comes back with.
  useEffect(() => () => {
    generation.current += 1;
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = null;
    const leftover = pending.current;
    pending.current = null;
    dirty.current = false;
    if (!leftover) return;
    const sentBefore = sendingRef.current;
    const lastVersion = version.current;
    const prior = inFlight.current && flight.current ? flight.current : Promise.resolve(null);
    void prior
      .then((result) => {
        // A teammate's plan stands; strokes already on their way don't need sending twice.
        if (result?.conflict || (result && sentBefore === leftover)) return undefined;
        return saveStrategyBoard(matchKey, result ? result.board.version : lastVersion, leftover);
      })
      .catch(() => undefined);
  }, [matchKey]);

  // `retry` keeps the "couldn't reach the server" line up while it waits to try again.
  const save = useCallback((next: StrategyStroke[], delay = SAVE_AFTER_MS, retry = false) => {
    dirty.current = true;
    pending.current = next;
    if (!retry) setStatus('saving');
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = null;
      if (inFlight.current) {
        saveRef.current(pending.current ?? next);
        return;
      }
      const sending = pending.current ?? next;
      const gen = generation.current;
      inFlight.current = true;
      sendingRef.current = sending;
      const request = saveStrategyBoard(matchKey, version.current, sending)
        .finally(() => { if (gen === generation.current) inFlight.current = false; });
      flight.current = request.then((result) => result, () => null);
      request
        .then((result) => {
          // Closed or moved to another match meanwhile: the way-out flush handles what's left.
          if (gen !== generation.current) return;
          if (result.conflict) {
            // A teammate saved first: theirs stands, and nothing queued here overwrites it.
            if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
            saveTimer.current = null;
            pending.current = null;
            dirty.current = false;
            adopt(result.board);
            setStatus('saved');
            showToast({ message: 'A teammate changed this plan. You’re seeing theirs now.', tone: 'info' });
            return;
          }
          version.current = result.board.version;
          // Drawn on while this was saving: the newer strokes are queued and save next.
          if (pending.current !== sending) return;
          pending.current = null;
          dirty.current = false;
          adopt(result.board);
          setStatus('saved');
        })
        .catch(() => {
          if (gen !== generation.current) return;
          setStatus('error');
          if (pending.current) saveRef.current(pending.current, RETRY_AFTER_MS, true);
        });
    }, delay);
  }, [matchKey, adopt]);
  const saveRef = useRef(save);
  useEffect(() => { saveRef.current = save; }, [save]);

  const commit = (next: StrategyStroke[]) => {
    setStrokes(next);
    save(next);
  };

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return undefined;
    const measure = () => setWidth(wrap.clientWidth);
    measure();
    if (typeof ResizeObserver !== 'function') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || !width) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = tokens['--field-canvas-bg'];
    ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = tokens['--field-canvas-grid'];
    ctx.lineWidth = 1;
    for (let m = 1; m < SEASON.fieldLengthM; m += 1) {
      const x = (m / SEASON.fieldLengthM) * width;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
    for (let m = 1; m < SEASON.fieldWidthM; m += 1) {
      const y = (m / SEASON.fieldWidthM) * height;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    ctx.strokeStyle = tokens['--field-canvas-line'];
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, width - 2, height - 2);
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(width / 2, 0);
    ctx.lineTo(width / 2, height);
    ctx.stroke();
    ctx.setLineDash([]);
    // Driver stations: ours on the left, theirs on the right.
    const ours = ourAlliance === 'red' ? tokens['--color-alliance-red'] : tokens['--color-alliance-blue'];
    const theirs = ourAlliance === 'red' ? tokens['--color-alliance-blue'] : tokens['--color-alliance-red'];
    const strip = Math.max(4, width / 120);
    ctx.fillStyle = ours;
    ctx.fillRect(0, 0, strip, height);
    ctx.fillStyle = theirs;
    ctx.fillRect(width - strip, 0, strip, height);
    const colourOf = (value: StrokeColor) => (value === 'ours' ? ours : value === 'theirs' ? theirs : tokens['--field-canvas-note']);
    for (const stroke of current ? [...strokes, current] : strokes) drawStroke(ctx, stroke, width, height, colourOf(stroke.color));
  }, [current, height, ourAlliance, strokes, tokens, width]);

  const pointAt = (event: ReactPointerEvent<HTMLCanvasElement>): [number, number] => {
    const rect = event.currentTarget.getBoundingClientRect();
    return [fraction(event.clientX - rect.left, rect.width), fraction(event.clientY - rect.top, rect.height)];
  };

  const full = strokes.length >= MAX_STROKES;
  const statusText = status === 'loading'
    ? 'Loading the plan…'
    : status === 'saving'
      ? 'Saving…'
      : status === 'error'
        ? 'Couldn’t reach the server. Your drawing is kept here until it can save.'
        : updatedBy
          ? `Shared with your team · last saved by ${updatedBy}`
          : 'Shared with your team. Draw to start the plan.';

  return (
    <div className={styles.board}>
      <div className={styles.tools}>
        <FieldRadioGroup
          legend="Draw"
          name={`board-tool-${matchKey}`}
          segmented
          value={tool}
          onValueChange={(value) => setTool(value as Tool)}
          options={[{ value: 'pen', label: 'Path' }, { value: 'arrow', label: 'Arrow' }, { value: 'zone', label: 'Zone' }]}
        />
        <FieldRadioGroup
          legend="Colour"
          name={`board-colour-${matchKey}`}
          segmented
          value={color}
          onValueChange={(value) => setColor(value as StrokeColor)}
          options={[{ value: 'ours', label: 'Us' }, { value: 'theirs', label: 'Them' }, { value: 'note', label: 'Note' }]}
        />
      </div>
      <div ref={wrapRef} className={styles.field}>
        <canvas
          ref={canvasRef}
          className={styles.canvas}
          style={{ height: height || undefined }}
          role="img"
          aria-label={`Field plan for this match: ${strokes.length} drawing${strokes.length === 1 ? '' : 's'}. Our drivers on the left.`}
          onPointerDown={(event) => {
            if (full || status === 'loading' || drawingPointer.current !== null) return;
            drawingPointer.current = event.pointerId;
            event.currentTarget.setPointerCapture?.(event.pointerId);
            setCurrent(startStroke(tool, color, pointAt(event)));
          }}
          onPointerMove={(event) => {
            if (!current || event.pointerId !== drawingPointer.current) return;
            setCurrent(extendStroke(current, pointAt(event)));
          }}
          onPointerUp={(event) => {
            if (event.pointerId !== drawingPointer.current) return;
            drawingPointer.current = null;
            if (!current) return;
            const done = current;
            setCurrent(null);
            if (keepStroke(done)) commit([...strokes, done]);
          }}
          onPointerCancel={(event) => {
            if (event.pointerId !== drawingPointer.current) return;
            drawingPointer.current = null;
            setCurrent(null);
          }}
        />
      </div>
      <div className={styles.footer}>
        <p className={styles.status} role="status">{full ? 'This plan is full. Undo or clear to draw more.' : statusText}</p>
        <span className={styles.actions}>
          <Button size="sm" variant="quiet" disabled={!strokes.length} onClick={() => commit(strokes.slice(0, -1))}>Undo</Button>
          <Button
            size="sm"
            variant="quiet"
            disabled={!strokes.length}
            onClick={() => {
              if (window.confirm('Clear the whole plan for everyone on your team?')) commit([]);
            }}
          >
            Clear
          </Button>
        </span>
      </div>
    </div>
  );
}
