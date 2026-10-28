# Recorder results — October 2, 2026

Finishing a recording now opens **Run results**, even offline. The screen shows
the number of identified robots, saved positions, analyzed frames, observed match
clock ranges, and a position heatmap for each robot. It explains what the recording
measures and what it cannot measure: movement estimates do not measure fuel or
cycle times, and observations spanning two times do not imply continuous coverage.

**Saved runs on this device**, under Scouting → On-Device Breakdown, reopens both
waiting and synced runs. My Team's waiting recordings also link to their results.
Workspace changes hide runs belonging to the previous workspace immediately.

Both immediate sync and automatic reconnect sync now retain the server's analysis
in IndexedDB alongside the original tracks. Reopening a run can show its offense,
defense, shift heatmaps, and review/quality status **at last sync**. Provisional
runs explicitly say that operator acceptance is still required. Unassessable
defense and empty shift heatmaps stay unknown rather than implying zero performance.

Older saved runs remain compatible and can show position heatmaps from their
original payload. The old app did not retain server analysis, so those older runs
may have no saved offense/defense estimates. Opening results never uploads a run.

Validation: 497 frontend tests; full ESLint, colour/type guards, TypeScript, and
production build. Eight new tests cover reopening, cached analysis, reconnect
updates, workspace isolation, old runs, deep links, missing analysis, and coordinate
binning. Browser checks used synthetic runs in an isolated test browser against
the dev server and built app at desktop and 390 px phone sizes. The actual canvas
heatmaps rendered, results survived a document reload, the title remained visible
below sticky navigation, and no horizontal overflow or recorder errors appeared. The standalone preview has no
API proxy, so opening Home produced an HTML-as-JSON error; this is a preview
configuration limitation, outside the saved-results flow. Live verification follows
deployment against the configured API.

These checks verify results display and persistence; they do not establish new
detector accuracy or physical-phone performance. The owner approved publication and deployment on October 2.
