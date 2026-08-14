import './TutorialRobotMascot.css';

type TutorialRobotMascotProps = {
  className?: string;
  animated?: boolean;
  pose?: 'wave' | 'point-left' | 'point-right';
  talking?: boolean;
};

export function TutorialRobotMascot({
  className,
  animated = true,
  pose = 'wave',
  talking = false,
}: TutorialRobotMascotProps) {
  const classes = [
    'tut-robot',
    animated && 'is-animated',
    talking && 'is-talking',
    `is-${pose}`,
    className,
  ].filter(Boolean).join(' ');
  const pointingLeft = pose === 'point-left';
  const pointingRight = pose === 'point-right';
  const leftArmClass = [
    'tut-robot-arm-group',
    'tut-robot-arm-group-left',
    pointingLeft && 'is-pointing',
  ].filter(Boolean).join(' ');
  const rightArmClass = [
    'tut-robot-arm-group',
    'tut-robot-arm-group-right',
    pose === 'wave' && 'is-waving',
    pointingRight && 'is-pointing',
  ].filter(Boolean).join(' ');

  return (
    <svg className={classes} viewBox="0 0 96 112" aria-hidden="true" focusable="false">
      <path className="tut-robot-shadow" d="M25 101c8 5 38 5 46 0 3-2 3-5 0-7-8-5-38-5-46 0-3 2-3 5 0 7z" />
      <g className="tut-robot-talk-marks tut-robot-talk-marks-left">
        <path d="M15 35l-8-4" />
        <path d="M18 25l-5-8" />
      </g>
      <g className="tut-robot-talk-marks tut-robot-talk-marks-right">
        <path d="M81 35l8-4" />
        <path d="M78 25l5-8" />
      </g>
      <path className="tut-robot-antenna" d="M48 22V11" />
      <circle className="tut-robot-antenna-light" cx="48" cy="8" r="5" />
      <g className="tut-robot-head-group">
        <rect className="tut-robot-head" x="21" y="20" width="54" height="46" rx="18" />
        <rect className="tut-robot-face" x="28" y="29" width="40" height="26" rx="13" />
        <circle className="tut-robot-eye" cx="40" cy="42" r="5" />
        <circle className="tut-robot-eye" cx="56" cy="42" r="5" />
        <circle className="tut-robot-cheek" cx="33" cy="49" r="3" />
        <circle className="tut-robot-cheek" cx="63" cy="49" r="3" />
      </g>
      <g className="tut-robot-body-group">
        <rect className="tut-robot-body" x="29" y="64" width="38" height="29" rx="12" />
        <path className="tut-robot-panel-line" d="M39 75h18" />
        <circle className="tut-robot-core" cx="48" cy="84" r="4" />
      </g>
      <g className={leftArmClass}>
        <path
          className="tut-robot-arm tut-robot-arm-left"
          d={pointingLeft ? 'M30 73c-9-2-16-8-22-15' : 'M29 73c-8 3-12 9-11 16'}
        />
        <path
          className="tut-robot-hand tut-robot-hand-left"
          d={pointingLeft ? 'M8 58l-6-4' : 'M16 89h8'}
        />
      </g>
      <g className={rightArmClass}>
        <path
          className="tut-robot-arm tut-robot-arm-right"
          d={pointingRight ? 'M67 73c9-2 16-8 22-15' : pose === 'wave' ? 'M67 73c9-6 15-4 18 3' : 'M67 73c8 3 12 9 11 16'}
        />
        <path
          className="tut-robot-hand tut-robot-hand-right"
          d={pointingRight ? 'M89 58l6-4' : pose === 'wave' ? 'M82 75l7-5' : 'M74 89h8'}
        />
      </g>
      <g className="tut-robot-arm-swoosh tut-robot-arm-swoosh-left">
        <path d="M12 48l-7-3" />
        <path d="M16 42l-5-6" />
      </g>
      <g className="tut-robot-arm-swoosh tut-robot-arm-swoosh-right">
        <path d="M84 48l7-3" />
        <path d="M80 42l5-6" />
      </g>
      <path className="tut-robot-leg" d="M39 93v9" />
      <path className="tut-robot-leg" d="M57 93v9" />
    </svg>
  );
}
