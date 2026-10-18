import { Link } from 'react-router-dom';
import { ScoutingViewBar } from '../components/ScoutingViewBar';
import { scoutingViewTitle } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { FieldCalibration } from '../features/onDevice/FieldCalibration';
import '../features/onDevice/OnDeviceRun.css';

export function FieldCalibrationPage() {
  return (
    <>
      <ScoutingViewBar />
      <div className="center-page-container">

          <SurfaceCard
            title={scoutingViewTitle('/scouting/calibrate')}
            subtitle="Tap the four field corners to map this camera view to field coordinates — the first step of the offline on-device match breakdown."
            expandable={false}
            mobileCollapsible={false}
          >
            <FieldCalibration />
            <p className="center-callout muted">Next: <Link to="/scouting/record">{scoutingViewTitle('/scouting/record')}</Link></p>
          </SurfaceCard>

      </div>
    </>
  );
}
