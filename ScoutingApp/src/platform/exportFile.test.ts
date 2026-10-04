import { afterEach, describe, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ native: true, writeFile: vi.fn(async () => ({ uri: 'file:///fixture-cache/exports/recovery.json' })), share: vi.fn(async () => ({})) }));
vi.mock('./runtime', () => ({ isNativeApp: () => mocks.native }));
vi.mock('@capacitor/filesystem', () => ({ Filesystem: { writeFile: mocks.writeFile }, Directory: { Cache: 'CACHE' }, Encoding: { UTF8: 'utf8' } }));
vi.mock('@capacitor/share', () => ({ Share: { share: mocks.share } }));
import { exportPrintableReport, exportTextFile } from './exportFile';
afterEach(() => { vi.restoreAllMocks(); mocks.native = true; });
describe('native recovery export', () => {
  it('writes exact data in app cache and shares the file with the OS', async () => {
    await exportTextFile('recovery.json', '{"pointsByTeam":{"frc254":[]}}');
    expect(mocks.writeFile).toHaveBeenCalledWith(expect.objectContaining({ path: expect.stringMatching(/^exports\/\d+-recovery.json$/), directory: 'CACHE', encoding: 'utf8', data: '{"pointsByTeam":{"frc254":[]}}', recursive: true }));
    expect(mocks.share).toHaveBeenCalledWith(expect.objectContaining({ files: ['file:///fixture-cache/exports/recovery.json'] }));
  });
  it('does not silently claim success if native storage fails', async () => {
    mocks.writeFile.mockRejectedValueOnce(new Error('Storage full'));
    await expect(exportTextFile('recovery.json', '{}')).rejects.toThrow('Storage full');
    expect(mocks.share).not.toHaveBeenCalled();
  });
  it('treats iOS share-sheet cancellation as cancellation while retaining the cache file', async () => {
    mocks.share.mockRejectedValueOnce(new Error('Share canceled'));
    await expect(exportTextFile('recovery.json', '{}')).resolves.toBe(false);
    expect(mocks.writeFile).toHaveBeenCalled();
  });
  it('sanitizes filenames so exports cannot escape their cache folder', async () => {
    await exportTextFile('../../recovery.json', '{}');
    expect(mocks.writeFile).toHaveBeenCalledWith(expect.objectContaining({ path: expect.stringMatching(/^exports\/\d+-\.\._\.\._recovery.json$/) }));
  });
});

describe('native printable reports', () => {
  it('shares a self-contained report without controls, scripts or local image URLs', async () => {
    document.body.innerHTML = '<main><h1>Picklist</h1><table><tbody><tr><td>254 &amp; 1678</td></tr></tbody></table><button>Delete</button><input value="private"><script>secret()</script><img src="/Heading.png"><nav>Menu</nav></main>';
    const style = document.createElement('style'); style.textContent = 'table { border-collapse: collapse; }'; document.head.append(style);
    try {
      await exportPrintableReport('picklist.html');
      const calls = mocks.writeFile.mock.calls as unknown as Array<[{data: string}]>;
      const html = calls.at(-1)![0];
      expect(html.data).toContain('254 &amp; 1678');
      expect(html.data).toContain('border-collapse');
      expect(html.data).not.toMatch(/<script|<input|<button|<nav|src="\/Heading/);
      expect(html.data).toContain('<!doctype html>');
    } finally { style.remove(); document.body.replaceChildren(); }
  });
  it('keeps the browser print flow on the web', async () => {
    mocks.native = false; const print = vi.spyOn(window, 'print').mockImplementation(() => {});
    await exportPrintableReport('picklist.html');
    expect(print).toHaveBeenCalledOnce(); expect(mocks.writeFile).not.toHaveBeenCalled();
  });
});
