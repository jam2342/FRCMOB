export function ScheduleStatus({ error, loading, retry }: { error?: string; loading?: boolean; retry: () => void }) {
  return error ? <p className="center-callout warning" role="alert">{error} <button type="button" className="center-btn ghost" onClick={retry} disabled={loading}>Retry schedule</button></p> : <p role="status">Loading match schedule…</p>;
}
