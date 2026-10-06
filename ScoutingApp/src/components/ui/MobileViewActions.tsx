import { Button } from './primitives';
import styles from './MobileViewActions.module.css';

export type MobileViewAction = {
  label: string;
  onClick: () => void;
  // "Back to …" sits on the left, the others on the right.
  back?: boolean;
};

// Phones used to get a two- or three-way switch (Event Finder / Calendar / Event Center) above the
// page, with labels wrapping onto two lines. Once something is chosen, what's left to do is
// "change it" or "go back to it", so that's all this shows: one slim row of plain actions.
export function MobileViewActions({ actions, label }: { actions: MobileViewAction[]; label: string }) {
  if (actions.length === 0) return null;
  const back = actions.filter((action) => action.back);
  const rest = actions.filter((action) => !action.back);
  return (
    <nav className={`mobile-view-actions ${styles.row}`} aria-label={label}>
      <div className={styles.side}>
        {back.map((action) => (
          <Button key={action.label} size="sm" variant="quiet" onClick={action.onClick} className={styles.back}>
            ‹ {action.label}
          </Button>
        ))}
      </div>
      <div className={styles.side}>
        {rest.map((action) => (
          <Button key={action.label} size="sm" variant="default" onClick={action.onClick}>
            {action.label}
          </Button>
        ))}
      </div>
    </nav>
  );
}
