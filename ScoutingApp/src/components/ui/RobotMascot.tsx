import styles from './RobotMascot.module.css';

// A small robot for "nothing here yet" screens: a wink of personality where a page is empty, not
// a character that talks. Decorative, so screen readers skip it; still with reduced motion.
export function RobotMascot({ size = 88 }: { size?: number }) {
  return (
    <svg className={styles.robot} width={size} height={size * 0.9} viewBox="0 0 96 86" aria-hidden="true" focusable="false">
      <g className={styles.bob}>
        <line x1="40" y1="10" x2="40" y2="20" className={styles.antenna} strokeWidth="3" strokeLinecap="round" />
        <circle cx="40" cy="8" r="4" className={styles.antennaTip} />
        <rect x="14" y="20" width="52" height="40" rx="12" className={styles.head} />
        <g className={styles.eyes}>
          <circle cx="30" cy="38" r="5" className={styles.face} />
          <circle cx="50" cy="38" r="5" className={styles.face} />
        </g>
        <path d="M31 49q9 6 18 0" className={styles.smile} strokeWidth="3" fill="none" strokeLinecap="round" />
        <rect x="22" y="62" width="36" height="18" rx="6" className={styles.body} />
        <g className={styles.arm}>
          <rect x="62" y="30" width="8" height="20" rx="4" className={styles.head} transform="rotate(-30 66 46)" />
        </g>
      </g>
    </svg>
  );
}
