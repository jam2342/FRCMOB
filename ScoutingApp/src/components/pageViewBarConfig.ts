import type { ViewBarItem } from './PageViewBar';

export const SCOUTING_VIEWS: ViewBarItem[] = [
  { label: 'Live Scouting', to: '/scouting', preserveSearch: true },
  { label: 'Pit Scouting', to: '/scouting/pit', preserveSearch: true },
  { label: 'Assignments', to: '/scouting/assignments', preserveSearch: true },
  { label: 'Coverage', to: '/scouting/coverage', preserveSearch: true },
  { label: 'Auto Paths', to: '/scouting/auto-paths', preserveSearch: true },
  { label: 'Field Calibration', to: '/scouting/calibrate', preserveSearch: true },
  { label: 'On-Device Breakdown', to: '/scouting/record', preserveSearch: true },
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
  { label: 'Match Hub', to: '/match-center' },
  { label: 'Predictions', to: '/match-center/predictions' },
  { label: 'Strategy', to: '/match-center/strategy' },
];
