// Capacitor's POST/PUT fetch bridge does not consume AbortSignal. Race its promise
// so a lost connection cannot leave the offline queue permanently "syncing".
// The underlying request may still finish; replay must keep its original write ID.
export function abortableFetch(fetcher: typeof fetch): typeof fetch {
  return (input, init) => {
    const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
    if (!signal) return fetcher(input, init);
    if (signal.aborted) return Promise.reject(signal.reason ?? new DOMException('Request aborted', 'AbortError'));
    return new Promise<Response>((resolve, reject) => {
      const abort = () => reject(signal.reason ?? new DOMException('Request aborted', 'AbortError'));
      signal.addEventListener('abort', abort, { once: true });
      try {
        void fetcher(input, init).then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
      } catch (error) {
        signal.removeEventListener('abort', abort);
        reject(error);
      }
    });
  };
}
