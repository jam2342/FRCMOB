import type { ReactNode } from 'react';

export type TableColumn<Row> = {
  key: string;
  label: string;
  align?: 'left' | 'right' | 'center';
  /** Marks the column as figures: tabular numerals and right alignment by default. */
  numeric?: boolean;
  /** Minimum width for the column. Applied as both width and min-width. */
  width?: string;
  sortable?: boolean;
  /** `index` is the row's position in `rows` — what an ordinal column needs. */
  render?: (row: Row, index: number) => ReactNode;
};

/**
 * Render one cell the way the table would. Lives here rather than in Table so a
 * page supplying `renderCards` can build its narrow view from the *same* column
 * definitions instead of a second copy of the formatting — that second copy is
 * exactly what this migration exists to delete.
 */
export function renderCell<Row>(column: TableColumn<Row>, row: Row, index = 0): ReactNode {
  if (column.render) return column.render(row, index);
  const value = (row as Record<string, unknown>)[column.key];
  if (value === null || value === undefined) return '—';
  return value as ReactNode;
}
