import { Component, type ErrorInfo, type ReactNode } from 'react';

// For extras around the page (next-match pill, team peek, ⌘K palette, the tour):
// if one fails, most often because its chunk didn't download on bad wifi, that
// one thing is left out instead of the error taking the whole app down to a
// blank screen. The page itself keeps its own ErrorBoundary with a message.
export class QuietBoundary extends Component<{ name: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.warn(`[${this.props.name}] left out after an error:`, error.message, info.componentStack?.split('\n')[1]?.trim());
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}
