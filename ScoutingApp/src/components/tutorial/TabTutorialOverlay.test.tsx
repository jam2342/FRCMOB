import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { TabTutorialOverlay } from './TabTutorialOverlay';
import { hasSeenTutorial, markTutorialSeen } from '../../tutorial/tutorialState';

vi.mock('./TutorialRobotMascot', () => ({ TutorialRobotMascot: () => null }));
beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });

// jsdom lays nothing out, so a panel "on screen" has to report a size.
function shown(tour: string) {
  return (
    <div
      data-tour={tour}
      ref={(el) => {
        if (el) el.getBoundingClientRect = () => ({ width: 300, height: 120, top: 100, left: 20, right: 320, bottom: 220, x: 20, y: 100, toJSON: () => ({}) });
      }}
    />
  );
}

it('marks only the current page seen when skipping its tutorial', () => {
  markTutorialSeen('favorites');
  const onClose = vi.fn();
  render(<TabTutorialOverlay scope="home" onClose={onClose} />);
  fireEvent.click(screen.getByRole('button', { name: "I've already seen this" }));
  expect(onClose).toHaveBeenCalledOnce();
  expect(hasSeenTutorial('home')).toBe(true);
  expect(hasSeenTutorial('favorites')).toBe(true);
  expect(hasSeenTutorial('settings')).toBe(false);
  expect(hasSeenTutorial('compare')).toBe(false);
});

it('leaves out steps whose panel is not on screen', () => {
  render(<TabTutorialOverlay scope="settings" onClose={vi.fn()} />);
  // Appearance and Advanced; alerts and the operator panels aren't rendered here.
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '2');
  fireEvent.click(screen.getByRole('button', { name: 'Go to step 2' }));
  expect(screen.getByRole('heading', { name: 'Everything else' })).toBeInTheDocument();
});

it('includes operator steps when their Settings panels are shown', () => {
  render(<>
    {shown('settings-alerts')}
    {shown('settings-operator')}
    {shown('settings-ops')}
    <TabTutorialOverlay scope="settings" onClose={vi.fn()} />
  </>);
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '5');
});

it('shows phone-only steps on a phone and desktop-only steps on a desktop', () => {
  const width = window.innerWidth;
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
  render(<TabTutorialOverlay scope="home" onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Go to step 3' }));
  expect(screen.getByText('Event info opens the rankings and the live stream for the event you picked.')).toBeInTheDocument();
  cleanup();
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1366 });
  render(<TabTutorialOverlay scope="home" onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Go to step 3' }));
  expect(screen.getByText(/The cards beside the matches show the rankings/)).toBeInTheDocument();
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
});
