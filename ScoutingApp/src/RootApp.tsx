import { Suspense, lazy, type ReactNode } from 'react';
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ErrorBoundary } from './components/ui/ErrorBoundary';
import { PageSpinner } from './components/ui/PageSpinner';
import { ProductShell } from './layout/ProductShell';

const HomePage = lazy(() => import('./pages/HomePage').then((mod) => ({ default: mod.HomePage })));
const EventsPage = lazy(() => import('./pages/EventsPage').then((mod) => ({ default: mod.EventsPage })));
const MatchCenterPage = lazy(() =>
  import('./pages/MatchCenterPage').then((mod) => ({ default: mod.MatchCenterPage })),
);
const TeamCenterPage = lazy(() => import('./pages/TeamCenterPage').then((mod) => ({ default: mod.TeamCenterPage })));
const ComparePage = lazy(() => import('./pages/ComparePage').then((mod) => ({ default: mod.ComparePage })));
const FavoritesPage = lazy(() => import('./pages/FavoritesPage').then((mod) => ({ default: mod.FavoritesPage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((mod) => ({ default: mod.SettingsPage })));
const ScoutingPage = lazy(() => import('./pages/ScoutingPage').then((mod) => ({ default: mod.ScoutingPage })));
const AllianceAdvisorPage = lazy(() =>
  import('./pages/AllianceAdvisorPage').then((mod) => ({ default: mod.AllianceAdvisorPage })),
);
const MatchPredictionPage = lazy(() =>
  import('./pages/MatchPredictionPage').then((mod) => ({ default: mod.MatchPredictionPage })),
);
const ExportPage = lazy(() =>
  import('./pages/ExportPage').then((mod) => ({ default: mod.ExportPage })),
);
const ScoutingAssignPage = lazy(() =>
  import('./pages/ScoutingAssignPage').then((mod) => ({ default: mod.ScoutingAssignPage })),
);
const StrategyBriefingPage = lazy(() =>
  import('./pages/StrategyBriefingPage').then((mod) => ({ default: mod.StrategyBriefingPage })),
);
const DataVizPage = lazy(() =>
  import('./pages/DataVizPage').then((mod) => ({ default: mod.DataVizPage })),
);
const AutoPathPage = lazy(() =>
  import('./pages/AutoPathPage').then((mod) => ({ default: mod.AutoPathPage })),
);
const PicklistPage = lazy(() =>
  import('./pages/PicklistPage').then((mod) => ({ default: mod.PicklistPage })),
);
const PitScoutingPage = lazy(() =>
  import('./pages/PitScoutingPage').then((mod) => ({ default: mod.PitScoutingPage })),
);
const ScoutingCoveragePage = lazy(() =>
  import('./pages/ScoutingCoveragePage').then((mod) => ({ default: mod.ScoutingCoveragePage })),
);
const FieldCalibrationPage = lazy(() =>
  import('./pages/FieldCalibrationPage').then((mod) => ({ default: mod.FieldCalibrationPage })),
);
const OnDeviceRunPage = lazy(() =>
  import('./pages/OnDeviceRunPage').then((mod) => ({ default: mod.OnDeviceRunPage })),
);

function withPageSuspense(content: ReactNode, label?: string) {
  return (
    <ErrorBoundary label={label}>
      <Suspense fallback={<PageSpinner />}>{content}</Suspense>
    </ErrorBoundary>
  );
}

export default function RootApp() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/event-center" element={<Navigate to="/home" replace />} />
        <Route path="/workspace" element={<Navigate to="/home" replace />} />
        <Route element={<ProductShell />}>
          <Route path="/home" element={withPageSuspense(<HomePage />, 'Home')} />
          <Route path="/events" element={withPageSuspense(<EventsPage />, 'Events')} />
          <Route path="/events/export" element={withPageSuspense(<ExportPage />, 'Export')} />
          <Route path="/events/dashboard" element={withPageSuspense(<DataVizPage />, 'Data Dashboard')} />
          <Route path="/teams" element={<Navigate to="/team-center" replace />} />
          <Route path="/teams-insights" element={<Navigate to="/team-center" replace />} />
          <Route path="/scouting" element={withPageSuspense(<ScoutingPage />, 'Scouting')} />
          <Route path="/scouting/assignments" element={withPageSuspense(<ScoutingAssignPage />, 'Scouting Assignments')} />
          <Route path="/scouting/auto-paths" element={withPageSuspense(<AutoPathPage />, 'Auto Paths')} />
          <Route path="/scouting/pit" element={withPageSuspense(<PitScoutingPage />, 'Pit Scouting')} />
          <Route path="/scouting/coverage" element={withPageSuspense(<ScoutingCoveragePage />, 'Coverage')} />
          <Route path="/scouting/calibrate" element={withPageSuspense(<FieldCalibrationPage />, 'Field Calibration')} />
          <Route path="/scouting/record" element={withPageSuspense(<OnDeviceRunPage />, 'On-Device Breakdown')} />
          <Route path="/match-center" element={withPageSuspense(<MatchCenterPage />, 'Match Center')} />
          <Route path="/match-center/predictions" element={withPageSuspense(<MatchPredictionPage />, 'Predictions')} />
          <Route path="/match-center/strategy" element={withPageSuspense(<StrategyBriefingPage />, 'Strategy')} />
          <Route path="/team-center" element={withPageSuspense(<TeamCenterPage />, 'Team Center')} />
          <Route path="/compare" element={withPageSuspense(<ComparePage />, 'Compare')} />
          <Route path="/compare/alliance-advisor" element={withPageSuspense(<AllianceAdvisorPage />, 'Alliance Advisor')} />
          <Route path="/compare/picklist" element={withPageSuspense(<PicklistPage />, 'Picklist')} />
          {/* Legacy standalone routes → redirect to new sub-paths */}
          <Route path="/alliance-advisor" element={<Navigate to="/compare/alliance-advisor" replace />} />
          <Route path="/predictions" element={<Navigate to="/match-center/predictions" replace />} />
          <Route path="/export" element={<Navigate to="/events/export" replace />} />
          <Route path="/scouting-assignments" element={<Navigate to="/scouting/assignments" replace />} />
          <Route path="/favorites" element={withPageSuspense(<FavoritesPage />, 'Favorites')} />
          <Route path="/settings" element={withPageSuspense(<SettingsPage />, 'Settings')} />
        </Route>
        <Route path="*" element={<Navigate to="/home" replace />} />
      </Routes>
    </HashRouter>
  );
}
