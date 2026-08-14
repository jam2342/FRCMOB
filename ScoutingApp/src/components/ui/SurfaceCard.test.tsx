import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SurfaceCard } from './SurfaceCard';

function stubMatchMedia(matches: boolean) {
  const matchMedia = vi.fn().mockImplementation(() => ({
    matches,
    media: '(max-width: 900px)',
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: matchMedia,
  });
}

describe('SurfaceCard collapse contract', () => {
  beforeEach(() => {
    stubMatchMedia(false);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not expose desktop collapse controls for mobile-only cards', () => {
    render(
      <SurfaceCard title="Mobile Only Card" mobileCollapsible>
        <div>Body</div>
      </SurfaceCard>,
    );

    expect(screen.queryByRole('button', { name: /minimize mobile only card block/i })).not.toBeInTheDocument();
  });

  it('keeps desktop collapse explicit when collapsible is enabled', () => {
    render(
      <SurfaceCard title="Desktop Card" collapsible mobileCollapsible={false}>
        <div>Body</div>
      </SurfaceCard>,
    );

    const collapseButton = screen.getByRole('button', { name: /minimize desktop card block/i });
    expect(collapseButton).toBeInTheDocument();

    fireEvent.click(collapseButton);

    expect(screen.getByRole('button', { name: /expand desktop card block/i })).toBeInTheDocument();
  });
});
