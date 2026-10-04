import type { Plugin } from 'vite';

// The on-device recorder runs the detector in WebAssembly, which only uses more than one
// CPU core when the page is cross-origin isolated (SharedArrayBuffer). Isolating the whole
// app would block YouTube embeds and third-party images, so only record.html is isolated:
// Vercel sets these headers for it in vercel.json, and this does the same for the dev and
// preview servers.
//
// A worker started by an isolated page must itself be served with a compatible COEP, or
// the browser refuses to start it -- and ORT then waits forever for its thread pool
// instead of failing. Subresources therefore carry COEP too; outside a worker or document
// it does nothing, so the rest of the app is unaffected.
export const RECORDER_ISOLATION_HEADERS = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
} as const;

function isRecorderDocument(url: string | undefined): boolean {
  return Boolean(url && url.split('?')[0].split('#')[0] === '/record.html');
}

// Everything but an HTML document: in dev a worker's module graph includes /@vite/client
// and bare /src paths, so matching on file extension is not enough.
function isSubresource(url: string | undefined): boolean {
  const path = (url ?? '/').split('?')[0].split('#')[0];
  return path !== '/' && !path.endsWith('.html');
}

export function isolateRecorderPage(): Plugin {
  const middleware = (req: { url?: string }, res: { setHeader: (k: string, v: string) => void }, next: () => void) => {
    if (isRecorderDocument(req.url)) {
      for (const [key, value] of Object.entries(RECORDER_ISOLATION_HEADERS)) res.setHeader(key, value);
    } else if (isSubresource(req.url)) {
      res.setHeader('Cross-Origin-Embedder-Policy', RECORDER_ISOLATION_HEADERS['Cross-Origin-Embedder-Policy']);
    }
    next();
  };
  return {
    name: 'isolate-recorder-page',
    configureServer(server) {
      server.middlewares.use(middleware);
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
  };
}
