import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TurnstileChallenge } from './TurnstileChallenge';

type Options = Parameters<NonNullable<Window['turnstile']>['render']>[1];
let options: Options;
const renderWidget = vi.fn((_container: HTMLElement, settings: Options) => { options = settings; return 'widget-1'; });
const removeWidget = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  window.turnstile = { render: renderWidget, remove: removeWidget };
});
afterEach(() => {
  delete window.turnstile;
  document.querySelectorAll('script[src*="challenges.cloudflare.com"]').forEach((script) => script.remove());
});

describe('Turnstile workspace challenge', () => {
  it('scopes the challenge and clears expired and failed tokens', async () => {
    const onToken = vi.fn();
    render(<TurnstileChallenge siteKey="public-site-key" onToken={onToken} />);
    await waitFor(() => expect(renderWidget).toHaveBeenCalledOnce());
    expect(options).toMatchObject({ sitekey: 'public-site-key', action: 'workspace_create' });
    act(() => options.callback('one-use-token'));
    expect(onToken).toHaveBeenLastCalledWith('one-use-token');
    act(() => options['expired-callback']());
    expect(onToken).toHaveBeenLastCalledWith(null);
    act(() => options.callback('next-token'));
    act(() => options['error-callback']());
    expect(onToken).toHaveBeenLastCalledWith(null);
    expect(screen.getByRole('status')).toHaveTextContent('could not finish');
    fireEvent.click(screen.getByRole('button', { name: 'try again' }));
    await waitFor(() => expect(renderWidget).toHaveBeenCalledTimes(2));
    expect(removeWidget).toHaveBeenCalledWith('widget-1');
  });

  it('removes its widget and ignores late callbacks after unmount', async () => {
    const onToken = vi.fn();
    const view = render(<TurnstileChallenge siteKey="public-site-key" onToken={onToken} />);
    await waitFor(() => expect(renderWidget).toHaveBeenCalledOnce());
    view.unmount();
    onToken.mockClear();
    act(() => options.callback('late-token'));
    act(() => options['expired-callback']());
    expect(removeWidget).toHaveBeenCalledWith('widget-1');
    expect(onToken).not.toHaveBeenCalled();
  });

  it('loads the official script, reports loading failure and supports retry', async () => {
    delete window.turnstile;
    const onToken = vi.fn();
    const view = render(<TurnstileChallenge siteKey="public-site-key" onToken={onToken} />);
    const script = document.querySelector<HTMLScriptElement>('script[src*="challenges.cloudflare.com"]');
    expect(script?.src).toBe('https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit');
    await act(async () => script?.dispatchEvent(new Event('error')));
    expect(screen.getByRole('status')).toHaveTextContent('could not finish');
    expect(onToken).toHaveBeenLastCalledWith(null);
    window.turnstile = { render: renderWidget, remove: removeWidget };
    fireEvent.click(screen.getByRole('button', { name: 'try again' }));
    await waitFor(() => expect(renderWidget).toHaveBeenCalledOnce());
    view.unmount();
  });

  it('does not render into an unmounted container when the script finishes late', async () => {
    delete window.turnstile;
    const onToken = vi.fn();
    const view = render(<TurnstileChallenge siteKey="public-site-key" onToken={onToken} />);
    const script = document.querySelector<HTMLScriptElement>('script[src*="challenges.cloudflare.com"]');
    view.unmount();
    window.turnstile = { render: renderWidget, remove: removeWidget };
    await act(async () => script?.dispatchEvent(new Event('load')));
    expect(renderWidget).not.toHaveBeenCalled();
  });
});
