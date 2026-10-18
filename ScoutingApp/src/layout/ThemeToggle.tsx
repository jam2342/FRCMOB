import { toggleTheme } from '../utils/theme';
import type { ThemeMode } from './userSettings';
import styles from './ThemeToggle.module.css';

// Sun or moon in the top bar: one tap switches theme, spreading out from the button.
export function ThemeToggle({ theme, className }: { theme: ThemeMode; className?: string }) {
  const toLight = theme !== 'light';
  return (
    <button
      type="button"
      className={`${styles.toggle} ${className ?? ''}`}
      onClick={(event) => toggleTheme(event.currentTarget)}
      aria-label={toLight ? 'Switch to light theme' : 'Switch to dark theme'}
      title={toLight ? 'Light theme' : 'Dark theme'}
    >
      {toLight ? (
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round">
          <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
        </svg>
      )}
    </button>
  );
}
