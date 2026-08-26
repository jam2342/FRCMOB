import { useEffect, useState, type ReactNode } from 'react';
import styles from './Table.module.css';
import { cx } from './cx';
import { renderCell, type TableColumn } from './tableCell';

export type { TableColumn };

export type SortDirection = 'asc' | 'desc';

export type TableProps<Row> = {
  columns: TableColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row, index: number) => string;
  sortBy?: { key: string; direction: SortDirection };
  onSort?: (key: string, direction: SortDirection) => void;
  stickyHeader?: boolean;
  stickyFirstCol?: boolean;
  empty?: ReactNode;
  /** Below this width each row renders as a stacked card instead of a table row. */
  cardBreakpoint?: number;
  /**
   * Replace the stacked-card fallback with a purpose-built narrow layout.
   * Stacking is the right default, but a compact standings grid beats one card
   * per row when the values are short — 30 rows of six figures fit on a phone,
   * 30 cards of six labelled lines do not. Supplying this keeps one definition
   * of the columns and rows while letting the narrow view stay designed.
   */
  renderCards?: (rows: Row[]) => ReactNode;
  /**
   * Per-row emphasis — "this is the team you are looking at" in a ranking of
   * 60. Returns a class from the page's own module, so the highlight stays a
   * page concern and the primitive stays colour-free.
   */
  rowClassName?: (row: Row, index: number) => string | false | undefined;
  className?: string;
  caption?: string;
};

const CARD_BREAKPOINT = 560;

function useIsNarrow(breakpoint: number) {
  const query = `(max-width: ${breakpoint}px)`;
  const [narrow, setNarrow] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const media = window.matchMedia(query);
    const update = () => setNarrow(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [query]);

  return narrow;
}

function SortIcon({ direction }: { direction: SortDirection | null }) {
  return (
    <svg className={styles.sortIcon} viewBox="0 0 12 12" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {direction === 'asc' ? (
        <path d="M6 9V3m0 0L3.5 5.5M6 3l2.5 2.5" />
      ) : direction === 'desc' ? (
        <path d="M6 3v6m0 0 2.5-2.5M6 9 3.5 6.5" />
      ) : (
        <path d="M4 4.5 6 2.5l2 2M4 7.5 6 9.5l2-2" />
      )}
    </svg>
  );
}

function alignClass<Row>(column: TableColumn<Row>): string | false {
  const align = column.align ?? (column.numeric ? 'right' : 'left');
  if (align === 'right') return styles.alignRight;
  if (align === 'center') return styles.alignCenter;
  return false;
}

export function Table<Row>({
  columns,
  rows,
  rowKey,
  sortBy,
  onSort,
  stickyHeader = false,
  stickyFirstCol = false,
  empty = 'Nothing to show yet.',
  cardBreakpoint = CARD_BREAKPOINT,
  renderCards,
  rowClassName,
  className,
  caption,
}: TableProps<Row>) {
  const narrow = useIsNarrow(cardBreakpoint);

  // Clicking the active column flips direction; a new column starts ascending.
  const toggleSort = (key: string) => {
    if (!onSort) return;
    const next: SortDirection = sortBy?.key === key && sortBy.direction === 'asc' ? 'desc' : 'asc';
    onSort(key, next);
  };

  const directionFor = (key: string): SortDirection | null =>
    sortBy?.key === key ? sortBy.direction : null;

  if (rows.length === 0) {
    return (
      <div className={cx(styles.scroller, className)}>
        <div className={styles.empty}>{empty}</div>
      </div>
    );
  }

  if (narrow) {
    const [first, ...restColumns] = columns;
    const sortables = columns.filter((column) => column.sortable);
    return (
      <div className={className}>
        {onSort && sortables.length > 0 ? (
          <div className={styles.cardSortBar}>
            {sortables.map((column) => {
              const direction = directionFor(column.key);
              return (
                <button
                  key={column.key}
                  type="button"
                  className={cx(styles.cardSortChip, direction && styles.cardSortChipActive)}
                  onClick={() => toggleSort(column.key)}
                  aria-pressed={Boolean(direction)}
                >
                  {column.label}
                  <SortIcon direction={direction} />
                </button>
              );
            })}
          </div>
        ) : null}
        {renderCards ? renderCards(rows) : (
        <div className={styles.cards}>
          {rows.map((row, index) => (
            <div className={cx(styles.card, rowClassName?.(row, index))} key={rowKey(row, index)}>
              <div className={styles.cardHead}>{renderCell(first, row, index)}</div>
              {restColumns.map((column) => (
                <div className={styles.cardRow} key={column.key}>
                  <span className={styles.cardLabel}>{column.label}</span>
                  <span className={cx(styles.cardValue, column.numeric && styles.numeric)}>
                    {renderCell(column, row, index)}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
        )}
      </div>
    );
  }

  return (
    <div className={cx(styles.scroller, className)}>
      <table
        className={cx(
          styles.table,
          stickyHeader && styles.stickyHeader,
          stickyFirstCol && styles.stickyFirstCol,
        )}
      >
        {caption ? <caption className={styles.caption}>{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((column) => {
              const direction = directionFor(column.key);
              return (
                <th
                  key={column.key}
                  scope="col"
                  /* `width` alone is only a hint: the table is width:100%, so
                     a column competing with eleven numeric ones gets squeezed
                     to nothing anyway — Compare's team names were breaking
                     across five lines. A declared width is a floor, and the
                     scroller already handles the overflow that causes. */
                  style={column.width ? { width: column.width, minWidth: column.width } : undefined}
                  className={cx(styles.th, alignClass(column), column.numeric && styles.numeric)}
                  aria-sort={
                    direction ? (direction === 'asc' ? 'ascending' : 'descending') : undefined
                  }
                >
                  {column.sortable && onSort ? (
                    <button
                      type="button"
                      className={cx(styles.sortButton, direction && styles.sortActive)}
                      onClick={() => toggleSort(column.key)}
                    >
                      {column.label}
                      <SortIcon direction={direction} />
                    </button>
                  ) : (
                    column.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={rowKey(row, index)} className={cx(rowClassName?.(row, index))}>
              {columns.map((column, columnIndex) =>
                columnIndex === 0 ? (
                  <th
                    key={column.key}
                    scope="row"
                    className={cx(styles.td, alignClass(column), column.numeric && styles.numeric)}
                  >
                    {renderCell(column, row, index)}
                  </th>
                ) : (
                  <td
                    key={column.key}
                    className={cx(styles.td, alignClass(column), column.numeric && styles.numeric)}
                  >
                    {renderCell(column, row, index)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
