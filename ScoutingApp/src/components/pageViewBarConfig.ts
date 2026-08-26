import type { ViewBarItem } from './PageViewBar';

export const SCOUTING_VIEWS: ViewBarItem[] = [
  { label: 'Live Scouting', to: '/scouting', preserveSearch: true },
  { label: 'Pit Scouting', to: '/scouting/pit', preserveSearch: true },
  { label: 'Assignments', to: '/scouting/assignments', preserveSearch: true },
  { label: 'Coverage', to: '/scouting/coverage', preserveSearch: true },
  { label: 'Auto Paths', to: '/scouting/auto-paths', preserveSearch: true },
  // Tools, not peer views: one calibrates a camera and one runs an offline
  // breakdown on a phone. They sat beside Live Scouting as equals.
  { label: 'Field Calibration', to: '/scouting/calibrate', preserveSearch: true, secondary: true },
  { label: 'On-Device Breakdown', to: '/scouting/record', preserveSearch: true, secondary: true },
];

export const EVENTS_VIEWS: ViewBarItem[] = [
  { label: 'Events', to: '/events' },
  { label: 'Export', to: '/events/export' },
  { label: 'Dashboard', to: '/events/dashboard' },
];

export const COMPARE_VIEWS: ViewBarItem[] = [
  { label: 'Compare', to: '/compare' },
  { label: 'Alliance Advisor', to: '/compare/alliance-advisor' },
  { label: 'Picklist', to: '/compare/picklist' },
];

export const MATCH_HUB_VIEWS: ViewBarItem[] = [
  // "Match Hub" was a synonym for the section the sidebar calls "Match Center",
  // so the same page had two names depending on where you clicked. Every other
  // family names the landing page for what it shows, not for its section.
  { label: 'Match', to: '/match-center' },
  { label: 'Predictions', to: '/match-center/predictions' },
  { label: 'Strategy', to: '/match-center/strategy' },
];
