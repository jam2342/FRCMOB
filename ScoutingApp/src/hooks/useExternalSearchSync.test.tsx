import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { useEffect, useState } from 'react';
import { MemoryRouter, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { useExternalSearchSync } from './useExternalSearchSync';

const seenSearches: string[] = [];

// The shape every center page has: state seeded from the URL, a writer that
// mirrors state back into it, and links that change the URL underneath.
function Page() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState(searchParams.get('tab') || 'overview');
  const navigate = useNavigate();
  const location = useLocation();

  const sync = useExternalSearchSync(searchParams, (params) => {
    setTab(params.get('tab') || 'overview');
  });

  useEffect(() => {
    if (!sync.shouldWrite()) return;
    const next = new URLSearchParams();
    next.set('tab', tab);
    if (next.toString() !== searchParams.toString()) {
      sync.markWritten(next.toString());
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams, sync, tab]);

  useEffect(() => {
    seenSearches.push(location.search);
  }, [location.search, tab]);

  return (
    <div>
      <p data-testid="tab">{tab}</p>
      <p data-testid="search">{location.search}</p>
      <button type="button" onClick={() => setTab('events')}>click events tab</button>
      <button type="button" onClick={() => navigate('/?tab=performance')}>link to performance</button>
    </div>
  );
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/?tab=overview']}>
      <Page />
    </MemoryRouter>,
  );
}

describe('useExternalSearchSync', () => {
  it('keeps the writer controls stable across unrelated renders and URL updates', () => {
    const { result, rerender } = renderHook(
      ({ search }) => useExternalSearchSync(new URLSearchParams(search), () => undefined),
      { initialProps: { search: 'tab=overview' } },
    );
    const initial = result.current;
    rerender({ search: 'tab=overview' });
    expect(result.current).toBe(initial);
    rerender({ search: 'tab=events' });
    expect(result.current).toBe(initial);
    expect(result.current.shouldWrite()).toBe(false);
    expect(result.current.shouldWrite()).toBe(true);
  });

  it('follows a link to the same page instead of reverting it', () => {
    renderPage();
    act(() => fireEvent.click(screen.getByText('link to performance')));
    expect(screen.getByTestId('tab')).toHaveTextContent('performance');
    expect(screen.getByTestId('search')).toHaveTextContent('?tab=performance');
  });

  it('never writes the old state back over the new URL', () => {
    renderPage();
    seenSearches.length = 0;
    act(() => fireEvent.click(screen.getByText('link to performance')));
    expect(seenSearches).not.toContain('?tab=overview');
    expect(seenSearches.length).toBeLessThan(5);
  });

  it('still writes state changes made on the page', () => {
    renderPage();
    act(() => fireEvent.click(screen.getByText('click events tab')));
    expect(screen.getByTestId('search')).toHaveTextContent('?tab=events');
    act(() => fireEvent.click(screen.getByText('link to performance')));
    act(() => fireEvent.click(screen.getByText('click events tab')));
    expect(screen.getByTestId('search')).toHaveTextContent('?tab=events');
  });
});
