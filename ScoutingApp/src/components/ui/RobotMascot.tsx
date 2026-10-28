import styles from './RobotMascot.module.css';

// A small robot for "nothing here yet" screens: a wink of personality where a page is empty, not
// a character that talks. Same head as the logo's robot. Decorative, so screen readers skip it;
// still with reduced motion.
export function RobotMascot({ size = 88 }: { size?: number }) {
  return (
    <svg className={styles.robot} width={size} height={size} viewBox="0 0 96 96" aria-hidden="true" focusable="false">
      <g className={styles.bob}>
        <line x1="48" y1="8" x2="48" y2="17" className={styles.antenna} strokeWidth="3" strokeLinecap="round" />
        <circle cx="48" cy="7" r="4" className={styles.antennaTip} />
        <rect x="42" y="52" width="12" height="8" rx="2" className={styles.joint} />
        <rect x="18" y="60" width="9" height="20" rx="4.5" className={styles.limb} />
        <g className={styles.arm}>
          <rect x="69" y="42" width="9" height="20" rx="4.5" className={styles.limb} />
        </g>
        <rect x="27" y="58" width="42" height="30" rx="9" className={styles.body} />
        <circle cx="48" cy="72" r="4" className={styles.light} />
        <rect x="21" y="16" width="54" height="40" rx="13" className={styles.head} />
        <g className={styles.eyes}>
          <circle cx="38" cy="34" r="5" className={styles.face} />
          <circle cx="58" cy="34" r="5" className={styles.face} />
        </g>
        <path d="M39 45q9 6 18 0" className={styles.smile} strokeWidth="3" fill="none" strokeLinecap="round" />
      </g>
    </svg>
  );
}
