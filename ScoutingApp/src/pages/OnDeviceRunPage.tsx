import { PageViewBar } from '../components/PageViewBar';
import { SCOUTING_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { OnDeviceRun } from '../features/onDevice/OnDeviceRun';

export function OnDeviceRunPage() {
  return (
    <>
      <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />
      <div className="center-page-container narrow">
        <SurfaceCardGroup>
          <SurfaceCard
            title="On-Device Match Breakdown"
            subtitle="Record a match on your phone and break it down offline — detect, track, identify, and score offense/defense on-device, then sync to the central dataset."
            expandable={false}
            mobileCollapsible={false}
          >
            <OnDeviceRun />
          </SurfaceCard>
        </SurfaceCardGroup>
      </div>
    </>
  );
}
