import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProductShell } from './ProductShell';

vi.mock('../api', () => ({
  searchTeams: vi.fn(async () => ({ teams: [] })),
}));

vi.mock('../hooks/useOnlineStatus', () => ({
  useOnlineStatus: () => ({ online: true, queueSize: 0 }),
}));

vi.mock('../hooks/usePwaInstall', () => ({
  usePwaInstall: () => ({
    canPrompt: false,
    promptInstall: vi.fn(),
    dismiss: vi.fn(),
  }),
}));

vi.mock('../hooks/usePrefetchRoutes', () => ({
  usePrefetchRoutes: () => undefined,
}));

vi.mock('./useShellSettingsState', () => ({
  useShellSettingsState: () => ({
    jumpMode: 'auto',
    jumpRegion: 'all',
    densityMode: 'comfortable',
    themeMode: 'dark',
    tutorialAutoplay: false,
    setJumpMode: vi.fn(),
    setJumpRegion: vi.fn(),
  }),
}));

vi.mock('../components/navigation/BottomTabBar', () => ({
  BottomTabBar: () => null,
}));

vi.mock('../components/navigation/MobileSearchOverlay', () => ({
  MobileSearchOverlay: () => null,
}));

vi.mock('../components/navigation/MoreSheet', () => ({
  MoreSheet: () => null,
}));

vi.mock('../components/ui/OfflineIndicator', () => ({
  OfflineIndicator: () => null,
}));

vi.mock('../components/tutorial/TabTutorialOverlay', () => ({
  TabTutorialOverlay: () => null,
}));

vi.mock('../tutorial/tutorialBlueprints', () => ({
  getTutorialBlueprint: () => ({ checklist: [] }),
}));

vi.mock('../tutorial/tutorialState', () => ({
  countCompletedChecklistItems: () => 0,
  hasSeenTutorial: () => true,
  markTutorialSeen: vi.fn(),
  SCOUTING_TUTORIAL_PROGRESS_EVENT: 'tutorial-progress',
}));

vi.mock('./appUx', () => ({
  resolveScopeFromPath: () => 'events',
}));

function FinderPage() {
  return (
    <div className="center-layout">
      <aside className="center-sidebar">
        <div className="finder-sidebar-header">Summary Header</div>
        <section className="surface-card">
          <header className="surface-card-head">
            <div>
              <h3>Event Finder</h3>
              <p>Select an event</p>
            </div>
          </header>
          <div className="surface-card-body">Filters</div>
        </section>
      </aside>
      <section className="center-main">
        <div>Main board</div>
      </section>
    </div>
  );
}

function renderShell() {
  return render(
    <MemoryRouter initialEntries={['/events']}>
      <Routes>
        <Route element={<ProductShell />}>
          <Route path="/events" element={<FinderPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('ProductShell finder collapse', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      writable: true,
      value: 1440,
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
    window.localStorage.clear();
  });

  it('collapses only from the explicit finder toggle', () => {
    renderShell();

    const root = document.querySelector('.product-shell');
    expect(root).not.toHaveClass('finder-collapsed');

    fireEvent.click(screen.getByText('Event Finder'));
    expect(root).not.toHaveClass('finder-collapsed');

    fireEvent.click(screen.getByRole('button', { name: /collapse finder/i }));
    expect(root).toHaveClass('finder-collapsed');
  });
});
