import { afterEach, describe, expect, it, vi } from 'vitest';
import { isPlainClick, markNextPageArrived, navigateInstantly, navigateWithTransition, notifyRouteCommitted, sectionDirection, switchTabWithTransition } from './viewTransition';

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

  it('swallows a transition the browser aborts (phone viewport changed) instead of throwing', async () => {
    const unhandled = vi.fn();
    process.on('unhandledRejection', unhandled);
    vi.stubGlobal('matchMedia', () => ({ matches: false }));
    transitionDoc().startViewTransition = (update: () => Promise<void> | void) => {
      const abort = () => Promise.reject(new DOMException('Transition was aborted because of invalid state', 'InvalidStateError'));
      void update();
      return { ready: abort(), finished: abort() };
    };
    const navigate = vi.fn();
    navigateWithTransition(navigate, '/events');
    notifyRouteCommitted();
    switchTabWithTransition(vi.fn(), 'forward');
    await new Promise((resolve) => setTimeout(resolve, 20));
    process.off('unhandledRejection', unhandled);
    expect(navigate).toHaveBeenCalledWith('/events');
    expect(unhandled).not.toHaveBeenCalled();
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

describe('instant tab switches', () => {
  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = '';
    delete transitionDoc().startViewTransition;
  });

  it('marks the incoming page as arrived before it is drawn, so it never fades in', async () => {
    document.body.innerHTML = '<main class="ps-content"><section id="old"></section></main>';
    const content = document.querySelector('.ps-content')!;
    markNextPageArrived();
    const page = document.createElement('section');
    content.replaceChildren(page);
    await Promise.resolve();
    expect(page.hasAttribute('data-arrived')).toBe(true);
  });

  it('stops marking pages once the switch is over', async () => {
    vi.useFakeTimers();
    document.body.innerHTML = '<main class="ps-content"></main>';
    markNextPageArrived();
    vi.advanceTimersByTime(3000);
    const later = document.createElement('section');
    document.querySelector('.ps-content')!.append(later);
    await Promise.resolve();
    expect(later.hasAttribute('data-arrived')).toBe(false);
  });

  it('navigates at once, even with view transitions available, and every tap lands', () => {
    const starts = vi.fn();
    transitionDoc().startViewTransition = starts;
    document.body.innerHTML = '<main class="ps-content"></main>';
    const visited: string[] = [];
    for (const to of ['/home', '/events', '/scouting']) navigateInstantly((path) => visited.push(path), to);
    expect(visited).toEqual(['/home', '/events', '/scouting']);
    expect(starts).not.toHaveBeenCalled();
  });
});
