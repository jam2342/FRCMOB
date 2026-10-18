import { pastSeasonNote } from './pastSeason';
import styles from './PastSeasonBanner.module.css';

export function PastSeasonBanner({ eventKey }: { eventKey: string | null | undefined }) {
  const note = pastSeasonNote(eventKey);
  if (!note) return null;
  return (
    <div className={styles.banner} role="note" data-testid="past-season-banner">
      {note}
    </div>
  );
}
