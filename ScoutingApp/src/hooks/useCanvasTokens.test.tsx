import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useCanvasTokens } from './useCanvasTokens';

const TOKENS = ['--field-canvas-bg', '--field-canvas-grid'] as const;

function Probe() {
  const tokens = useCanvasTokens(TOKENS);
  return <span data-testid="bg">{tokens['--field-canvas-bg'] || 'empty'}</span>;
}

function setTheme(theme: 'dark' | 'light') {
  const style = document.createElement('style');
  style.id = 'canvas-token-test';
  style.textContent = `
    :root { --field-canvas-bg: #1a2332; --field-canvas-grid: rgba(255,255,255,0.06); }
    :root.theme-light { --field-canvas-bg: #dde5ee; --field-canvas-grid: rgba(15,23,42,0.07); }
  `;
  document.getElementById('canvas-token-test')?.remove();
  document.head.appendChild(style);
  document.documentElement.classList.remove('theme-dark', 'theme-light');
  document.documentElement.classList.add(`theme-${theme}`);
}

afterEach(() => {
  document.getElementById('canvas-token-test')?.remove();
  document.documentElement.classList.remove('theme-dark', 'theme-light');
});

describe('useCanvasTokens', () => {
  it('resolves a token to a concrete colour a canvas can use', () => {
    setTheme('dark');
    render(<Probe />);
    expect(screen.getByTestId('bg')).toHaveTextContent('#1a2332');
  });

  it('re-reads when the theme class changes', async () => {
    setTheme('dark');
    render(<Probe />);
    expect(screen.getByTestId('bg')).toHaveTextContent('#1a2332');

    // The whole point: a canvas keeps its pixels when the stylesheet swaps, so
    // it has to be told. MutationObserver delivers on a microtask.
    await act(async () => {
      document.documentElement.classList.remove('theme-dark');
      document.documentElement.classList.add('theme-light');
      await Promise.resolve();
    });

    expect(screen.getByTestId('bg')).toHaveTextContent('#dde5ee');
  });

  it('does not hand back a new object when nothing changed', async () => {
    setTheme('dark');
    const seen: string[] = [];
    function Counter() {
      const tokens = useCanvasTokens(TOKENS);
      seen.push(tokens['--field-canvas-bg']);
      return null;
    }
    render(<Counter />);
    const before = seen.length;

    await act(async () => {
      // An unrelated class change on the same attribute.
      document.documentElement.classList.add('density-compact');
      await Promise.resolve();
    });

    expect(seen.length).toBe(before);
    document.documentElement.classList.remove('density-compact');
  });
});
