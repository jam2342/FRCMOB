import { ScoutingViewBar } from '../components/ScoutingViewBar';
import { scoutingViewTitle } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { SavedRuns } from '../features/onDevice/SavedRuns';
import { OnDeviceRun } from '../features/onDevice/OnDeviceRun';

export function OnDeviceRunPage() {
  return (
    <>
      <ScoutingViewBar />
      <div className="center-page-container">

          <SurfaceCard
            title={scoutingViewTitle('/scouting/record')}
            subtitle="Film or upload a match, identify the robots, then view their position heatmaps. Offense and defense estimates appear after sync when enough data is available."
            mobileCollapsible={false}
          >
            <OnDeviceRun />
          </SurfaceCard>
          <SavedRuns />

      </div>
    </>
  );
}
