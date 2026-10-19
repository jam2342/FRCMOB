import { useEffect, useRef, useState } from 'react';

type TurnstileOptions = {
  sitekey: string;
  action: 'workspace_create';
  theme: 'auto';
  size: 'flexible';
  callback: (token: string) => void;
  'expired-callback': () => void;
  'error-callback': () => boolean;
};

type Turnstile = {
  render: (container: HTMLElement, options: TurnstileOptions) => string;
  remove: (widgetId: string) => void;
};

declare global {
  interface Window { turnstile?: Turnstile }
}

let scriptRequest: Promise<Turnstile> | null = null;

function loadTurnstile(): Promise<Turnstile> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (scriptRequest) return scriptRequest;
  scriptRequest = new Promise<Turnstile>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.async = true;
    script.defer = true;
    const fail = () => {
      window.clearTimeout(timeout);
      script.remove();
      reject(new Error('Security check could not load.'));
    };
    const timeout = window.setTimeout(fail, 15000);
    script.onload = () => {
      window.clearTimeout(timeout);
      if (window.turnstile) resolve(window.turnstile);
      else fail();
    };
    script.onerror = fail;
    document.head.appendChild(script);
  }).catch((error: unknown) => {
    scriptRequest = null;
    throw error;
  });
  return scriptRequest;
}

export function TurnstileChallenge({ siteKey, onToken }: {
  siteKey: string;
  onToken: (token: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    let widgetId: string | undefined;
    let api: Turnstile | undefined;
    onToken(null);
    loadTurnstile().then((loaded) => {
      if (!active || !container.current) return;
      api = loaded;
      widgetId = loaded.render(container.current, {
        sitekey: siteKey,
        action: 'workspace_create',
        theme: 'auto',
        size: 'flexible',
        callback: (token) => {
          if (active) { setError(false); onToken(token); }
        },
        'expired-callback': () => { if (active) onToken(null); },
        'error-callback': () => {
          if (active) { onToken(null); setError(true); }
          return true;
        },
      });
    }).catch(() => {
      if (active) { onToken(null); setError(true); }
    });
    return () => {
      active = false;
      if (widgetId !== undefined) api?.remove(widgetId);
    };
  }, [siteKey, onToken, attempt]);

  return (
    <div>
      <div ref={container} role="group" aria-label="Workspace security check" />
      {error ? (
        <p className="my-team__note" role="status">
          The security check could not finish. Check your connection and{' '}
          <button type="button" onClick={() => { setError(false); setAttempt((value) => value + 1); }}>try again</button>.
        </p>
      ) : null}
    </div>
  );
}
