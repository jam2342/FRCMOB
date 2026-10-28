import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getStrategyBoard, saveStrategyBoard, type StrategyBoard as Board } from '../../api';
import { StrategyBoard } from './StrategyBoard';

vi.mock('../../api', () => ({ getStrategyBoard: vi.fn(), saveStrategyBoard: vi.fn() }));
const toast = vi.hoisted(() => ({ showToast: vi.fn() }));
vi.mock('../../components/ui/toast', () => toast);

const empty: Board = { match_key: '2026mndu_qm12', strokes: [], version: 0, updated_by: null, updated_at: null };

function canvas() {
  const el = screen.getByRole('img', { name: /Field plan/ }) as HTMLCanvasElement;
  el.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 400, right: 800, bottom: 400, x: 0, y: 0, toJSON: () => ({}) });
  return el;
}

function drag(el: HTMLElement, from: [number, number], to: [number, number]) {
  fireEvent.pointerDown(el, { clientX: from[0], clientY: from[1], pointerId: 1 });
  fireEvent.pointerMove(el, { clientX: to[0], clientY: to[1], pointerId: 1 });
  fireEvent.pointerUp(el, { clientX: to[0], clientY: to[1], pointerId: 1 });
}

async function settle(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

beforeEach(() => {
  vi.useFakeTimers();
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as never;
});
afterEach(() => {
  vi.useRealTimers();
  vi.mocked(getStrategyBoard).mockReset();
  vi.mocked(saveStrategyBoard).mockReset();
  toast.showToast.mockReset();
});

describe('StrategyBoard', () => {
  it('draws an arrow and saves it once, as field fractions', async () => {
    vi.mocked(getStrategyBoard).mockResolvedValue(empty);
    vi.mocked(saveStrategyBoard).mockImplementation(async (_key, _version, strokes) => ({ ok: true, board: { ...empty, strokes, version: 1, updated_by: 'Ava' } }));
    render(<StrategyBoard matchKey="2026mndu_qm12" ourAlliance="red" />);
    await settle();
    expect(screen.getByRole('status')).toHaveTextContent('Draw to start the plan');
    fireEvent.click(screen.getByRole('radio', { name: 'Arrow' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Them' }));
    drag(canvas(), [80, 200], [400, 100]);
    expect(screen.getByRole('status')).toHaveTextContent('Saving');
    await settle(800);
    expect(saveStrategyBoard).toHaveBeenCalledTimes(1);
    expect(saveStrategyBoard).toHaveBeenCalledWith('2026mndu_qm12', 0, [{ tool: 'arrow', color: 'theirs', points: [[0.1, 0.5], [0.5, 0.25]] }]);
    expect(screen.getByRole('status')).toHaveTextContent('last saved by Ava');
    expect(canvas()).toHaveAccessibleName(/1 drawing\./);
  });

  it("a tap isn't a drawing", async () => {
    vi.mocked(getStrategyBoard).mockResolvedValue(empty);
    render(<StrategyBoard matchKey="2026mndu_qm12" ourAlliance="blue" />);
    await settle();
    fireEvent.click(screen.getByRole('radio', { name: 'Zone' }));
    drag(canvas(), [100, 100], [101, 101]);
    await settle(800);
    expect(saveStrategyBoard).not.toHaveBeenCalled();
  });

  it("shows a teammate's plan when they saved first, and says so", async () => {
    vi.mocked(getStrategyBoard).mockResolvedValue({ ...empty, version: 3, strokes: [{ tool: 'pen', color: 'ours', points: [[0.2, 0.2]] }] });
    const theirs: Board = { ...empty, version: 4, updated_by: 'Ben', strokes: [{ tool: 'zone', color: 'note', points: [[0.1, 0.1], [0.4, 0.4]] }] };
    vi.mocked(saveStrategyBoard).mockResolvedValue({ ok: false, conflict: true, board: theirs });
    render(<StrategyBoard matchKey="2026mndu_qm12" ourAlliance="red" />);
    await settle();
    drag(canvas(), [400, 200], [500, 250]);
    await settle(800);
    expect(saveStrategyBoard).toHaveBeenCalledWith('2026mndu_qm12', 3, expect.any(Array));
    expect(toast.showToast).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringMatching(/teammate changed this plan/) }));
    expect(screen.getByRole('status')).toHaveTextContent('last saved by Ben');
  });

  it('undo takes the last drawing back off, and a failed save tries again', async () => {
    vi.mocked(getStrategyBoard).mockResolvedValue({ ...empty, version: 1, strokes: [{ tool: 'pen', color: 'ours', points: [[0.2, 0.2]] }] });
    vi.mocked(saveStrategyBoard)
      .mockRejectedValueOnce(new Error('offline'))
      .mockImplementation(async (_key, _version, strokes) => ({ ok: true, board: { ...empty, strokes, version: 2, updated_by: 'Ava' } }));
    render(<StrategyBoard matchKey="2026mndu_qm12" ourAlliance="red" />);
    await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await settle(800);
    expect(screen.getByRole('status')).toHaveTextContent('Couldn’t reach the server');
    await settle(5800);
    expect(saveStrategyBoard).toHaveBeenCalledTimes(2);
    expect(saveStrategyBoard).toHaveBeenLastCalledWith('2026mndu_qm12', 1, []);
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled();
  });

  it('closing mid-save sends the newer strokes after it, with the version it comes back with', async () => {
    vi.mocked(getStrategyBoard).mockResolvedValue(empty);
    let answer: (value: Awaited<ReturnType<typeof saveStrategyBoard>>) => void = () => undefined;
    vi.mocked(saveStrategyBoard)
      .mockImplementationOnce((_key, _version, strokes) => new Promise((resolve) => {
        answer = () => resolve({ ok: true, board: { ...empty, strokes, version: 1, updated_by: 'Ava' } });
      }))
      .mockImplementation(async (_key, version, strokes) => ({ ok: true, board: { ...empty, strokes, version: version + 1, updated_by: 'Ava' } }));
    const view = render(<StrategyBoard matchKey="2026mndu_qm12" ourAlliance="red" />);
    await settle();
    drag(canvas(), [80, 200], [400, 100]);
    await settle(800); // the first stroke's save is on its way
    drag(canvas(), [100, 100], [300, 300]);
    view.unmount();
    await act(async () => { answer({ ok: true, board: empty }); await vi.runAllTimersAsync(); });
    const calls = vi.mocked(saveStrategyBoard).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[1][1]).toBe(1);
    expect(calls[1][2]).toHaveLength(2);
    expect(toast.showToast).not.toHaveBeenCalled();
  });

  it("a second finger doesn't join the stroke being drawn", async () => {
    vi.mocked(getStrategyBoard).mockResolvedValue(empty);
    vi.mocked(saveStrategyBoard).mockImplementation(async (_key, _version, strokes) => ({ ok: true, board: { ...empty, strokes, version: 1, updated_by: 'Ava' } }));
    render(<StrategyBoard matchKey="2026mndu_qm12" ourAlliance="red" />);
    await settle();
    const el = canvas();
    fireEvent.pointerDown(el, { clientX: 80, clientY: 200, pointerId: 1 });
    fireEvent.pointerDown(el, { clientX: 700, clientY: 300, pointerId: 2 });
    fireEvent.pointerMove(el, { clientX: 760, clientY: 380, pointerId: 2 });
    fireEvent.pointerMove(el, { clientX: 400, clientY: 100, pointerId: 1 });
    fireEvent.pointerUp(el, { clientX: 760, clientY: 380, pointerId: 2 });
    fireEvent.pointerUp(el, { clientX: 400, clientY: 100, pointerId: 1 });
    await settle(800);
    const [, , strokes] = vi.mocked(saveStrategyBoard).mock.calls[0];
    expect(strokes).toHaveLength(1);
    expect(strokes[0].points.every(([x]) => x <= 0.5)).toBe(true);
  });
});
