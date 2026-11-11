import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { TabTutorialOverlay } from './TabTutorialOverlay';
import { hasSeenTutorial } from '../../tutorial/tutorialState';

vi.mock('../ui/RobotMascot', () => ({ RobotMascot: () => null }));
beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); localStorage.clear(); vi.useRealTimers(); });

// Timers fire outside React's batching, so each advance runs inside act.
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

const added: HTMLElement[] = [];
afterEach(() => { added.splice(0).forEach((el) => el.remove()); });

// A target that arrives late, like a card that renders once its data loads.
function target(tour: string) {
  const el = document.createElement('div');
  el.setAttribute('data-tour', tour);
  el.scrollIntoView = vi.fn();
  el.getBoundingClientRect = () => ({ width: 300, height: 120, top: 100, left: 20, right: 320, bottom: 220, x: 20, y: 100, toJSON: () => ({}) });
  document.body.appendChild(el);
  added.push(el);
  return el;
}

it('draws nothing and leaves the tour unseen while no target exists, then gives up quietly', async () => {
  vi.useFakeTimers();
  const onClose = vi.fn();
  render(<TabTutorialOverlay scope="my-team" onClose={onClose} />);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.body.textContent).not.toMatch(/\d+\s*\/\s*0|No steps/);
  await tick(4900);
  expect(onClose).not.toHaveBeenCalled();
  await tick(200);
  expect(onClose).toHaveBeenCalledOnce();
  expect(hasSeenTutorial('my-team')).toBe(false);
});

it('appears with the right count once its targets show up, and only grows after that', async () => {
  vi.useFakeTimers();
  const onClose = vi.fn();
  render(<TabTutorialOverlay scope="my-team" onClose={onClose} />);
  expect(screen.queryByRole('dialog')).toBeNull();
  await tick(1500);
  const first = [target('myteam-intro'), target('myteam-join')];
  await tick(300);
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '2');
  expect(hasSeenTutorial('my-team')).toBe(true);
  expect(document.body.textContent).not.toMatch(/\d+\s*\/\s*0/);
  target('myteam-members');
  await tick(300);
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '3');
  first.forEach((el) => el.remove());
  await tick(300);
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '3');
  await tick(10000);
  expect(onClose).not.toHaveBeenCalled();
});

it('stops waiting when it unmounts', async () => {
  vi.useFakeTimers();
  const onClose = vi.fn();
  render(<TabTutorialOverlay scope="my-team" onClose={onClose} />).unmount();
  await tick(10000);
  expect(onClose).not.toHaveBeenCalled();
});
