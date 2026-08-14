import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PageViewBar } from './PageViewBar';
import { EVENTS_VIEWS, SCOUTING_VIEWS } from './pageViewBarConfig';

type MatchMediaListener = (event: MediaQueryListEvent) => void;

function stubMatchMedia(matches: boolean) {
  let currentMatches = matches;
  let mediaText = '';
  const listeners = new Set<MatchMediaListener>();
  const mediaQueryList = {
    get matches() {
      return currentMatches;
    },
    get media() {
      return mediaText;
    },
    onchange: null,
    addEventListener: vi.fn((event: string, listener: MatchMediaListener) => {
      if (event === 'change') listeners.add(listener);
    }),
    removeEventListener: vi.fn((event: string, listener: MatchMediaListener) => {
      if (event === 'change') listeners.delete(listener);
    }),
    addListener: vi.fn((listener: MatchMediaListener) => {
      listeners.add(listener);
    }),
    removeListener: vi.fn((listener: MatchMediaListener) => {
      listeners.delete(listener);
    }),
    dispatchEvent: vi.fn(),
  };

  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation((media: string) => {
      mediaText = media;
      return mediaQueryList;
    }),
  });

  return {
    mediaQueryList,
    setMatches(nextMatches: boolean) {
      currentMatches = nextMatches;
      const event = { matches: nextMatches, media: mediaText } as MediaQueryListEvent;
      listeners.forEach((listener) => listener(event));
    },
  };
}

describe('PageViewBar', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    // Remove the matchMedia stub so other tests see the jsdom default (desktop).
    Reflect.deleteProperty(window, 'matchMedia');
  });

  it('preserves scouting query context across scouting sub-tabs', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/scouting?event=2026week0&match=2026week0_qm1&team=frc118&my_team=frc148']}>
        <Routes>
          <Route path="/scouting" element={<PageViewBar items={SCOUTING_VIEWS} />} />
        </Routes>
      </MemoryRouter>,
    );

    const links = [...container.querySelectorAll('a')];
    const assignmentsLink = links.find((link) => link.textContent === 'Assignments');
    const autoPathsLink = links.find((link) => link.textContent === 'Auto Paths');

    expect(assignmentsLink).toHaveAttribute(
      'href',
      '/scouting/assignments?event=2026week0&match=2026week0_qm1&team=frc118&my_team=frc148',
    );
    expect(autoPathsLink).toHaveAttribute(
      'href',
      '/scouting/auto-paths?event=2026week0&match=2026week0_qm1&team=frc118&my_team=frc148',
    );
  });

  it('does not preserve search by default for non-scouting view bars', () => {
    render(
      <MemoryRouter initialEntries={['/events?event=2026week0&tab=teams']}>
        <Routes>
          <Route path="/events" element={<PageViewBar items={EVENTS_VIEWS} />} />
        </Routes>
      </MemoryRouter>,
    );

    const exportLink = screen.getByRole('link', { name: 'Export' });
    expect(exportLink).toHaveAttribute('href', '/events/export');
  });

  it('collapses to a single dropdown pill on mobile and reveals tabs on open', () => {
    stubMatchMedia(true);
    render(
      <MemoryRouter initialEntries={['/scouting/pit?event=2026week0&team=frc118']}>
        <Routes>
          <Route
            path="/scouting/pit"
            element={<PageViewBar items={SCOUTING_VIEWS} collapseToMenuOnMobile />}
          />
        </Routes>
      </MemoryRouter>,
    );

    // Only the trigger shows up-front, labelled with the active sub-view — no
    // second full-width row of tabs.
    const trigger = screen.getByRole('button', { name: 'Switch scouting tool' });
    expect(trigger).toHaveTextContent('Pit Scouting');
    expect(screen.queryByRole('menu')).toBeNull();
    expect(screen.queryByRole('link', { name: 'Auto Paths' })).toBeNull();

    // Opening the pill reveals every sub-view, with scouting query context kept.
    fireEvent.click(trigger);
    expect(screen.getByRole('menu')).toBeInTheDocument();
    const autoPaths = screen.getByRole('menuitem', { name: 'Auto Paths' });
    expect(autoPaths).toHaveAttribute('href', '/scouting/auto-paths?event=2026week0&team=frc118');
  });

  it('positions the portaled dropdown under the trigger and tracks viewport movement', () => {
    stubMatchMedia(true);
    render(
      <MemoryRouter initialEntries={['/scouting/pit?event=2026week0&team=frc118']}>
        <Routes>
          <Route
            path="/scouting/pit"
            element={<PageViewBar items={SCOUTING_VIEWS} collapseToMenuOnMobile />}
          />
        </Routes>
      </MemoryRouter>,
    );

    const trigger = screen.getByRole('button', { name: 'Switch scouting tool' });
    let triggerRect = {
      bottom: 52,
      height: 36,
      left: 32,
      right: 212,
      top: 16,
      width: 180,
      x: 32,
      y: 16,
      toJSON: () => ({}),
    } as DOMRect;
    vi.spyOn(trigger, 'getBoundingClientRect').mockImplementation(() => triggerRect);

    fireEvent.click(trigger);
    const menu = screen.getByRole('menu');
    expect(menu).toHaveStyle({ top: '58px', left: '32px', minWidth: '180px' });

    triggerRect = {
      ...triggerRect,
      bottom: 74,
      left: 44,
      right: 244,
      top: 38,
      width: 200,
      x: 44,
      y: 38,
    } as DOMRect;
    fireEvent.scroll(window);
    expect(menu).toHaveStyle({ top: '80px', left: '44px', minWidth: '200px' });
  });

  it('keeps the full bar on desktop even when collapseToMenuOnMobile is set', () => {
    stubMatchMedia(false);
    render(
      <MemoryRouter initialEntries={['/scouting']}>
        <Routes>
          <Route path="/scouting" element={<PageViewBar items={SCOUTING_VIEWS} collapseToMenuOnMobile />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.queryByRole('button', { name: 'Switch scouting tool' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Auto Paths' })).toBeInTheDocument();
  });

  it('closes an open mobile dropdown when switching to desktop and does not reopen it on return', () => {
    const media = stubMatchMedia(true);
    const removeDocumentListener = vi.spyOn(document, 'removeEventListener');
    const removeWindowListener = vi.spyOn(window, 'removeEventListener');
    render(
      <MemoryRouter initialEntries={['/scouting/pit']}>
        <Routes>
          <Route
            path="/scouting/pit"
            element={<PageViewBar items={SCOUTING_VIEWS} collapseToMenuOnMobile />}
          />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Switch scouting tool' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    removeDocumentListener.mockClear();
    removeWindowListener.mockClear();

    act(() => media.setMatches(false));
    expect(screen.queryByRole('menu')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Switch scouting tool' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Auto Paths' })).toBeInTheDocument();
    expect(removeDocumentListener).toHaveBeenCalledWith('pointerdown', expect.any(Function));
    expect(removeWindowListener).toHaveBeenCalledWith('keydown', expect.any(Function));

    act(() => media.setMatches(true));
    const trigger = screen.getByRole('button', { name: 'Switch scouting tool' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('removes desktop bar listeners when switching into the mobile dropdown mode', () => {
    const media = stubMatchMedia(false);
    const removeListener = vi.spyOn(EventTarget.prototype, 'removeEventListener');
    const removeWindowListener = vi.spyOn(window, 'removeEventListener');
    render(
      <MemoryRouter initialEntries={['/scouting']}>
        <Routes>
          <Route path="/scouting" element={<PageViewBar items={SCOUTING_VIEWS} collapseToMenuOnMobile />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole('navigation', { name: 'Page view' })).toBeInTheDocument();
    removeListener.mockClear();
    removeWindowListener.mockClear();

    act(() => media.setMatches(true));
    expect(screen.queryByRole('navigation', { name: 'Page view' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Switch scouting tool' })).toBeInTheDocument();
    expect(removeListener).toHaveBeenCalledWith('scroll', expect.any(Function));
    expect(removeWindowListener).toHaveBeenCalledWith('resize', expect.any(Function));
  });
});
