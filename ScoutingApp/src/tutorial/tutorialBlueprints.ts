import type { TutorialScope } from '../layout/userSettings';

export type TutorialStepPlacement = 'auto' | 'top' | 'right' | 'bottom' | 'left';

export type TutorialWalkthroughStep = {
  id: string;
  system: string;
  title: string;
  description: string;
  actions: string[];
  tooltips: string[];
  selector?: string;
  mobileSelector?: string;
  placement?: TutorialStepPlacement;
};

type TutorialChecklistItem = {
  id: string;
  title: string;
  detail: string;
  stepId?: string;
};

export type TutorialBlueprint = {
  scope: TutorialScope;
  title: string;
  subtitle: string;
  icon: string;
  estimatedMinutes: number;
  walkthrough: TutorialWalkthroughStep[];
  checklist: TutorialChecklistItem[];
  adoptionPlaybook: string[];
};

const BLUEPRINTS: Record<TutorialScope, TutorialBlueprint> = {
  home: {
    scope: 'home',
    title: 'Home Command Workflow',
    subtitle: 'Run Home as a live operations board, not a static dashboard.',
    icon: 'HOME',
    estimatedMinutes: 8,
    walkthrough: [
      {
        id: 'home-toolbar',
        system: 'Global Context',
        title: 'Start every shift from quick context',
        description:
          'Use quick search and context strip first so every tab opens against the same event, match, and team references.',
        actions: [
          'Set an event in quick search before opening deep pages.',
          'Pin active event/team context if your shift is match-heavy.',
          'Verify stale context is cleared before handoff.',
        ],
        tooltips: [
          'Quick context drift is the top cause of mismatched scouting notes.',
          'Pinned context should be reviewed every match block.',
        ],
        selector: '[data-onboard="shell-context"], .ps-context-strip',
        mobileSelector: '.mobile-search-overlay, .ps-context-strip',
        placement: 'bottom',
      },
      {
        id: 'home-active-events',
        system: 'Event Triage',
        title: 'Use Active Events to choose your tracking target',
        description:
          'Treat the Active Events rail as triage. Prioritize events with live match count spikes or stale data warnings.',
        actions: [
          'Select event from Active Events, not from memory.',
          'Check live count and status labels before committing.',
          'Escalate stale or missing rows before match start.',
        ],
        tooltips: [
          'Event switches should be announced to all scouts immediately.',
          'Live count spikes are early warnings for queue pressure.',
        ],
        selector: '.home-fotmob-left .home-fotmob-card .home-fotmob-event-list, .home-event-item',
        mobileSelector: '.home-mobile-event-picker, .home-drawer-section',
        placement: 'right',
      },
      {
        id: 'home-live-feed',
        system: 'Match Feed',
        title: 'Validate live feed before acting on snapshots',
        description:
          'Use the live match feed as the source of truth for what is happening now, then branch into Match Center for depth.',
        actions: [
          'Spot-check live timers against expected block.',
          'Open match details directly from feed rows.',
          'Track winner and score deltas for rapid trend calls.',
        ],
        tooltips: [
          'If feed timing disagrees with field reality, trigger a manual refresh.',
          'Fast drill-down beats manual page navigation under pressure.',
        ],
        selector: '.home-match-list, .home-fotmob-feed',
        mobileSelector: '.home-drawer-section .home-match-list, .home-fotmob-feed',
        placement: 'left',
      },
      {
        id: 'home-leaderboard',
        system: 'Standings',
        title: 'Use leaderboard as a routing tool',
        description:
          'Leaderboard is for deciding what to inspect next. Click into Team Center for actual role and reliability analysis.',
        actions: [
          'Review top and mid-pack movement each block.',
          'Open teams directly from ranking rows.',
          'Tag volatility candidates for compare sessions.',
        ],
        tooltips: [
          'Do not lock pick decisions from rank alone.',
          'Unexpected rank jumps are often schedule artifacts.',
        ],
        selector: '.home-ranking-list, .home-ranking-row',
        mobileSelector: '.home-drawer-section .home-ranking-list',
        placement: 'left',
      },
      {
        id: 'home-calendar',
        system: 'Schedule Planning',
        title: 'Use calendar for future workload planning',
        description:
          'Calendar mode helps pre-stage scouting coverage for upcoming events and detect date conflicts early.',
        actions: [
          'Review next month events before travel windows.',
          'Inspect Date TBA separately so they do not disappear.',
          'Assign event owners before week rollover.',
        ],
        tooltips: [
          'Calendar planning reduces scramble when schedules drop late.',
          'Date TBA queues should be checked at least daily.',
        ],
        selector: '.home-calendar-card',
        mobileSelector: '.home-calendar-drawer, .home-calendar-event-list',
        placement: 'top',
      },
    ],
    checklist: [
      {
        id: 'home-set-context',
        title: 'Set event context before opening other tabs',
        detail: 'Ensures Events, Match Center, Team Center, and Compare stay aligned.',
        stepId: 'home-toolbar',
      },
      {
        id: 'home-triage-live-event',
        title: 'Choose active event using live count and freshness',
        detail: 'Avoids routing the team to stale event context.',
        stepId: 'home-active-events',
      },
      {
        id: 'home-open-live-match',
        title: 'Open at least one live match from Home feed',
        detail: 'Proves drill-down flow works before a high-pressure block.',
        stepId: 'home-live-feed',
      },
      {
        id: 'home-open-team-from-rankings',
        title: 'Open at least one team from leaderboard',
        detail: 'Forces rank-to-analysis flow instead of static reading.',
        stepId: 'home-leaderboard',
      },
      {
        id: 'home-calendar-review',
        title: 'Review monthly calendar and Date TBA queue',
        detail: 'Prevents missed event planning and travel surprises.',
        stepId: 'home-calendar',
      },
    ],
    adoptionPlaybook: [
      'Shift lead sets context and confirms feed freshness at start.',
      'Analysts use Home to route, not to finalize decisions.',
      'End-of-shift handoff includes current event, next match block, and open risks.',
    ],
  },
  events: {
    scope: 'events',
    title: 'Events Intelligence Workflow',
    subtitle: 'Move from event discovery to action-ready event context.',
    icon: 'EVT',
    estimatedMinutes: 10,
    walkthrough: [
      {
        id: 'events-finder',
        system: 'Discovery',
        title: 'Resolve event quickly with search + region filters',
        description:
          'Use event finder as the canonical event selector. Handle aliases and fuzzy queries through the finder workflow.',
        actions: [
          'Search by city, district, or event key.',
          'Use region filter to remove cross-country noise.',
          'Select from results list to lock context.',
        ],
        tooltips: [
          'Search should be your single source for event resolution.',
          'Region filtering improves speed when multiple events share names.',
        ],
        selector: '.center-input-row-event-search',
        mobileSelector: '.events-mobile-view-toggle, .center-input-row-event-search',
        placement: 'right',
      },
      {
        id: 'events-results',
        system: 'Selection',
        title: 'Validate selected event from result cards',
        description:
          'Read event tags and key metadata before committing. Wrong event context is expensive later in scouting.',
        actions: [
          'Check event key, year, and location label.',
          'Use type tags to confirm district/regional context.',
          'Open selected event with one click.',
        ],
        tooltips: [
          'If the event key looks right but tags look wrong, recheck.',
          'Do not trust browser history over visible finder cards.',
        ],
        selector: '.events-finder-list',
        mobileSelector: '.events-finder-list',
        placement: 'right',
      },
      {
        id: 'events-calendar',
        system: 'Planning',
        title: 'Use month planner for upcoming workload',
        description:
          'Calendar is your pre-scout planning surface for future events and contingency slots.',
        actions: [
          'Move month-by-month for upcoming event blocks.',
          'Select event directly from calendar list.',
          'Track Date TBA rows as a separate queue.',
        ],
        tooltips: [
          'Date TBA events should be checked every sync cycle.',
          'Calendar mode is for planning, not match analysis.',
        ],
        selector: '.events-calendar-card',
        mobileSelector: '.events-calendar-card, .events-mobile-view-toggle',
        placement: 'left',
      },
      {
        id: 'events-tabs',
        system: 'Event Center Tabs',
        title: 'Navigate center tabs intentionally',
        description:
          'Each tab has a different decision purpose: overview, schedule, rankings, teams, and breakdown.',
        actions: [
          'Use Overview for quick health snapshot.',
          'Use Schedule for live/upcoming timing decisions.',
          'Use Rankings/Teams for trend and depth checks.',
        ],
        tooltips: [
          'Do not overuse Overview once event is active.',
          'Most tactical decisions happen in Schedule and Teams.',
        ],
        selector: '.center-main .center-tabs, .fm-tab-bar',
        mobileSelector: '.fm-tab-bar',
        placement: 'bottom',
      },
      {
        id: 'events-schedule',
        system: 'Match Operations',
        title: 'Drive into match operations from schedule',
        description:
          'Use schedule rows as the dispatch board to Match Center and Team Center.',
        actions: [
          'Open match details from schedule rows.',
          'Check status pills for live/final/upcoming split.',
          'Monitor score/winner for fast tactical adaptation.',
        ],
        tooltips: [
          'Schedule rows are faster than manual match key entry.',
          'Live status mismatch is a signal to refresh immediately.',
        ],
        selector: '.center-match-list, .center-table-wrap',
        mobileSelector: '.center-mobile-card-list',
        placement: 'top',
      },
    ],
    checklist: [
      {
        id: 'events-search-run',
        title: 'Run finder query and apply region filter',
        detail: 'Builds repeatable event selection behavior for every scout.',
        stepId: 'events-finder',
      },
      {
        id: 'events-select-event',
        title: 'Select event from result card metadata',
        detail: 'Prevents wrong-year/wrong-location mistakes.',
        stepId: 'events-results',
      },
      {
        id: 'events-calendar-check',
        title: 'Review monthly calendar and Date TBA list',
        detail: 'Ensures future workload and planning are visible.',
        stepId: 'events-calendar',
      },
      {
        id: 'events-tab-pass',
        title: 'Visit Overview, Schedule, and Teams tabs',
        detail: 'Confirms analysts understand tab responsibilities.',
        stepId: 'events-tabs',
      },
      {
        id: 'events-open-match',
        title: 'Open at least one match from schedule',
        detail: 'Validates route into Match Center under real context.',
        stepId: 'events-schedule',
      },
    ],
    adoptionPlaybook: [
      'Event scout owns event context selection and communicates key changes.',
      'Strategy lead verifies rankings + teams before compare sessions.',
      'Use schedule links for dispatch instead of manual deep links.',
    ],
  },
  scouting: {
    scope: 'scouting',
    title: 'Live Scouting Operations',
    subtitle: 'Run high-integrity live capture with room sync and role control.',
    icon: 'SCOUT',
    estimatedMinutes: 12,
    walkthrough: [
      {
        id: 'scouting-profile',
        system: 'Identity',
        title: 'Set scout profile before any room activity',
        description:
          'Room access tokens bind to scout profile. Incorrect profile means write failures and duplicate identity conflicts.',
        actions: [
          'Set scout name first.',
          'Set your team for opponent shortcuts.',
          'Avoid profile changes mid-match unless rejoining room.',
        ],
        tooltips: [
          'Profile changes require token refresh via rejoin.',
          'Shared devices should use explicit profile handoff.',
        ],
        selector: '#scout-profile-name',
        mobileSelector: '#scout-profile-name',
        placement: 'right',
      },
      {
        id: 'scouting-room-join',
        system: 'Room Sync',
        title: 'Join room using key or QR and verify connection',
        description:
          'Room join is required for shared assignments, live presence, and synchronized entry storage.',
        actions: [
          'Create or join with room key.',
          'Use Join via QR when room key is shared on another device.',
          'Confirm connected state and member count.',
        ],
        tooltips: [
          'Disconnected room means local-only saves.',
          'If token expires, rejoin before forcing retries.',
        ],
        selector: '#scout-room-key',
        mobileSelector: '#scout-room-key',
        placement: 'bottom',
      },
      {
        id: 'scouting-match-target',
        system: 'Target Selection',
        title: 'Select event, match, and team before timing starts',
        description:
          'Input order matters. Select target context first, then run timer and capture sequence.',
        actions: [
          'Pick event with event picker.',
          'Select match and target robot.',
          'Use next match/team shortcuts for pacing.',
        ],
        tooltips: [
          'Selecting team late creates partial or misattributed entries.',
          'Use scout board mode to keep flow consistent.',
        ],
        selector: '#scout-event-select, #scout-match-select, .scout-team-grid',
        mobileSelector: '.mobile-view-toggle, .scout-team-grid',
        placement: 'left',
      },
      {
        id: 'scouting-capture',
        system: 'Capture Pipeline',
        title: 'Capture in game-order and save continuously',
        description:
          'Use a consistent input order to lower variance: auto -> teleop -> endgame -> notes.',
        actions: [
          'Run timer with match start.',
          'Record scoring and penalties in sequence.',
          'Save entry and confirm sync message.',
        ],
        tooltips: [
          'A saved local entry is not guaranteed synced unless room status confirms.',
          'Short notes are better than no notes during live pressure.',
        ],
        selector: '.center-main .surface-card',
        mobileSelector: '.center-main .surface-card',
        placement: 'top',
      },
      {
        id: 'scouting-lead-controls',
        system: 'Leadership Controls',
        title: 'Use leader controls for assignment and moderation',
        description:
          'Room leaders should actively manage member roles, assignment conflicts, and stale sessions.',
        actions: [
          'Promote/demote secondary leaders as needed.',
          'Remove duplicate or inactive scout sessions.',
          'Verify assignment ownership before match start.',
        ],
        tooltips: [
          'Leader tools are for throughput and quality control, not only emergencies.',
          'Kick stale sessions before they block profile reuse.',
        ],
        selector: '.scout-profile-grid .center-actions-row, .scout-profile-chip-row',
        mobileSelector: '.scout-profile-grid',
        placement: 'left',
      },
    ],
    checklist: [
      {
        id: 'scout-profile-set',
        title: 'Set scout profile and verify team',
        detail: 'Identity correctness prevents token and attribution issues.',
        stepId: 'scouting-profile',
      },
      {
        id: 'scout-room-join',
        title: 'Join room and verify connected state',
        detail: 'Ensures shared sync and collaboration features are active.',
        stepId: 'scouting-room-join',
      },
      {
        id: 'scout-room-qr',
        title: 'Test Join via QR path once',
        detail: 'Confirms QR onboarding fallback works on your device.',
        stepId: 'scouting-room-join',
      },
      {
        id: 'scout-target-flow',
        title: 'Set event, match, and team before live timer',
        detail: 'Prevents partial data and rework after save.',
        stepId: 'scouting-match-target',
      },
      {
        id: 'scout-save-sync',
        title: 'Save one entry and confirm room sync status',
        detail: 'Verifies local + remote persistence path.',
        stepId: 'scouting-capture',
      },
      {
        id: 'scout-leader-validation',
        title: 'Leader validates role controls and member list',
        detail: 'Keeps room healthy for full match block.',
        stepId: 'scouting-lead-controls',
      },
    ],
    adoptionPlaybook: [
      'All scouts join room before first qualification block.',
      'Team lead monitors member count and leadership continuity.',
      'After each block, perform quick missing-entry sweep and patch.',
    ],
  },
  'match-center': {
    scope: 'match-center',
    title: 'Match Center Breakdown Flow',
    subtitle: 'Move from match selection to tactical conclusions quickly.',
    icon: 'MATCH',
    estimatedMinutes: 9,
    walkthrough: [
      {
        id: 'match-finder',
        system: 'Finder',
        title: 'Load event and narrow match list',
        description:
          'Use Match Finder to choose event context, then narrow to target matches with filters and sort chips.',
        actions: [
          'Set event with Event Picker.',
          'Apply filter and status sort for live operations.',
          'Use recent match chips for rapid revisit.',
        ],
        tooltips: [
          'Finder quality determines speed for every downstream action.',
          'Recent chips cut navigation time during finals.',
        ],
        selector: '.center-layout-match .center-sidebar .surface-card',
        mobileSelector: '.mobile-view-toggle, .center-layout-match .center-sidebar .surface-card',
        placement: 'right',
      },
      {
        id: 'match-list',
        system: 'Queue',
        title: 'Dispatch from match list rows',
        description:
          'Treat match list rows as dispatch controls for operations. Copy deep links for cross-device coordination.',
        actions: [
          'Open selected match from picker row.',
          'Use copy link for synchronized staff handoff.',
          'Monitor status pill changes for live transitions.',
        ],
        tooltips: [
          'Deep links reduce verbal miscommunication in crowded pits.',
          'Status mismatch should trigger refresh before decisions.',
        ],
        selector: '.match-picker-row',
        mobileSelector: '.match-picker-row',
        placement: 'right',
      },
      {
        id: 'match-hero',
        system: 'Live Context',
        title: 'Read hero summary before tab deep dives',
        description:
          'Score hero and top-line timing should be checked before interpreting breakdown numbers.',
        actions: [
          'Confirm score and status alignment.',
          'Verify alliance lineup labels.',
          'Switch tabs only after top context is valid.',
        ],
        tooltips: [
          'Wrong lineup assumptions invalidate synergy conclusions.',
          'Hero panel is your sanity check for live state.',
        ],
        selector: '.fm-score-hero, .center-main .surface-card',
        mobileSelector: '.fm-score-hero',
        placement: 'bottom',
      },
      {
        id: 'match-breakdown',
        system: 'Synergy',
        title: 'Use breakdown tab for decision-grade metrics',
        description:
          'Breakdown tab converts match context into pair synergy, confidence, and tactical signals.',
        actions: [
          'Review status, winner, and score cards first.',
          'Inspect pair breakdown by points/confidence.',
          'Flag confidence dips for follow-up.',
        ],
        tooltips: [
          'Synergy without confidence context is incomplete.',
          'Large confidence swings often indicate unstable pair behavior.',
        ],
        selector: '.center-kpi-grid, .center-table-wrap',
        mobileSelector: '.center-mobile-card-list, .center-kpi-grid',
        placement: 'top',
      },
      {
        id: 'match-team-context',
        system: 'Team Drill-Down',
        title: 'Pivot into team context from match cards',
        description:
          'Use Team Context tab to validate lineup assumptions and jump to team-level deep dives when needed.',
        actions: [
          'Open team details from lineup cards.',
          'Track live/idle forms for all six teams.',
          'Carry key findings into Compare or Team Center.',
        ],
        tooltips: [
          'Lineup context plus team trends beats isolated match numbers.',
          'Fast team pivots improve timeout decisions.',
        ],
        selector: '.center-team-card-grid',
        mobileSelector: '.center-team-card-grid',
        placement: 'left',
      },
    ],
    checklist: [
      { id: 'match-set-event', title: 'Set event and open finder list', detail: 'Establishes match context baseline.', stepId: 'match-finder' },
      { id: 'match-open-row', title: 'Open match from row and copy deep link', detail: 'Validates dispatch + coordination path.', stepId: 'match-list' },
      { id: 'match-hero-validate', title: 'Validate score hero before analysis', detail: 'Prevents incorrect tactical assumptions.', stepId: 'match-hero' },
      { id: 'match-breakdown-pass', title: 'Review breakdown metrics and confidence', detail: 'Builds decision-grade match understanding.', stepId: 'match-breakdown' },
      { id: 'match-team-pivot', title: 'Open at least one team from Team Context', detail: 'Closes loop between match and team analysis.', stepId: 'match-team-context' },
    ],
    adoptionPlaybook: [
      'Dispatcher owns finder and deep-link flow.',
      'Analyst validates hero context before calling tactical changes.',
      'Use breakdown + team context in sequence for reliability.',
    ],
  },
  'team-center': {
    scope: 'team-center',
    title: 'Team Center Reliability Workflow',
    subtitle: 'Turn team data into role-fit decisions with confidence.',
    icon: 'TEAM',
    estimatedMinutes: 10,
    walkthrough: [
      {
        id: 'team-finder',
        system: 'Team Lookup',
        title: 'Load team and event context from finder',
        description:
          'Start every analysis session by explicitly selecting team and event context in finder controls.',
        actions: [
          'Enter team key or team number.',
          'Set event context or auto-select from registered events.',
          'Load team and verify status chips.',
        ],
        tooltips: [
          'Team context without event can hide schedule pressure effects.',
          'Finder controls are your first reliability gate.',
        ],
        selector: '#team-center-team-input, #team-center-event-input',
        mobileSelector: '#team-center-team-input',
        placement: 'right',
      },
      {
        id: 'team-hero',
        system: 'Topline Snapshot',
        title: 'Read team hero before tab analysis',
        description:
          'Mobile and desktop hero sections provide rating, EPA, and live status signals that frame all deeper analysis.',
        actions: [
          'Confirm team identity and current event.',
          'Check rating/EPA headline values.',
          'Use hero to spot obvious drift before deep dive.',
        ],
        tooltips: [
          'Hero values are a filter, not the final decision.',
          'Mismatch between hero and detail tabs usually means stale data.',
        ],
        selector: '.fm-team-hero, .center-main .surface-card',
        mobileSelector: '.fm-team-hero',
        placement: 'bottom',
      },
      {
        id: 'team-tabs',
        system: 'Analysis Tabs',
        title: 'Use tabs by purpose, not habit',
        description:
          'Each tab is optimized for a specific analysis question: overview, events, media, and advanced.',
        actions: [
          'Start in overview for role and reliability snapshot.',
          'Use events tab for schedule and competition context.',
          'Use advanced tab only when debugging model payload.',
        ],
        tooltips: [
          'Advanced tab is diagnostic, not daily scouting workflow.',
          'Overview + Events handle most tactical decisions.',
        ],
        selector: '.center-main .center-tabs, .fm-tab-bar',
        mobileSelector: '.fm-tab-bar',
        placement: 'bottom',
      },
      {
        id: 'team-schedule-difficulty',
        system: 'Event Pressure',
        title: 'Evaluate schedule difficulty before role claims',
        description:
          'Difficulty rows reveal whether a team profile is inflated by easier opponents or hardened by difficult schedules.',
        actions: [
          'Sort by hardest/easiest match windows.',
          'Inspect partner/opponent context.',
          'Open difficult matches in Match Center for validation.',
        ],
        tooltips: [
          'Schedule pressure explains many short-term performance swings.',
          'Hard schedule plus stable output is a strong signal.',
        ],
        selector: '.center-difficulty-pill, .center-table-wrap',
        mobileSelector: '.center-mobile-card-list',
        placement: 'top',
      },
      {
        id: 'team-media-advanced',
        system: 'Evidence + Diagnostics',
        title: 'Use media and advanced payloads to close uncertain cases',
        description:
          'When metrics conflict, use images and raw payload views to resolve context before final recommendations.',
        actions: [
          'Check logo/robot media recency.',
          'Inspect model details for outlier inputs.',
          'Capture caveats in notes for strategy crew.',
        ],
        tooltips: [
          'Raw payload checks are fastest way to validate suspicious outputs.',
          'Document caveats so selection meetings stay auditable.',
        ],
        selector: '.center-media-image, .center-code-block',
        mobileSelector: '.center-code-block, .center-media-image',
        placement: 'left',
      },
    ],
    checklist: [
      { id: 'team-load-context', title: 'Load team and event from finder', detail: 'Prevents contextless analysis.', stepId: 'team-finder' },
      { id: 'team-hero-pass', title: 'Review hero rating/EPA and live status', detail: 'Creates baseline before deep analysis.', stepId: 'team-hero' },
      { id: 'team-tab-pass', title: 'Visit overview and events tabs', detail: 'Ensures both performance and context are reviewed.', stepId: 'team-tabs' },
      { id: 'team-difficulty-check', title: 'Inspect schedule difficulty and open one hard match', detail: 'Validates role claims under pressure.', stepId: 'team-schedule-difficulty' },
      { id: 'team-diagnostics-check', title: 'Use media/advanced views for one uncertain case', detail: 'Builds evidence-backed recommendations.', stepId: 'team-media-advanced' },
    ],
    adoptionPlaybook: [
      'Role recommendations require both performance and schedule context.',
      'Use Match Center pivots to validate unusual difficulty rows.',
      'Capture uncertainty notes explicitly before compare meetings.',
    ],
  },
  compare: {
    scope: 'compare',
    title: 'Compare Decision Workflow',
    subtitle: 'Convert candidate pools into clear pick recommendations.',
    icon: 'CMP',
    estimatedMinutes: 11,
    walkthrough: [
      {
        id: 'compare-controls',
        system: 'Candidate Setup',
        title: 'Set event context and candidate teams',
        description:
          'Compare starts with a clean candidate set and explicit event context. Without it, results are noisy and hard to trust.',
        actions: [
          'Set event context explicitly.',
          'Add target teams manually or via pool.',
          'Refresh compare payloads before decision session.',
        ],
        tooltips: [
          'Compare is strongest with 3-4 focused candidates.',
          'Always refresh before alliance discussions.',
        ],
        selector: '.compare-controls-card',
        mobileSelector: '.compare-controls-card',
        placement: 'right',
      },
      {
        id: 'compare-pool',
        system: 'Event Pool',
        title: 'Curate candidates from event team pool',
        description:
          'Use pool rows to add/remove teams quickly while preserving event constraints for theoretical modeling.',
        actions: [
          'Select candidates from pool list.',
          'Watch analyzed count and rating hints.',
          'Avoid mixing out-of-event teams for theoretical mode.',
        ],
        tooltips: [
          'Pool curation is faster than typing during live meetings.',
          'Out-of-event teams break theoretical assumptions.',
        ],
        selector: '.compare-pool-card',
        mobileSelector: '.compare-pool-card',
        placement: 'right',
      },
      {
        id: 'compare-tabs',
        system: 'Decision Modes',
        title: 'Switch between diagnostics and theoretical builder',
        description:
          'Diagnostics tab compares known signals; theoretical tab tests alliance compositions and weighting strategies.',
        actions: [
          'Use diagnostics for direct team comparison.',
          'Use theoretical tab for alliance what-if scoring.',
          'Keep context event stable across both.',
        ],
        tooltips: [
          'Do not change event context mid-session.',
          'Theoretical mode is only as good as candidate quality.',
        ],
        selector: '.compare-header-card .center-tabs',
        mobileSelector: '.compare-header-card .center-tabs',
        placement: 'bottom',
      },
      {
        id: 'compare-summary',
        system: 'Diagnostics',
        title: 'Read summary and model signals together',
        description:
          'Use summary cards to compare rating, confidence, strengths, risk, and TBA context in one scan.',
        actions: [
          'Review rating and confidence first.',
          'Cross-check risk and record together.',
          'Open Team Center for anomalies.',
        ],
        tooltips: [
          'High rating with low confidence is a caution signal.',
          'Risk context is mandatory before final pick calls.',
        ],
        selector: '.compare-summary-card',
        mobileSelector: '.compare-summary-card',
        placement: 'top',
      },
      {
        id: 'compare-theoretical',
        system: 'Alliance Modeling',
        title: 'Run theoretical builder for shortlist scoring',
        description:
          'Builder mode lets you test composition and weight strategies to stress-test shortlist recommendations.',
        actions: [
          'Set team slots and weight sliders.',
          'Run builder and review weighted score output.',
          'Inspect pair and selection model tables.',
        ],
        tooltips: [
          'Weight tuning should be explicit and documented.',
          'Use autofill only as a starting point, not final result.',
        ],
        selector: '.compare-theoretical-card',
        mobileSelector: '.compare-theoretical-card',
        placement: 'top',
      },
    ],
    checklist: [
      { id: 'compare-set-context', title: 'Set event context in controls', detail: 'Keeps all candidates comparable.', stepId: 'compare-controls' },
      { id: 'compare-add-candidates', title: 'Add 3-4 teams from controls/pool', detail: 'Prevents bloated comparison sessions.', stepId: 'compare-pool' },
      { id: 'compare-refresh', title: 'Run refresh before discussing picks', detail: 'Ensures latest metrics are used.', stepId: 'compare-controls' },
      { id: 'compare-diagnostics-review', title: 'Review diagnostics summary cards', detail: 'Balances rating and risk.', stepId: 'compare-summary' },
      { id: 'compare-run-theoretical', title: 'Run one theoretical builder scenario', detail: 'Stress-tests composition assumptions.', stepId: 'compare-theoretical' },
    ],
    adoptionPlaybook: [
      'Compare lead controls candidate list and context lock.',
      'Recorder captures rationale for top pick and fallback.',
      'Final shortlist must include explicit risk note per team.',
    ],
  },
  favorites: {
    scope: 'favorites',
    title: 'Favorites Watchlist Operations',
    subtitle: 'Maintain a high-signal watchlist for recurring decisions.',
    icon: 'FAV',
    estimatedMinutes: 7,
    walkthrough: [
      {
        id: 'favorites-manager',
        system: 'Watchlist Intake',
        title: 'Use Favorite Manager for intentional adds',
        description:
          'Add only teams/events with strategic value. Watchlist quality drops when every team is pinned.',
        actions: [
          'Add teams and events with explicit purpose.',
          'Set rating context event when needed.',
          'Refresh favorites after major match blocks.',
        ],
        tooltips: [
          'Watchlists should stay small and actionable.',
          'Context event keeps rating comparisons consistent.',
        ],
        selector: '.favorites-manager-card',
        mobileSelector: '.favorites-manager-card',
        placement: 'right',
      },
      {
        id: 'favorites-snapshot',
        system: 'Live Readiness',
        title: 'Use snapshot for live match awareness',
        description:
          'Snapshot card gives a quick live view across pinned events and dispatches you to match details.',
        actions: [
          'Review live match list regularly.',
          'Jump into Match Center from snapshot rows.',
          'Use live count to prioritize analyst attention.',
        ],
        tooltips: [
          'Snapshot is your fast lane during active event windows.',
          'No live rows may indicate stale event context.',
        ],
        selector: '.favorites-snapshot-card',
        mobileSelector: '.favorites-snapshot-card',
        placement: 'right',
      },
      {
        id: 'favorites-tabs',
        system: 'View Modes',
        title: 'Switch between overview, events, and teams modes',
        description:
          'Tabs separate broad monitoring from focused event/team watchlist review.',
        actions: [
          'Use overview for mixed scan.',
          'Use events tab for schedule readiness.',
          'Use teams tab for role and risk tracking.',
        ],
        tooltips: [
          'Tab discipline reduces cognitive load in high-pressure rounds.',
          'Overview is not a substitute for focused tab review.',
        ],
        selector: '.favorites-header-card .center-tabs',
        mobileSelector: '.favorites-header-card .center-tabs',
        placement: 'bottom',
      },
      {
        id: 'favorites-event-cards',
        system: 'Event Watch',
        title: 'Use event cards for schedule and status tracking',
        description:
          'Event cards surface readiness and timing context for each pinned event.',
        actions: [
          'Review event health and match readiness.',
          'Open events quickly from watchlist cards.',
          'Remove stale events once no longer strategic.',
        ],
        tooltips: [
          'Watchlist pruning is as important as adding.',
          'Old events increase noise and delay response.',
        ],
        selector: '.favorites-events-shell',
        mobileSelector: '.favorites-events-shell',
        placement: 'top',
      },
      {
        id: 'favorites-team-cards',
        system: 'Team Watch',
        title: 'Use team cards for recurring reliability checks',
        description:
          'Team cards centralize rating, confidence, warning signals, and quick pivots to Team Center.',
        actions: [
          'Review warnings and freshness states.',
          'Open team details for outliers.',
          'Remove resolved teams to keep signal high.',
        ],
        tooltips: [
          'Warnings should trigger follow-up, not passive observation.',
          'Freshness state is your first trust filter.',
        ],
        selector: '.favorites-teams-shell',
        mobileSelector: '.favorites-teams-shell',
        placement: 'top',
      },
    ],
    checklist: [
      { id: 'fav-add-event', title: 'Add one event with clear purpose', detail: 'Builds intentional watchlist behavior.', stepId: 'favorites-manager' },
      { id: 'fav-add-team', title: 'Add at least two candidate teams', detail: 'Creates a meaningful team watchlist.', stepId: 'favorites-manager' },
      { id: 'fav-open-live-match', title: 'Open one live match from snapshot', detail: 'Validates live dispatch workflow.', stepId: 'favorites-snapshot' },
      { id: 'fav-tab-review', title: 'Review overview/events/teams tabs', detail: 'Confirms correct mode usage.', stepId: 'favorites-tabs' },
      { id: 'fav-prune-list', title: 'Remove one stale favorite', detail: 'Prevents watchlist bloat and noise.', stepId: 'favorites-team-cards' },
    ],
    adoptionPlaybook: [
      'Watchlist owner curates entries each qualification block.',
      'Live snapshot is reviewed before every scouting dispatch.',
      'Stale entries are removed during end-of-shift cleanup.',
    ],
  },
  settings: {
    scope: 'settings',
    title: 'Settings and Governance',
    subtitle: 'Configure runtime defaults, security, and diagnostics intentionally.',
    icon: 'CFG',
    estimatedMinutes: 9,
    walkthrough: [
      {
        id: 'settings-appearance',
        system: 'UI Baseline',
        title: 'Set appearance defaults for your crew',
        description:
          'Theme and density are operational settings. Use compact mode for shared displays and crowded pit workflows.',
        actions: [
          'Pick theme and density intentionally.',
          'Verify immediate application on current page.',
          'Keep defaults consistent across shared devices.',
        ],
        tooltips: [
          'UI consistency reduces onboarding friction for new scouts.',
          'Compact mode improves information density in live operations.',
        ],
        selector: '.settings-appearance-card',
        mobileSelector: '.settings-appearance-card',
        placement: 'right',
      },
      {
        id: 'settings-workflow',
        system: 'Workflow Defaults',
        title: 'Configure quick search and tutorial behavior',
        description:
          'Workflow defaults control navigation speed and onboarding consistency for the full team.',
        actions: [
          'Set quick search mode and region defaults.',
          'Set tutorial autoplay policy for device role.',
          'Reset tutorial progress when onboarding new scouts.',
        ],
        tooltips: [
          'Autoplay enabled is best for training devices.',
          'Reset tutorial progress before assigning spare tablets.',
        ],
        selector: '.settings-workflow-card',
        mobileSelector: '.settings-workflow-card',
        placement: 'right',
      },
      {
        id: 'settings-runtime',
        system: 'Security + Runtime',
        title: 'Use short-lived admin sessions for privileged actions',
        description:
          'Admin mode should be session-bound and temporary. Never keep persistent privileged state longer than needed.',
        actions: [
          'Create admin session only when required.',
          'Validate header/session status chips.',
          'Disable and clear admin mode after operations.',
        ],
        tooltips: [
          'Long-lived admin sessions increase risk exposure.',
          'Session chip state should be checked before maintenance calls.',
        ],
        selector: '.settings-runtime-card',
        mobileSelector: '.settings-runtime-card',
        placement: 'left',
      },
      {
        id: 'settings-ops',
        system: 'Ops Dashboard',
        title: 'Read ops snapshot before running maintenance',
        description:
          'Use dashboard KPIs to decide when to run backfills and queue recoveries.',
        actions: [
          'Refresh snapshot before action.',
          'Check queue pressure and alert counts.',
          'Run climb backfill only when integrity indicates drift.',
        ],
        tooltips: [
          'Action on stale ops data creates false recoveries.',
          'Queue pressure trend matters more than one point-in-time value.',
        ],
        selector: '.settings-ops-card',
        mobileSelector: '.settings-ops-card',
        placement: 'top',
      },
      {
        id: 'settings-diagnostics-maintenance',
        system: 'Local State Hygiene',
        title: 'Use diagnostics + maintenance for controlled cleanup',
        description:
          'Diagnostics and maintenance sections let you inspect and safely reset local browser state.',
        actions: [
          'Refresh diagnostics before cleanup.',
          'Clear only targeted caches when possible.',
          'Use full reset defaults only for re-onboarding.',
        ],
        tooltips: [
          'Over-clearing local state can disrupt active shifts.',
          'Diagnostics first, destructive actions second.',
        ],
        selector: '.settings-diagnostics-card, .settings-maintenance-card',
        mobileSelector: '.settings-diagnostics-card, .settings-maintenance-card',
        placement: 'top',
      },
    ],
    checklist: [
      { id: 'settings-theme-density', title: 'Set theme and density defaults', detail: 'Aligns UI with crew operations.', stepId: 'settings-appearance' },
      { id: 'settings-search-defaults', title: 'Set quick search mode + region', detail: 'Improves navigation consistency.', stepId: 'settings-workflow' },
      { id: 'settings-tutorial-policy', title: 'Set tutorial autoplay policy', detail: 'Controls onboarding behavior per device.', stepId: 'settings-workflow' },
      { id: 'settings-admin-session', title: 'Create and verify admin session', detail: 'Validates privileged workflow.', stepId: 'settings-runtime' },
      { id: 'settings-ops-refresh', title: 'Refresh ops dashboard and read KPIs', detail: 'Builds data-driven maintenance behavior.', stepId: 'settings-ops' },
      { id: 'settings-diagnostics-refresh', title: 'Refresh local diagnostics before cleanup', detail: 'Prevents blind state resets.', stepId: 'settings-diagnostics-maintenance' },
    ],
    adoptionPlaybook: [
      'Settings owner publishes standard defaults for all devices.',
      'Admin mode is enabled only during active maintenance windows.',
      'Diagnostics review is part of shift-end routine.',
    ],
  },
  ops: {
    scope: 'ops',
    title: 'Ops Recovery and Reliability',
    subtitle: 'Operate queue and integrity workflows with deliberate sequencing.',
    icon: 'OPS',
    estimatedMinutes: 9,
    walkthrough: [
      {
        id: 'ops-runtime-access',
        system: 'Privilege Gate',
        title: 'Validate admin/runtime access before ops actions',
        description:
          'Operational actions require explicit auth readiness and known runtime settings.',
        actions: [
          'Confirm admin session active.',
          'Check write auth and public mode chips.',
          'Confirm refresh interval and current status.',
        ],
        tooltips: [
          'Failed ops actions are often auth-context issues, not backend issues.',
          'Check auth chips before rerunning failed actions.',
        ],
        selector: '.settings-runtime-card',
        mobileSelector: '.settings-runtime-card',
        placement: 'right',
      },
      {
        id: 'ops-dashboard-read',
        system: 'Observability',
        title: 'Read queue and integrity signals before intervention',
        description:
          'Use ops dashboard KPIs to choose narrow, evidence-based recovery actions.',
        actions: [
          'Refresh snapshot.',
          'Inspect queue pending and pressure.',
          'Inspect climb mismatch and signal coverage.',
        ],
        tooltips: [
          'Use trends, not single numbers, for intervention decisions.',
          'Integrity mismatches should be triaged before bulk actions.',
        ],
        selector: '.settings-ops-card',
        mobileSelector: '.settings-ops-card',
        placement: 'left',
      },
      {
        id: 'ops-backfill',
        system: 'Recovery Action',
        title: 'Run targeted recovery and verify post-state',
        description:
          'Run climb backfill intentionally, then verify KPI improvement and status messaging.',
        actions: [
          'Run backfill from ops controls.',
          'Capture updated/inserted result counts.',
          'Re-read dashboard KPIs after completion.',
        ],
        tooltips: [
          'Always measure impact immediately after recovery.',
          'Repeated backfill without diagnosis can increase load.',
        ],
        selector: '.settings-ops-card .center-actions-row',
        mobileSelector: '.settings-ops-card .center-actions-row',
        placement: 'bottom',
      },
      {
        id: 'ops-local-diag',
        system: 'Local Diagnostics',
        title: 'Use diagnostics to isolate browser-side issues',
        description:
          'Local diagnostics helps separate browser state issues from backend reliability incidents.',
        actions: [
          'Refresh diagnostics.',
          'Inspect favorites and compare cache counts.',
          'Escalate only after confirming backend-side symptoms.',
        ],
        tooltips: [
          'Not every issue is backend instability.',
          'Browser cache state can mimic data freshness failures.',
        ],
        selector: '.settings-diagnostics-card',
        mobileSelector: '.settings-diagnostics-card',
        placement: 'top',
      },
    ],
    checklist: [
      { id: 'ops-auth-check', title: 'Verify admin session and auth chips', detail: 'Ensures ops actions are authorized.', stepId: 'ops-runtime-access' },
      { id: 'ops-refresh-snapshot', title: 'Refresh ops snapshot before action', detail: 'Avoids acting on stale observability.', stepId: 'ops-dashboard-read' },
      { id: 'ops-read-queue', title: 'Review queue pressure and pending counts', detail: 'Determines urgency and scope.', stepId: 'ops-dashboard-read' },
      { id: 'ops-run-backfill', title: 'Run targeted backfill once and review result', detail: 'Validates recovery workflow.', stepId: 'ops-backfill' },
      { id: 'ops-post-verify', title: 'Verify dashboard after backfill', detail: 'Confirms action impact and remaining risk.', stepId: 'ops-backfill' },
      { id: 'ops-local-check', title: 'Refresh local diagnostics before escalation', detail: 'Separates local from backend faults.', stepId: 'ops-local-diag' },
    ],
    adoptionPlaybook: [
      'Ops lead performs auth + snapshot checks before any recovery action.',
      'Recoveries are logged with before/after KPI notes.',
      'Escalation includes local diagnostics state to reduce false positives.',
    ],
  },
};

export function getTutorialBlueprint(scope: TutorialScope): TutorialBlueprint {
  return BLUEPRINTS[scope] || BLUEPRINTS.home;
}
