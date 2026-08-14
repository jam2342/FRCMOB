/**
 * Full-page loading spinner shown while lazy-loaded routes resolve.
 * Pairs with the `.page-spinner` styles in ProductShell.css.
 */
export function PageSpinner() {
  return (
    <div className="page-spinner" role="status" aria-label="Loading page">
      <div className="page-spinner-ring" />
      <span className="page-spinner-label">Loading…</span>
    </div>
  );
}
