import { afterEach, describe, expect, it, vi } from 'vitest';
import { isPlainClick, navigateWithTransition, notifyRouteCommitted, sectionDirection, switchTabWithTransition } from './viewTransition';

type TransitionDoc = { startViewTransition?: unknown };
const transitionDoc = () => document as unknown as TransitionDoc;

function fakeTransitions() {
  const callbacks: Array<() => Promise<void> | void> = [];
  transitionDoc().startViewTransition = (update: () => Promise<void> | void) => {
    callbacks.push(update);
    return { finished: Promise.resolve(update()).then(() => undefined) };
  };
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
  return callbacks;
}

afterEach(() => {
  delete transitionDoc().startViewTransition;
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.vt;
});

describe('view transitions', () => {
  it('navigates directly where the browser has no View Transitions', () => {
    const navigate = vi.fn();
    navigateWithTransition(navigate, '/events');
    expect(navigate).toHaveBeenCalledWith('/events');
  });

  it('navigates directly when reduced motion is on', () => {
    fakeTransitions();
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    const navigate = vi.fn();
    navigateWithTransition(navigate, '/events', { direction: 'forward' });
    expect(navigate).toHaveBeenCalledWith('/events');
    expect(document.documentElement.dataset.vt).toBeUndefined();
  });

  it('holds the transition until the new route commits, then cleans up the morph name', async () => {
    fakeTransitions();
    const row = document.createElement('div');
    document.body.appendChild(row);
    const navigate = vi.fn();
    navigateWithTransition(navigate, '/match-center', { direction: 'forward', morphFrom: row, morphName: 'match-hero' });
    expect(navigate).toHaveBeenCalledWith('/match-center');
    expect(document.documentElement.dataset.vt).toBe('forward');
    expect(row.style.viewTransitionName).toBe('match-hero');
    notifyRouteCommitted();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(row.style.viewTransitionName).toBe('');
    expect(document.documentElement.dataset.vt).toBeUndefined();
    row.remove();
  });

  it('marks the page that slid in as arrived, so the shell fade does not replay after it', async () => {
    fakeTransitions();
    const content = document.createElement('main');
    content.className = 'ps-content';
    const pageRoot = document.createElement('div');
    content.appendChild(pageRoot);
    document.body.appendChild(content);
    navigateWithTransition(vi.fn(), '/events', { direction: 'forward' });
    notifyRouteCommitted();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(pageRoot.hasAttribute('data-arrived')).toBe(true);
    content.remove();
  });

  it('waits for placeholders the new page marks pending, and gives up after a short cap', async () => {
    fakeTransitions();
    const content = document.createElement('main');
    content.className = 'ps-content';
    const pending = document.createElement('p');
    pending.setAttribute('data-vt-pending', '');
    content.appendChild(pending);
    document.body.appendChild(content);
    navigateWithTransition(vi.fn(), '/scouting', { direction: 'forward' });
    notifyRouteCommitted();
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(document.documentElement.dataset.vt).toBe('forward');
    pending.remove();
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(document.documentElement.dataset.vt).toBeUndefined();

    content.appendChild(pending);
    navigateWithTransition(vi.fn(), '/events', { direction: 'forward' });
    notifyRouteCommitted();
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(document.documentElement.dataset.vt).toBeUndefined();
    content.remove();
  });

  it('runs a tab switch synchronously inside the transition and unpins afterwards', async () => {
    fakeTransitions();
    const header = document.createElement('div');
    const update = vi.fn();
    switchTabWithTransition(update, 'back', [header]);
    expect(update).toHaveBeenCalledTimes(1);
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(header.style.viewTransitionName).toBe('');
  });

  it('picks a direction from the navigation order', () => {
    const order = ['/home', '/events', '/scouting'];
    expect(sectionDirection(order, '/home', '/scouting')).toBe('forward');
    expect(sectionDirection(order, '/scouting/pit', '/events')).toBe('back');
    expect(sectionDirection(order, '/settings', '/events')).toBe('fade');
  });

  it('leaves modified clicks to the browser', () => {
    const base = { defaultPrevented: false, button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false };
    expect(isPlainClick(base)).toBe(true);
    expect(isPlainClick({ ...base, metaKey: true })).toBe(false);
    expect(isPlainClick({ ...base, button: 1 })).toBe(false);
  });
});
