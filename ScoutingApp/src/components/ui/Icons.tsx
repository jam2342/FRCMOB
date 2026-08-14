/**
 * Centralized SVG icon library for the mobile UI.
 * All icons use a consistent outlined style: currentColor strokes, no fill, 24×24 viewBox.
 * Size is controlled by the parent container (typically 14–20px via CSS).
 */
import './Icons.css';

const defaults = {
  viewBox: '0 0 24 24',
  'aria-hidden': true as const,
  focusable: 'false' as const,
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

function I({ children, className, size }: { children: React.ReactNode; className?: string; size?: number }) {
  return (
    <svg
      {...defaults}
      className={className}
      width={size}
      height={size}
      style={size ? undefined : { width: '1em', height: '1em' }}
    >
      {children}
    </svg>
  );
}

/* ── Navigation & Layout ────────────────────────────────────── */

export function GridIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </I>
  );
}

export function ListIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <circle cx="4" cy="6" r="1" fill="currentColor" stroke="none" />
      <circle cx="4" cy="12" r="1" fill="currentColor" stroke="none" />
      <circle cx="4" cy="18" r="1" fill="currentColor" stroke="none" />
    </I>
  );
}

export function SearchIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="11" cy="11" r="7" />
      <line x1="16.5" y1="16.5" x2="21" y2="21" />
    </I>
  );
}

export function ChevronLeftIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polyline points="15 18 9 12 15 6" />
    </I>
  );
}

export function ChevronRightIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polyline points="9 6 15 12 9 18" />
    </I>
  );
}

export function ChevronDownIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polyline points="6 9 12 15 18 9" />
    </I>
  );
}

export function ChevronUpIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polyline points="18 15 12 9 6 15" />
    </I>
  );
}

export function ExternalLinkIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </I>
  );
}

/* ── Status & State ─────────────────────────────────────────── */

export function LiveDotIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="12" cy="12" r="4" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="8" strokeOpacity={0.4} />
    </I>
  );
}

export function ClockIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </I>
  );
}

export function CheckCircleIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M22 11.08V12a10 10 0 11-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </I>
  );
}

export function AlertTriangleIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <circle cx="12" cy="17" r="0.5" fill="currentColor" stroke="none" />
    </I>
  );
}

export function InfoIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <circle cx="12" cy="8" r="0.5" fill="currentColor" stroke="none" />
    </I>
  );
}

export function WifiIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M5 12.55a11 11 0 0114 0" />
      <path d="M1.42 9a16 16 0 0121.16 0" />
      <path d="M8.53 16.11a6 6 0 016.95 0" />
      <circle cx="12" cy="20" r="1" fill="currentColor" stroke="none" />
    </I>
  );
}

export function WifiOffIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <line x1="1" y1="1" x2="23" y2="23" />
      <path d="M16.72 11.06A10.94 10.94 0 0119 12.55" />
      <path d="M5 12.55a10.94 10.94 0 015.17-2.39" />
      <path d="M10.71 5.05A16 16 0 0122.56 9" />
      <path d="M1.42 9a15.91 15.91 0 014.7-2.88" />
      <path d="M8.53 16.11a6 6 0 016.95 0" />
      <circle cx="12" cy="20" r="1" fill="currentColor" stroke="none" />
    </I>
  );
}

export function RefreshIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
    </I>
  );
}

/* ── Content Types ──────────────────────────────────────────── */

export function CalendarIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="3" y1="10" x2="21" y2="10" />
    </I>
  );
}

export function UsersIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 00-3-3.87" />
      <path d="M16 3.13a4 4 0 010 7.75" />
    </I>
  );
}

export function TrophyIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M6 9H4a2 2 0 01-2-2V5a2 2 0 012-2h2" />
      <path d="M18 9h2a2 2 0 002-2V5a2 2 0 00-2-2h-2" />
      <path d="M6 3h12v7a6 6 0 01-12 0V3z" />
      <path d="M9 21h6" />
      <path d="M12 16v5" />
    </I>
  );
}

export function StarIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </I>
  );
}

export function TargetIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="6" />
      <circle cx="12" cy="12" r="2" />
    </I>
  );
}

export function TagIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z" />
      <circle cx="7" cy="7" r="1" fill="currentColor" stroke="none" />
    </I>
  );
}

export function ImageIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="8.5" cy="8.5" r="1.5" />
      <polyline points="21 15 16 10 5 21" />
    </I>
  );
}

export function CodeIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </I>
  );
}

export function LinkIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71" />
    </I>
  );
}

/* ── Actions ────────────────────────────────────────────────── */

export function PlayIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polygon points="5 3 19 12 5 21 5 3" fill="currentColor" stroke="none" />
    </I>
  );
}

export function PauseIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none" />
      <rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none" />
    </I>
  );
}

export function ResetIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polyline points="1 4 1 10 7 10" />
      <path d="M3.51 15a9 9 0 102.13-9.36L1 10" />
    </I>
  );
}

export function SaveIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" />
      <polyline points="17 21 17 13 7 13 7 21" />
      <polyline points="7 3 7 8 15 8" />
    </I>
  );
}

export function TrashIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
    </I>
  );
}

export function DownloadIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </I>
  );
}

export function CopyIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
    </I>
  );
}

export function QrCodeIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="4" height="4" rx="0.5" />
      <line x1="21" y1="14" x2="21" y2="17" />
      <line x1="14" y1="21" x2="17" y2="21" />
    </I>
  );
}

export function LogOutIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </I>
  );
}

/* ── Data & Metrics ─────────────────────────────────────────── */

export function BarChartIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
    </I>
  );
}

export function PieChartIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M21.21 15.89A10 10 0 118 2.83" />
      <path d="M22 12A10 10 0 0012 2v10z" />
    </I>
  );
}

export function GaugeIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z" />
      <path d="M12 6v6l4 2" />
    </I>
  );
}

export function HashIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <line x1="4" y1="9" x2="20" y2="9" />
      <line x1="4" y1="15" x2="20" y2="15" />
      <line x1="10" y1="3" x2="8" y2="21" />
      <line x1="16" y1="3" x2="14" y2="21" />
    </I>
  );
}

/* ── Scouting & FRC Specific ────────────────────────────────── */

export function RobotIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <rect x="5" y="8" width="14" height="12" rx="2" />
      <circle cx="9" cy="14" r="1.5" />
      <circle cx="15" cy="14" r="1.5" />
      <line x1="9" y1="18" x2="15" y2="18" />
      <line x1="12" y1="4" x2="12" y2="8" />
      <circle cx="12" cy="3" r="1" />
    </I>
  );
}

export function GamepadIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <line x1="6" y1="12" x2="10" y2="12" />
      <line x1="8" y1="10" x2="8" y2="14" />
      <circle cx="15" cy="11" r="1" fill="currentColor" stroke="none" />
      <circle cx="18" cy="13" r="1" fill="currentColor" stroke="none" />
      <path d="M17.32 5H6.68a4 4 0 00-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 003 3c1.296 0 1.998-.804 2.604-1.604l.71-.888A2 2 0 019.87 16h4.26a2 2 0 011.557.508l.71.888C17.002 18.196 17.704 19 19 19a3 3 0 003-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.152A4 4 0 0017.32 5z" />
    </I>
  );
}

export function FlagIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
      <line x1="4" y1="22" x2="4" y2="15" />
    </I>
  );
}

export function ShieldIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </I>
  );
}

export function ShieldCheckIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <polyline points="9 12 11 14 15 10" />
    </I>
  );
}

export function ZapIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </I>
  );
}

export function FlameIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M8.5 14.5A2.5 2.5 0 0011 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 11-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 002.5 2.5z" />
    </I>
  );
}

export function MountainIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M8 3l4 8 5-5 5 16H2L8 3z" />
    </I>
  );
}

export function StopwatchIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="12" cy="13" r="8" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="14.5" y1="11.5" x2="12" y2="13" />
      <line x1="10" y1="2" x2="14" y2="2" />
      <line x1="12" y1="2" x2="12" y2="5" />
    </I>
  );
}

export function ClipboardIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2" />
      <rect x="8" y="2" width="8" height="4" rx="1" />
    </I>
  );
}

export function ClipboardCheckIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2" />
      <rect x="8" y="2" width="8" height="4" rx="1" />
      <polyline points="9 14 11 16 15 12" />
    </I>
  );
}

export function PenIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z" />
    </I>
  );
}

export function CameraIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z" />
      <circle cx="12" cy="13" r="4" />
    </I>
  );
}

export function VideoIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <polygon points="23 7 16 12 23 17 23 7" />
      <rect x="1" y="5" width="15" height="14" rx="2" />
    </I>
  );
}

export function GlobeIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
    </I>
  );
}

export function PuzzleIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M20 17v-2a2 2 0 00-2-2h-1a2.5 2.5 0 010-5h1a2 2 0 002-2V4a2 2 0 00-2-2h-2a2.5 2.5 0 01-5 0H9a2 2 0 00-2 2v1a2.5 2.5 0 010 5V8a2 2 0 00-2 2v2a2.5 2.5 0 000 5v1a2 2 0 002 2h10a2 2 0 002-2z" />
    </I>
  );
}

export function SteeringWheelIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="2" />
      <path d="M12 14v8" />
      <path d="M5.63 7.5L11 11" />
      <path d="M18.37 7.5L13 11" />
    </I>
  );
}

export function EyeIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </I>
  );
}

export function SettingsIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
    </I>
  );
}

export function MapPinIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z" />
      <circle cx="12" cy="10" r="3" />
    </I>
  );
}

export function CloudSyncIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z" />
    </I>
  );
}

export function ScoreboardIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <rect x="2" y="5" width="20" height="14" rx="2.5" />
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="2" y1="12" x2="22" y2="12" />
    </I>
  );
}

export function HandshakeIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M20 17l-3.5-7L14 6l-2 3-2-3-2.5 4L4 17" />
      <path d="M12 22c-1 0-3-2.5-3-5 0-1.5.5-3 3-3s3 1.5 3 3c0 2.5-2 5-3 5z" />
      <line x1="4" y1="17" x2="8" y2="13" />
      <line x1="20" y1="17" x2="16" y2="13" />
    </I>
  );
}

export function BracketIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M3 5v14" />
      <path d="M3 5h4v4H3" />
      <path d="M3 15h4v4H3" />
      <path d="M7 7h6v2" />
      <path d="M7 17h6v-2" />
      <path d="M13 9h3v6h-3" />
      <path d="M16 12h5" />
    </I>
  );
}

export function AwardIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <circle cx="12" cy="8" r="6" />
      <path d="M15.477 12.89L17 22l-5-3-5 3 1.523-9.11" />
    </I>
  );
}

export function DeltaIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <path d="M12 3L2 21h20L12 3z" />
    </I>
  );
}

export function SignalIcon({ className, size }: { className?: string; size?: number }) {
  return (
    <I className={className} size={size}>
      <line x1="6" y1="18" x2="6" y2="15" />
      <line x1="10" y1="18" x2="10" y2="11" />
      <line x1="14" y1="18" x2="14" y2="7" />
      <line x1="18" y1="18" x2="18" y2="3" />
    </I>
  );
}
