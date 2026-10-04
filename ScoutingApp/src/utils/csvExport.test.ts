import { describe, expect, it, vi } from 'vitest';
const exportFile = vi.hoisted(() => vi.fn(async () => true));
vi.mock('../platform/exportFile', () => ({ exportTextFile: exportFile }));
import { downloadCsv } from './csvExport';

describe('CSV export', () => {
  it('shares an RFC 4180 file with quoted commas, quotes and line breaks', async () => {
    await downloadCsv('scouting.csv', ['Team', 'Notes'], [[254, 'a, "quote"\nline'], [null, '']]);
    expect(exportFile).toHaveBeenCalledWith('scouting.csv', 'Team,Notes\r\n254,"a, ""quote""\nline"\r\n,\r\n', 'text/csv;charset=utf-8');
  });
  it('propagates storage failure so callers cannot report success', async () => {
    exportFile.mockRejectedValueOnce(new Error('Storage full'));
    await expect(downloadCsv('scouting.csv', ['Team'], [[254]])).rejects.toThrow('Storage full');
  });
});
