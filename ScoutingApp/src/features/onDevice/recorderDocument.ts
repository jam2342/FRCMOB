// The recorder runs from its own document, record.html, which is served cross-origin
// isolated so the detector can use several CPU cores (build/isolateRecorder.ts). The rest
// of the app stays on index.html, where isolation would block YouTube embeds and outside
// images. These decide when to hop between the two with a full page load.
const RECORDER_ROUTE = '/scouting/record';
const RECORDER_DOCUMENT = '/record.html';

function isRecorderRoute(routePath: string): boolean {
  return routePath === RECORDER_ROUTE || routePath.startsWith(`${RECORDER_ROUTE}/`);
}

export function recorderDocumentRedirect(
  documentPath: string,
  routePath: string,
  search: string,
  isolation: { supported: boolean; active: boolean },
): string | null {
  const onRecorderDocument = documentPath.endsWith(RECORDER_DOCUMENT);
  if (onRecorderDocument && !isRecorderRoute(routePath)) return `/#${routePath}${search}`;
  // Only hop when the browser can actually isolate; otherwise the recorder just runs
  // single-threaded where it is. An isolated page never redirects, so this can't loop.
  if (!onRecorderDocument && isRecorderRoute(routePath) && isolation.supported && !isolation.active) {
    return `${RECORDER_DOCUMENT}#${routePath}${search}`;
  }
  return null;
}
