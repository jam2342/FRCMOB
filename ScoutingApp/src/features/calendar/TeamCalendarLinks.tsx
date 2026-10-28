import { teamCalendarUrls } from './calendarUrls';
import { isNativeApp } from '../../platform/runtime';
import styles from './TeamCalendarLinks.module.css';

type Props = { eventKey: string; teamNumber: number };

// "Add our matches to my calendar": one subscription per phone, and the times follow the
// field running early or late. For pit crew and parents as much as scouts.
export function TeamCalendarLinks({ eventKey, teamNumber }: Props) {
  const urls = teamCalendarUrls(eventKey, `frc${teamNumber}`);
  return (
    <p className={styles.row}>
      <span>Team {teamNumber}'s matches in your calendar:</span>
      {isNativeApp() ? (
        // The app hands https links to the system browser, which offers to add the calendar.
        <a className="center-inline-link" href={urls.download}>Add to calendar</a>
      ) : (
        // Kept together, so a narrow screen doesn't leave Download on a line of its own.
        <span className={styles.links}>
          <a className="center-inline-link" href={urls.subscribe}>Subscribe</a>
          <a className="center-inline-link" href={urls.download} download={`${teamNumber}-${eventKey}.ics`}>Download</a>
        </span>
      )}
    </p>
  );
}
