import { PageViewBar } from './PageViewBar';
import { SCOUTING_VIEWS } from './pageViewBarConfig';

// Every scouting page opens with this one menu, in the same form: tabs on wide screens, one
// dropdown on phones. Live Scouting used to render its own copy as a sideways-scrolling chip row
// while the other six showed the dropdown, so the same menu looked different depending on which
// page you were on.
export function ScoutingViewBar() {
  return <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" />;
}
