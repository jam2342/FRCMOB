type BootWindow = Window & { __frcmobBoot?: Record<string, Promise<Response>> };

// Hands over a request index.html already started, once. Null when there is none
// for this URL (native app, dev server, or already taken).
export function takeBootFetch(url: string): Promise<Response> | null {
  if (typeof window === 'undefined') return null;
  const boot = (window as BootWindow).__frcmobBoot;
  if (!boot || !(url in boot)) return null;
  const pending = boot[url];
  delete boot[url];
  return pending;
}
