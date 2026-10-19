import { useEffect, useRef, type MutableRefObject } from 'react';

type ClockPhase = 'Auto' | 'Teleop' | 'Endgame' | 'Complete';
export type PhasePanel = 'auto' | 'teleop' | 'endgame';

const PANEL_FOR_PHASE: Partial<Record<ClockPhase, PhasePanel>> = {
  Auto: 'auto',
  Teleop: 'teleop',
  Endgame: 'endgame',
};

// A tap this recent means a finger may be on its way to a button; the board waits for a
// pause rather than moving the button out from under it.
export const PHASE_FOLLOW_QUIET_MS = 1500;

// The board's phase tab follows the running match clock: Auto hands over to Teleop, Teleop to
// Endgame. Only from the phase that just ended — a scout who went back to Auto to fix a count,
// or over to Review, stays where they are.
export function usePhaseFollow({
  enabled,
  running,
  phase,
  panel,
  lastTapAt,
  onAdvance,
}: {
  enabled: boolean;
  running: boolean;
  phase: ClockPhase;
  panel: string;
  lastTapAt: MutableRefObject<number>;
  onAdvance: (next: PhasePanel) => void;
}) {
  const previous = useRef(phase);
  const panelRef = useRef(panel);
  const onAdvanceRef = useRef(onAdvance);
  useEffect(() => {
    panelRef.current = panel;
    onAdvanceRef.current = onAdvance;
  });

  useEffect(() => {
    const from = PANEL_FOR_PHASE[previous.current];
    const to = PANEL_FOR_PHASE[phase];
    previous.current = phase;
    if (!enabled || !running || !from || !to || from === to || panelRef.current !== from) return undefined;
    let timer: number | undefined;
    const attempt = () => {
      const wait = lastTapAt.current + PHASE_FOLLOW_QUIET_MS - Date.now();
      if (wait > 0) {
        timer = window.setTimeout(attempt, wait);
        return;
      }
      if (panelRef.current === from) onAdvanceRef.current(to);
    };
    attempt();
    return () => window.clearTimeout(timer);
  }, [enabled, running, phase, lastTapAt]);
}
