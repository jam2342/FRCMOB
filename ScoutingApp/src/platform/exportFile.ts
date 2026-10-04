import { isNativeApp } from './runtime';

// Blob download links aren't portable across native WebViews. Use the system share sheet there.
export async function exportTextFile(filename: string, text: string, mimeType = 'application/json'): Promise<boolean> {
  if (isNativeApp()) {
    const [{ Filesystem, Directory, Encoding }, { Share }] = await Promise.all([
      import('@capacitor/filesystem'), import('@capacitor/share'),
    ]);
    const safeName = filename.replace(/[^a-z0-9._-]/gi, '_');
    const path = `exports/${Date.now()}-${safeName}`;
    const file = await Filesystem.writeFile({ path, directory: Directory.Cache, data: text, encoding: Encoding.UTF8, recursive: true });
    // Keep the cache copy after sharing/cancellation; never delete the pending original.
    try {
      await Share.share({ title: filename, files: [file.uri], dialogTitle: 'Save or share your export' });
      return true;
    } catch (error) {
      // iOS reports dismissal as a rejection; cancelling is not a storage failure.
      if (error instanceof Error && error.message === 'Share canceled') return false;
      throw error;
    }
  }
  const url = URL.createObjectURL(new Blob([text], { type: mimeType }));
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally { setTimeout(() => URL.revokeObjectURL(url), 1000); }
  return true;
}

// Native WebViews have no browser Print dialog. Share a self-contained HTML report
// that can be opened offline; web builds retain their existing print/PDF flow.
export async function exportPrintableReport(filename: string): Promise<void> {
  if (!isNativeApp()) { window.print(); return; }
  const report = document.implementation.createHTMLDocument(document.title || 'FRCMOB report');
  const viewport = report.createElement('meta');
  viewport.name = 'viewport';
  viewport.content = 'width=device-width, initial-scale=1';
  report.head.append(viewport);
  const style = report.createElement('style');
  style.textContent = Array.from(document.styleSheets).map(sheet => {
    try { return Array.from(sheet.cssRules).map(rule => rule.cssText).join('\n'); }
    catch { return ''; }
  }).join('\n');
  report.head.append(style);
  report.documentElement.className = document.documentElement.className;
  report.body.className = document.body.className;
  const content = document.querySelector('main') ?? document.getElementById('root') ?? document.body;
  const snapshot = content.cloneNode(true) as HTMLElement;
  snapshot.querySelectorAll('script, iframe, input, select, textarea, button, nav, .no-print, [role="dialog"]').forEach(node => node.remove());
  // Local bundle URLs cannot be resolved by another app opening the report.
  snapshot.querySelectorAll('img').forEach(img => { if (!img.src.startsWith('data:')) img.remove(); });
  report.body.append(snapshot);
  await exportTextFile(filename, `<!doctype html>\n${report.documentElement.outerHTML}`, 'text/html;charset=utf-8');
}
