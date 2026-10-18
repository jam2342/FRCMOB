import type { ReactNode } from 'react';
import { AlertTriangleIcon, WifiOffIcon } from './Icons';
import { RobotMascot } from './RobotMascot';
import './EmptyState.css';

export interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
  type?: 'default' | 'error' | 'offline';
  /** The waving robot, for "nothing here yet" screens (not errors or loading). */
  mascot?: boolean;
}

export function EmptyState({ icon, title, description, action, compact, className, type = 'default', mascot = false }: EmptyStateProps) {
  let defaultIcon = icon;
  if (!icon) {
    if (type === 'error') defaultIcon = <AlertTriangleIcon />;
    if (type === 'offline') defaultIcon = <WifiOffIcon />;
  }

  return (
    <div className={`es es--${type} ${compact ? 'es--compact' : ''} ${className || ''}`}>
      {mascot ? <RobotMascot size={compact ? 64 : 88} /> : defaultIcon && <div className="es__icon">{defaultIcon}</div>}
      <h3 className="es__title">{title}</h3>
      {description && <p className="es__desc">{description}</p>}
      {action && <div className="es__action">{action}</div>}
    </div>
  );
}
