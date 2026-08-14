import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  /** Optional label shown in the fallback UI (e.g. the page name). */
  label?: string;
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

const CHUNK_REFRESH_TS_KEY = 'frcmob_chunk_refresh_ts';
const CHUNK_REFRESH_COOLDOWN_MS = 15000;

function isChunkLoadFailure(error: Error | null): boolean {
  const message = String(error?.message || '').toLowerCase();
  if (!message) return false;
  return (
    message.includes('failed to fetch dynamically imported module') ||
    message.includes('importing a module script failed') ||
    message.includes('loading chunk') ||
    message.includes('chunkloaderror')
  );
}

/**
 * Generic React error boundary.
 *
 * Catches render-time exceptions in its subtree and displays a
 * recoverable fallback instead of tearing down the whole app.
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[ErrorBoundary${this.props.label ? ` – ${this.props.label}` : ''}]`, error, info.componentStack);

    // Deployment-safe recovery for stale code-split chunks:
    // if a lazy chunk 404s, do one hard refresh to get the latest index/chunks.
    if (isChunkLoadFailure(error)) {
      try {
        const lastRefreshRaw = window.sessionStorage.getItem(CHUNK_REFRESH_TS_KEY) || '';
        const lastRefresh = Number(lastRefreshRaw);
        const recentRefresh = Number.isFinite(lastRefresh) && Date.now() - lastRefresh < CHUNK_REFRESH_COOLDOWN_MS;
        if (!recentRefresh) {
          window.sessionStorage.setItem(CHUNK_REFRESH_TS_KEY, String(Date.now()));
          window.location.reload();
          return;
        }
      } catch {
        // If storage is unavailable, fall through to fallback UI.
      }
    }
  }

  private handleRetry = () => {
    if (isChunkLoadFailure(this.state.error)) {
      try {
        window.sessionStorage.removeItem(CHUNK_REFRESH_TS_KEY);
      } catch {
        // ignore
      }
      window.location.reload();
      return;
    }
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary-fallback">
          <h2 className="error-boundary-heading">
            {this.props.label ? `${this.props.label} failed to load` : 'Something went wrong'}
          </h2>
          <p className="error-boundary-message">
            {this.state.error?.message ?? 'An unexpected error occurred.'}
          </p>
          <button onClick={this.handleRetry} className="error-boundary-retry">
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
