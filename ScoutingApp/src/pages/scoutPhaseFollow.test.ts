import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PHASE_FOLLOW_QUIET_MS, usePhaseCue, usePhaseFollow } from './scoutPhaseFollow';

type Props = { phase: 'Auto' | 'Teleop' | 'Endgame' | 'Complete'; panel: string; running?: boolean };

function setup(initial: Props, lastTap = 0) {
  const onAdvance = vi.fn();
  const lastTapAt = { current: lastTap };
  const hook = renderHook(
    ({ phase, panel, running = true }: Props) => usePhaseFollow({ enabled: true, running, phase, panel, lastTapAt, onAdvance }),
    { initialProps: initial },
  );
  return { ...hook, onAdvance, lastTapAt };
}

describe('usePhaseFollow', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(100_000); });
  afterEach(() => { vi.useRealTimers(); });

  it('moves the board from Auto to Teleop when the clock does', () => {
    const { rerender, onAdvance } = setup({ phase: 'Auto', panel: 'auto' });
    rerender({ phase: 'Teleop', panel: 'auto' });
    expect(onAdvance).toHaveBeenCalledWith('teleop');
  });

  it('waits for a pause after a tap, so the button under a finger does not move', () => {
    const { rerender, onAdvance } = setup({ phase: 'Teleop', panel: 'teleop' }, 100_000 - 200);
    rerender({ phase: 'Endgame', panel: 'teleop' });
    expect(onAdvance).not.toHaveBeenCalled();
    vi.advanceTimersByTime(PHASE_FOLLOW_QUIET_MS);
    expect(onAdvance).toHaveBeenCalledWith('endgame');
  });

  it('leaves a scout alone who went somewhere else (Review, or back to fix Auto)', () => {
    const { rerender, onAdvance } = setup({ phase: 'Auto', panel: 'review' });
    rerender({ phase: 'Teleop', panel: 'review' });
    rerender({ phase: 'Endgame', panel: 'auto' });
    vi.advanceTimersByTime(PHASE_FOLLOW_QUIET_MS * 2);
    expect(onAdvance).not.toHaveBeenCalled();
  });

  it('does nothing while the clock is stopped', () => {
    const { rerender, onAdvance } = setup({ phase: 'Auto', panel: 'auto', running: false });
    rerender({ phase: 'Teleop', panel: 'auto', running: false });
    expect(onAdvance).not.toHaveBeenCalled();
  });
});

describe('usePhaseCue', () => {
  type CueProps = { phase: 'Auto' | 'Teleop' | 'Endgame' | 'Complete'; running: boolean };
  function cue(initial: CueProps) {
    const onCue = vi.fn();
    const hook = renderHook(({ phase, running }: CueProps) => usePhaseCue({ running, phase, onCue }), { initialProps: initial });
    return { ...hook, onCue };
  }

  it('buzzes once when auto ends and once when the endgame starts', () => {
    const { rerender, onCue } = cue({ phase: 'Auto', running: true });
    rerender({ phase: 'Teleop', running: true });
    rerender({ phase: 'Teleop', running: true });
    rerender({ phase: 'Endgame', running: true });
    rerender({ phase: 'Complete', running: true });
    expect(onCue).toHaveBeenCalledTimes(2);
  });

  it('stays quiet for a stopped clock, a reset, or a scout scrubbing backwards', () => {
    const { rerender, onCue } = cue({ phase: 'Auto', running: false });
    rerender({ phase: 'Teleop', running: false });
    rerender({ phase: 'Auto', running: true });
    rerender({ phase: 'Endgame', running: true });
    expect(onCue).not.toHaveBeenCalled();
  });

  it('stays quiet when the clock starts already past auto, as when joining mid-match', () => {
    const { rerender, onCue } = cue({ phase: 'Auto', running: false });
    rerender({ phase: 'Teleop', running: true });
    expect(onCue).not.toHaveBeenCalled();
    rerender({ phase: 'Endgame', running: true });
    expect(onCue).toHaveBeenCalledTimes(1);
  });
});
