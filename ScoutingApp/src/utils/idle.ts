export function scheduleIdleWork(
  callback: () => void,
  options?: {
    fallbackDelayMs?: number;
    timeoutMs?: number;
  },
): number {
  const fallbackDelayMs = Math.max(0, Math.floor(options?.fallbackDelayMs ?? 45));
  const timeoutMs = Math.max(0, Math.floor(options?.timeoutMs ?? 120));
  if (typeof window.requestIdleCallback === 'function') {
    return window.requestIdleCallback(() => callback(), { timeout: timeoutMs });
  }
  return window.setTimeout(callback, fallbackDelayMs);
}

export function cancelIdleWork(handle: number): void {
  if (typeof window.cancelIdleCallback === 'function') {
    window.cancelIdleCallback(handle);
    return;
  }
  window.clearTimeout(handle);
}
