import './RobotMascot.css';

type RobotMascotProps = {
  // Width in px; the height follows the drawing. CSS sets it when left out.
  size?: number;
  className?: string;
  animated?: boolean;
  pose?: 'wave' | 'point-left' | 'point-right';
  talking?: boolean;
};

// The one FRCMOB robot: the page tours' guide (waving, pointing, talking) and the quiet
// face of "nothing here yet" screens (EmptyState). Decorative, so screen readers skip it;
// still with reduced motion (RobotMascot.css).
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
  const pointingLeft = pose === 'point-left';
  const pointingRight = pose === 'point-right';
  const leftArmClass = [
    'robot-mascot-arm-group',
    'robot-mascot-arm-group-left',
    pointingLeft && 'is-pointing',
  ].filter(Boolean).join(' ');
  const rightArmClass = [
    'robot-mascot-arm-group',
    'robot-mascot-arm-group-right',
    pose === 'wave' && 'is-waving',
    pointingRight && 'is-pointing',
  ].filter(Boolean).join(' ');

  return (
    <svg className={classes} viewBox="0 0 96 112" width={size} style={size ? { width: size } : undefined} aria-hidden="true" focusable="false">
      <path className="robot-mascot-shadow" d="M25 101c8 5 38 5 46 0 3-2 3-5 0-7-8-5-38-5-46 0-3 2-3 5 0 7z" />
      <g className="robot-mascot-talk-marks robot-mascot-talk-marks-left">
        <path d="M15 35l-8-4" />
        <path d="M18 25l-5-8" />
      </g>
      <g className="robot-mascot-talk-marks robot-mascot-talk-marks-right">
        <path d="M81 35l8-4" />
        <path d="M78 25l5-8" />
      </g>
      <path className="robot-mascot-antenna" d="M48 22V11" />
      <circle className="robot-mascot-antenna-light" cx="48" cy="8" r="5" />
      <g className="robot-mascot-head-group">
        <rect className="robot-mascot-head" x="21" y="20" width="54" height="46" rx="18" />
        <rect className="robot-mascot-face" x="28" y="29" width="40" height="26" rx="13" />
        <circle className="robot-mascot-eye" cx="40" cy="42" r="5" />
        <circle className="robot-mascot-eye" cx="56" cy="42" r="5" />
        <circle className="robot-mascot-cheek" cx="33" cy="49" r="3" />
        <circle className="robot-mascot-cheek" cx="63" cy="49" r="3" />
      </g>
      <g className="robot-mascot-body-group">
        <rect className="robot-mascot-body" x="29" y="64" width="38" height="29" rx="12" />
        <path className="robot-mascot-panel-line" d="M39 75h18" />
        <circle className="robot-mascot-core" cx="48" cy="84" r="4" />
      </g>
      <g className={leftArmClass}>
        <path
          className="robot-mascot-arm robot-mascot-arm-left"
          d={pointingLeft ? 'M30 73c-9-2-16-8-22-15' : 'M29 73c-8 3-12 9-11 16'}
        />
        <path
          className="robot-mascot-hand robot-mascot-hand-left"
          d={pointingLeft ? 'M8 58l-6-4' : 'M16 89h8'}
        />
      </g>
      <g className={rightArmClass}>
        <path
          className="robot-mascot-arm robot-mascot-arm-right"
          d={pointingRight ? 'M67 73c9-2 16-8 22-15' : pose === 'wave' ? 'M67 73c9-6 15-4 18 3' : 'M67 73c8 3 12 9 11 16'}
        />
        <path
          className="robot-mascot-hand robot-mascot-hand-right"
          d={pointingRight ? 'M89 58l6-4' : pose === 'wave' ? 'M82 75l7-5' : 'M74 89h8'}
        />
      </g>
      <g className="robot-mascot-arm-swoosh robot-mascot-arm-swoosh-left">
        <path d="M12 48l-7-3" />
        <path d="M16 42l-5-6" />
      </g>
      <g className="robot-mascot-arm-swoosh robot-mascot-arm-swoosh-right">
        <path d="M84 48l7-3" />
        <path d="M80 42l5-6" />
      </g>
      <path className="robot-mascot-leg" d="M39 93v9" />
      <path className="robot-mascot-leg" d="M57 93v9" />
    </svg>
  );
}
