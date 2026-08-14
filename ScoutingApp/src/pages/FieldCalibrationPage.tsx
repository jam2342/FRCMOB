import { PageViewBar } from '../components/PageViewBar';
import { SCOUTING_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { FieldCalibration } from '../features/onDevice/FieldCalibration';
import '../features/onDevice/OnDeviceRun.css';

export function FieldCalibrationPage() {
  return (
    <>
      <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />
      <div className="center-page-container narrow">
        <SurfaceCardGroup>
          <SurfaceCard
            title="Field Calibration"
            subtitle="Tap the four field corners to map this camera view to field coordinates — the first step of the offline on-device match breakdown."
            expandable={false}
            mobileCollapsible={false}
          >
            <FieldCalibration />
          </SurfaceCard>
        </SurfaceCardGroup>
      </div>
    </>
  );
}
