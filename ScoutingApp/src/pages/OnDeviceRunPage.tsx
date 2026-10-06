import { PageViewBar } from '../components/PageViewBar';
import { SCOUTING_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { SavedRuns } from '../features/onDevice/SavedRuns';
import { OnDeviceRun } from '../features/onDevice/OnDeviceRun';

export function OnDeviceRunPage() {
  return (
    <>
      <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />
      <div className="center-page-container narrow">

          <SurfaceCard
            title="On-Device Match Breakdown"
            subtitle="Film or upload a match, identify the robots, then view their position heatmaps. Offense and defense estimates appear after sync when enough data is available."
            expandable={false}
            mobileCollapsible={false}
          >
            <OnDeviceRun />
          </SurfaceCard>
          <SavedRuns />

      </div>
    </>
  );
}
