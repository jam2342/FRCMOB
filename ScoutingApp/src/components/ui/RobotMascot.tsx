import './RobotMascot.css';

type RobotMascotProps = {
  // Width in px; the height follows the drawing. CSS sets it when left out.
  size?: number;
  className?: string;
  animated?: boolean;
  pose?: 'wave' | 'point-left' | 'point-right';
  talking?: boolean;
};

// Each arm's resting angle per pose, around its shoulder (the intake pivot on the elevator).
// The CSS animates the outer arm group on top of this, so a pose and a wave combine.
const LEFT_ARM = { wave: -35, 'point-left': 2, 'point-right': -35 } as const;
const RIGHT_ARM = { wave: -62, 'point-left': 35, 'point-right': -12 } as const;

// The one FRCMOB robot, Swerve Scout: an FRC robot with a screen face on its shooter hood, an
// elevator, bumpers and swerve modules. It guides the page tours (waving, pointing, talking)
// and is the quiet face of "nothing here yet" screens (EmptyState). Decorative, so screen
// readers skip it; still with reduced motion (RobotMascot.css).
export function RobotMascot({
  size,
  className,
  animated = true,
  pose = 'wave',
  talking = false,
}: RobotMascotProps) {
  const classes = [
    'robot-mascot',
    animated && 'is-animated',
    talking && 'is-talking',
    `is-${pose}`,
    className,
  ].filter(Boolean).join(' ');
  const leftArmClass = [
    'robot-mascot-arm-group',
    'robot-mascot-arm-group-left',
    pose === 'point-left' && 'is-pointing',
  ].filter(Boolean).join(' ');
  const rightArmClass = [
    'robot-mascot-arm-group',
    'robot-mascot-arm-group-right',
    pose === 'wave' && 'is-waving',
    pose === 'point-right' && 'is-pointing',
  ].filter(Boolean).join(' ');

  return (
    <svg className={classes} viewBox="0 0 120 120" width={size} style={size ? { width: size } : undefined} aria-hidden="true" focusable="false">
      <ellipse className="robot-mascot-shadow" cx="60" cy="111" rx="42" ry="4" />
      <g className="robot-mascot-talk-marks robot-mascot-talk-marks-left">
        <path d="M17 26l-8-4" />
        <path d="M20 17l-5-7" />
      </g>
      <g className="robot-mascot-talk-marks robot-mascot-talk-marks-right">
        <path d="M103 26l8-4" />
        <path d="M100 17l5-7" />
      </g>
      <g className="robot-mascot-body-group">
        <rect className="robot-mascot-wheel" x="16" y="96" width="18" height="11" rx="3" />
        <rect className="robot-mascot-wheel" x="86" y="96" width="18" height="11" rx="3" />
        <rect className="robot-mascot-frame" x="22" y="70" width="76" height="12" rx="2" />
        <rect className="robot-mascot-tube" x="32" y="42" width="6" height="32" />
        <rect className="robot-mascot-tube" x="82" y="42" width="6" height="32" />
        <rect className="robot-mascot-crossbar" x="32" y="58" width="56" height="4" />
      </g>
      <g className={leftArmClass}>
        <g transform={`rotate(${LEFT_ARM[pose]} 30 62)`}>
          <rect className="robot-mascot-arm" x="8" y="59" width="24" height="6" rx="3" />
          <circle className="robot-mascot-wheel" cx="9" cy="62" r="6" />
          <circle className="robot-mascot-hub" cx="9" cy="62" r="2.2" />
        </g>
      </g>
      <g className={rightArmClass}>
        <g transform={`rotate(${RIGHT_ARM[pose]} 90 62)`}>
          <rect className="robot-mascot-arm" x="88" y="59" width="24" height="6" rx="3" />
          <circle className="robot-mascot-wheel" cx="111" cy="62" r="6" />
          <circle className="robot-mascot-hub" cx="111" cy="62" r="2.2" />
        </g>
      </g>
      <g className="robot-mascot-arm-swoosh robot-mascot-arm-swoosh-left">
        <path d="M10 44l-6-3" />
        <path d="M14 38l-4-5" />
      </g>
      <g className="robot-mascot-arm-swoosh robot-mascot-arm-swoosh-right">
        <path d="M110 30l6-3" />
        <path d="M106 24l4-5" />
      </g>
      <g className="robot-mascot-body-group">
        <rect className="robot-mascot-body" x="10" y="80" width="100" height="19" rx="7" />
        <text className="robot-mascot-number" x="60" y="93.3" textAnchor="middle">FRCMOB</text>
        <circle className="robot-mascot-core" cx="101" cy="75" r="2.6" />
      </g>
      <rect className="robot-mascot-antenna" x="58.5" y="7" width="3" height="9" />
      <circle className="robot-mascot-antenna-light" cx="60" cy="7" r="4" />
      <g className="robot-mascot-head-group">
        <rect className="robot-mascot-head" x="24" y="15" width="72" height="34" rx="11" />
        <rect className="robot-mascot-face" x="31" y="21" width="58" height="22" rx="7" />
        <rect className="robot-mascot-eye" x="42" y="25.5" width="9" height="11" rx="4.5" />
        <rect className="robot-mascot-eye" x="69" y="25.5" width="9" height="11" rx="4.5" />
        <path className="robot-mascot-mouth" d="M54 39q6 3 12 0" />
      </g>
    </svg>
  );
}
