import { formatDayTime } from '../utils/formatTime';
import { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import { getScoutingCoverage } from '../api';
import type { ScoutingCoverageResponse } from '../api';
import { EventPicker } from '../components/EventPicker';
import { ScoutingViewBar } from '../components/ScoutingViewBar';
import { scoutingViewTitle } from '../components/pageViewBarConfig';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { Stat } from '../components/ui/primitives';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { Table, type TableColumn } from '../components/ui/primitives';
import './ScoutingCoveragePage.css';
import { WorkspaceGate } from '../features/workspace/WorkspaceGate';

type LeaderboardRow = ScoutingCoverageResponse['leaderboard'][number];

const LEADERBOARD_COLUMNS: TableColumn<LeaderboardRow>[] = [
  {
    key: 'rank',
    label: '#',
    render: (_row, index) => (
      <span className={`coverage-rank-badge ${index < 3 ? `coverage-rank-badge--top${index + 1}` : ''}`.trim()}>
        {index + 1}
      </span>
    ),
  },
  { key: 'scout_profile', label: 'Scout' },
  { key: 'entry_count', label: 'Entries', numeric: true },
  { key: 'matches_covered', label: 'Matches', numeric: true },
  { key: 'teams_covered', label: 'Teams', numeric: true },
  {
    key: 'best_qual_streak',
    label: 'Best Streak',
    numeric: true,
    render: (row) => (
      <>
        {/* Ten quals in a row without a gap is the thing worth calling out,
            so it is a label rather than a colour on the number. */}
        {row.best_qual_streak >= 10 ? <span className="coverage-streak-hot">Hot</span> : null}
        {row.best_qual_streak}
      </>
    ),
  },
  { key: 'last_entry_at', label: 'Last Entry', render: (row) => relativeTime(row.last_entry_at) },
];

const STORAGE_KEY = 'scouting_center_event_key';
const REFRESH_MS = 30_000;

type CoverageTab = 'grid' | 'leaderboard' | 'quality';
type GridFilter = 'gaps' | 'all';
type GridRow = ScoutingCoverageResponse['grid'][number];
type GridSlot = GridRow['slots'][number];

function slotsFor(row: GridRow, alliance: 'red' | 'blue'): GridSlot[] {
  return row.slots.filter((slot) => slot.alliance === alliance);
}

function missingCount(row: GridRow): number {
  return row.slots.filter((slot) => slot.entry_count === 0).length;
}

function slotNote(slot: GridSlot): string {
  if (slot.entry_count === 0) return 'Not scouted';
  const who = [...new Set(slot.scouts.filter(Boolean))].join(', ');
  const entries = slot.entry_count > 1 ? ` (${slot.entry_count} entries)` : '';
  return who ? `Scouted by ${who}${entries}` : `Scouted${entries}`;
}

// A mark and a word, so covered and missing never rest on red against green alone.
function SlotChip({ slot }: { slot: GridSlot }) {
  const covered = slot.entry_count > 0;
  return (
    <span className={`coverage-chip ${covered ? 'coverage-chip--covered' : 'coverage-chip--missing'}`} title={slotNote(slot)}>
      {teamNumber(slot.team_key)}
      <span aria-hidden="true">{covered ? '✓' : '✕'}</span>
      <span className="sr-only">{covered ? 'scouted' : 'not scouted'}</span>
    </span>
  );
}

function allianceCell(row: GridRow, alliance: 'red' | 'blue') {
  return (
    <span className="coverage-chip-row">
      {slotsFor(row, alliance).map((slot) => <SlotChip key={slot.team_key} slot={slot} />)}
    </span>
  );
}

const GRID_COLUMNS: TableColumn<GridRow>[] = [
  { key: 'label', label: 'Match' },
  { key: 'red', label: 'Red', render: (row) => allianceCell(row, 'red') },
  { key: 'blue', label: 'Blue', render: (row) => allianceCell(row, 'blue') },
];

// One card per match on a phone: six robots, each with its state in words.
function CoverageCards({ rows }: { rows: GridRow[] }) {
  return (
    <div className="coverage-cards">
      {rows.map((row) => {
        const missing = missingCount(row);
        return (
          <div className="coverage-card" key={row.match_key}>
            <div className="coverage-card-head">
              <strong>{row.label}</strong>
              <span className="coverage-card-meta">
                {missing === 0 ? 'All scouted' : `${missing} missing`}
              </span>
            </div>
            {(['red', 'blue'] as const).map((alliance) => (
              <div className={`coverage-card-alliance coverage-card-alliance--${alliance}`} key={alliance}>
                <div className="coverage-card-alliance-label">{alliance === 'red' ? 'Red' : 'Blue'}</div>
                {slotsFor(row, alliance).map((slot) => {
                  const covered = slot.entry_count > 0;
                  return (
                    <div className="coverage-card-slot" key={slot.team_key}>
                      <span className="coverage-card-team">{teamNumber(slot.team_key)}</span>
                      <span className={`coverage-card-state ${covered ? 'is-covered' : 'is-missing'}`}>
                        <span className="coverage-card-mark" aria-hidden="true">{covered ? '✓' : '✕'}</span> {slotNote(slot)}
                      </span>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function teamNumber(teamKey: string): string {
  return teamKey.replace(/^frc/i, '');
}

function relativeTime(iso: string | null): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function ScoutingCoverageWorkspacePage() {
  const { eventKey, fetchTrigger, eventInput, setEventInput, commitInput, selectEvent } =
    useEventKeyParam(STORAGE_KEY);
  const isMobile = useMobileLayout();
  const { online, isShowingOfflineData } = useOnlineStatus();
  const connectionRef = useRef({ online, isShowingOfflineData });
  connectionRef.current = { online, isShowingOfflineData };

  const [data, setData] = useState<ScoutingCoverageResponse | null>(null);
  const [tab, setTab] = useState<CoverageTab>('grid');
  const [gridFilterChoice, setGridFilterChoice] = useState<GridFilter | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  // A response for an event the page has since left is dropped; otherwise a slow
  // old request replaced the new event's coverage.
  const activeEventKey = useRef(eventKey);
  const fetchCoverage = useCallback(async (key: string, silent = false) => {
    if (!silent) setLoading(true);
    try {
      const result = await getScoutingCoverage(key);
      if (key !== activeEventKey.current) return;
      setData(result);
      if (connectionRef.current.online && !connectionRef.current.isShowingOfflineData && navigator.onLine) setLastUpdated(Date.now());
      setErrorText('');
    } catch {
      if (key !== activeEventKey.current) return;
      setErrorText('Could not refresh coverage.');
    } finally {
      if (!silent && key === activeEventKey.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!eventKey) return;
    activeEventKey.current = eventKey;
    setData(null);
    setLastUpdated(null);
    setErrorText('');
    void fetchCoverage(eventKey);
    const interval = window.setInterval(() => void fetchCoverage(eventKey, true), REFRESH_MS);
    return () => window.clearInterval(interval);
  }, [eventKey, fetchCoverage, fetchTrigger]);

  const summary = data?.summary;
  const gapRows = useMemo(() => (data ? data.grid.filter((row) => missingCount(row) > 0) : []), [data]);
  // Open on the holes when there are any: that is what this page is for.
  const gridFilter: GridFilter = gridFilterChoice ?? (gapRows.length > 0 ? 'gaps' : 'all');
  const gridRows = gridFilter === 'gaps' ? gapRows : (data?.grid ?? []);
  const coverageTabs = data ? (
    <SegmentedTabs<CoverageTab>
      className="coverage-view-tabs"
      items={[
        { value: 'grid', label: 'Match Grid' },
        { value: 'leaderboard', label: 'Leaderboard' },
        { value: 'quality', label: `Quality (${data.outliers.length})` },
      ]}
      value={tab}
      onChange={setTab}
      ariaLabel="Coverage views"
    />
  ) : null;

  return (
    <>
      <ScoutingViewBar />
      <div className="center-page-container">

          <SurfaceCard
            title={scoutingViewTitle('/scouting/coverage')}
            subtitle="Who has scouted what — find the holes before alliance selection does."
            mobileCollapsible={false}
          >
            <EventPicker
              label="Event"
              value={eventKey}
              onSelect={selectEvent}
              inputValue={eventInput}
              onInputChange={setEventInput}
              onSubmit={commitInput}
              loading={loading}
            />
            {errorText || (data && (!online || isShowingOfflineData)) ? <p className="center-callout warning" role="alert">
              {data ? `Showing saved coverage. ${lastUpdated ? `Last live update ${formatDayTime(lastUpdated)}.` : 'No live update confirmed yet.'}` : errorText}
              {' '}<button type="button" className="center-btn ghost" disabled={loading} onClick={() => void fetchCoverage(eventKey)}>Retry</button>
            </p> : null}

            {summary ? (
              <div className="page-hero">
                {/* Coverage and Slots Covered were two of five identical boxes.
                    They are one fact: 86% is only meaningful as 129 of 150,
                    so the count becomes the hero's baseline rather than its
                    neighbour. */}
                <Stat
                  size="display"
                  label="Coverage"
                  value={`${summary.coverage_pct}%`}
                  sub={`${summary.covered_slots} of ${summary.total_slots} slots scouted`}
                  tone={
                    summary.coverage_pct >= 90
                      ? 'success'
                      : summary.coverage_pct >= 60
                        ? 'warning'
                        : 'danger'
                  }
                />
                <div className="page-hero-stats">
                  <Stat size="sm" label="Entries" value={summary.total_entries} />
                  <Stat size="sm" label="Scouts" value={summary.scout_count} />
                  <Stat
                    size="sm"
                    label="Quality flags"
                    value={summary.outlier_count}
                    tone={summary.outlier_count > 0 ? 'warning' : 'default'}
                  />
                </div>
              </div>
            ) : null}
          </SurfaceCard>

          {data ? (
            <SurfaceCard
              expandable
              title="Details"
              right={!isMobile ? coverageTabs : undefined}
              mobileCollapsible={false}
            >
              {isMobile ? <div className="coverage-mobile-tabs">{coverageTabs}</div> : null}
              {tab === 'grid' ? (
                <>
                  <SegmentedTabs<GridFilter>
                    className="coverage-grid-filter"
                    ariaLabel="Which matches to show"
                    value={gridFilter}
                    onChange={setGridFilterChoice}
                    items={[
                      { value: 'gaps', label: <>Gaps <small>{gapRows.length}</small></> },
                      { value: 'all', label: <>All <small>{data.grid.length}</small></> },
                    ]}
                  />
                  {gridRows.length === 0 ? (
                    <p className="center-callout muted">
                      {data.grid.length === 0 ? 'No schedule to check against yet.' : 'Every robot in every match has a scouting entry.'}
                    </p>
                  ) : (
                    <Table
                      className="coverage-grid-table"
                      columns={GRID_COLUMNS}
                      rows={gridRows}
                      rowKey={(row) => row.match_key}
                      renderCards={(rows) => <CoverageCards rows={rows} />}
                    />
                  )}
                </>
              ) : null}

              {tab === 'leaderboard' ? (
                <Table
                  columns={LEADERBOARD_COLUMNS}
                  rows={data.leaderboard}
                  rowKey={(row) => row.scout_profile}
                  empty="No scouting entries yet."
                />
              ) : null}

              {tab === 'quality' ? (
                data.outliers.length === 0 ? (
                  <p className="center-callout muted">
                    {data.summary.total_entries > 0 ? 'No quality flags detected.' : <>Quality checks need scouting reports. <a href={`#/scouting?event=${encodeURIComponent(eventKey)}`}>Start scouting</a>.</>}
                  </p>
                ) : (
                  <ul className="coverage-outliers">
                    {data.outliers.map((outlier, index) => (
                      <li key={`${outlier.match_key}-${outlier.team_key}-${index}`}>
                        <span
                          className={`coverage-outlier-kind ${
                            outlier.kind === 'scout_disagreement' ? 'kind-disagree' : 'kind-anomaly'
                          }`}
                        >
                          {outlier.kind === 'scout_disagreement' ? 'Disagreement' : 'Anomaly'}
                        </span>
                        <strong>
                          #{teamNumber(outlier.team_key)} · {outlier.match_key.split('_')[1]?.toUpperCase() ?? outlier.match_key}
                        </strong>
                        <span className="coverage-outlier-detail">{outlier.detail}</span>
                        {outlier.scouts.filter(Boolean).length > 0 ? (
                          <span className="text-muted">- {outlier.scouts.filter(Boolean).join(', ')}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )
              ) : null}
            </SurfaceCard>
          ) : null}

      </div>
    </>
  );
}

// Team-only: scouting coverage reports are private to a workspace, so nothing loads until
// the device has joined one.
export function ScoutingCoveragePage() {
  return (
    <WorkspaceGate feature="Scouting coverage reports" viewBar={<ScoutingViewBar />}>
      <ScoutingCoverageWorkspacePage />
    </WorkspaceGate>
  );
}
