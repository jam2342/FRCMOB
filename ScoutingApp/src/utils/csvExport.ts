/**
 * Client-side CSV export utility.
 * Generates a CSV string and triggers a browser download.
 */

/** Escape a cell value for RFC 4180 compliant CSV. */
function escapeCell(value: unknown): string {
  if (value == null) return '';
  const str = String(value);
  if (str.includes('"') || str.includes(',') || str.includes('\n') || str.includes('\r')) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/**
 * Build a CSV string from headers and rows.
 * @param headers - Column labels (first row).
 * @param rows - Array of cell-value rows.
 */
function buildCsv(headers: string[], rows: unknown[][]): string {
  const lines: string[] = [];
  lines.push(headers.map(escapeCell).join(','));
  for (const row of rows) {
    lines.push(row.map(escapeCell).join(','));
  }
  return lines.join('\r\n') + '\r\n';
}

/**
 * Trigger a CSV file download in the browser.
 * @param filename - Suggested file name (should end in .csv).
 * @param headers - Column header labels.
 * @param rows - Data rows (each row is an array of cell values).
 */
export function downloadCsv(filename: string, headers: string[], rows: unknown[][]): void {
  const csv = buildCsv(headers, rows);
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
