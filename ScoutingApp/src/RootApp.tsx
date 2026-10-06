import { isNativeApp } from './platform/runtime';
import { Suspense, lazy, useEffect, type ReactNode } from 'react';
import { HashRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ErrorBoundary } from './components/ui/ErrorBoundary';
import { PageSpinner } from './components/ui/PageSpinner';
import { ProductShell } from './layout/ProductShell';
import { prefetchRoutesWhenIdle } from './routePrefetch';
import { recorderDocumentRedirect } from './features/onDevice/recorderDocument';

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
const MyTeamPage = lazy(() => import('./pages/MyTeamPage').then((mod) => ({ default: mod.MyTeamPage })));
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

// The primitives gallery — the route guards 3 and 4 sweep to check that every
// primitive holds contrast and does not overflow on a phone. Guards run
// against the dev server, so production builds leave it out entirely: it was
// a demo page anyone could open at /#/primitives.
const PrimitivesPage = import.meta.env.DEV
  ? lazy(() => import('./pages/PrimitivesPage').then((mod) => ({ default: mod.PrimitivesPage })))
  : null;

const PrivacyPolicyPage = lazy(() =>
  import('./pages/PrivacyPolicyPage').then((mod) => ({ default: mod.PrivacyPolicyPage })),
);
const TermsOfServicePage = lazy(() =>
  import('./pages/TermsOfServicePage').then((mod) => ({ default: mod.TermsOfServicePage })),
);

function withPageSuspense(content: ReactNode, label?: string) {
  return (
    <ErrorBoundary label={label}>
      <Suspense fallback={<PageSpinner />}>{content}</Suspense>
    </ErrorBoundary>
  );
}

// Hops between index.html and the isolated recorder document (record.html) when the
// route crosses between the recorder and the rest of the app.
// Decided during render, not after: the route below would otherwise start loading the
// recorder on the wrong document, and the redirect cancels those loads mid-flight (the
// error boundary then reports "Unable to preload CSS" on the page being left).
function RecorderDocumentGuard({ children }: { children: ReactNode }) {
  const location = useLocation();
  const target = recorderDocumentRedirect(window.location.pathname, location.pathname, location.search, {
    supported: !isNativeApp() && typeof window.crossOriginIsolated === 'boolean',
    active: window.crossOriginIsolated === true,
  });
  useEffect(() => {
    if (target) window.location.replace(target);
  }, [target]);
  return target ? null : children;
}

// Old addresses still turn up in bookmarks and shared links; keep their ?event=/&team= so the
// redirect lands on the same selection.
function RedirectKeepingQuery({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

export default function RootApp() {
  useEffect(() => {
    prefetchRoutesWhenIdle();
  }, []);
  return (
    <HashRouter>
      <RecorderDocumentGuard>
        <Routes>
          <Route path="/event-center" element={<Navigate to="/home" replace />} />
          <Route path="/workspace" element={<Navigate to="/home" replace />} />
          <Route element={<ProductShell />}>
            <Route path="/home" element={withPageSuspense(<HomePage />, 'Home')} />
            <Route path="/events" element={withPageSuspense(<EventsPage />, 'Events')} />
            <Route path="/events/export" element={withPageSuspense(<ExportPage />, 'Export')} />
            <Route path="/events/dashboard" element={withPageSuspense(<DataVizPage />, 'Data Dashboard')} />
            <Route path="/teams" element={<RedirectKeepingQuery to="/team-center" />} />
            <Route path="/teams-insights" element={<RedirectKeepingQuery to="/team-center" />} />
            <Route path="/my-team" element={withPageSuspense(<MyTeamPage />, 'My Team')} />
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
            <Route path="/alliance-advisor" element={<RedirectKeepingQuery to="/compare/alliance-advisor" />} />
            <Route path="/predictions" element={<RedirectKeepingQuery to="/match-center/predictions" />} />
            <Route path="/export" element={<RedirectKeepingQuery to="/events/export" />} />
            <Route path="/scouting-assignments" element={<RedirectKeepingQuery to="/scouting/assignments" />} />
            <Route path="/favorites" element={withPageSuspense(<FavoritesPage />, 'Favorites')} />
            <Route path="/settings" element={withPageSuspense(<SettingsPage />, 'Settings')} />
            {PrimitivesPage ? (
              <Route path="/primitives" element={withPageSuspense(<PrimitivesPage />, 'Primitives')} />
            ) : null}
            <Route path="/privacy" element={withPageSuspense(<PrivacyPolicyPage />, 'Privacy Policy')} />
            <Route path="/terms" element={withPageSuspense(<TermsOfServicePage />, 'Terms of Service')} />
            <Route path="/privacy-policy" element={<Navigate to="/privacy" replace />} />
            <Route path="/terms-of-service" element={<Navigate to="/terms" replace />} />
          </Route>
          <Route path="*" element={<Navigate to="/home" replace />} />
        </Routes>
      </RecorderDocumentGuard>
    </HashRouter>
  );
}
