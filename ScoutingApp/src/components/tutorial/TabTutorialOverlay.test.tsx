import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { TabTutorialOverlay } from './TabTutorialOverlay';
import { hasSeenTutorial, markTutorialSeen } from '../../tutorial/tutorialState';

vi.mock('./TutorialRobotMascot', () => ({ TutorialRobotMascot: () => null }));
beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });

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

it('omits Settings operator steps when their panels are absent', () => {
  render(<TabTutorialOverlay scope="settings" onClose={vi.fn()} />);
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '3');
  fireEvent.click(screen.getByRole('button', { name: 'Go to step 3' }));
  expect(screen.getByRole('heading', { name: 'Use diagnostics + maintenance for controlled cleanup' })).toBeInTheDocument();
});

it('includes operator steps when their Settings panels are rendered', () => {
  render(<>
    <div className="settings-runtime-card" />
    <div className="settings-ops-card" />
    <TabTutorialOverlay scope="settings" onClose={vi.fn()} />
  </>);
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '5');
});
