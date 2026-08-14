import { useCallback, useEffect, useState } from 'react';
import { getScoutingCoverage } from '../api';
import type { ScoutingCoverageResponse } from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { SCOUTING_VIEWS } from '../components/pageViewBarConfig';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useMobileLayout } from '../hooks/useMobileLayout';
import './ScoutingCoveragePage.css';

const STORAGE_KEY = 'scouting_center_event_key';
const REFRESH_MS = 30_000;

type CoverageTab = 'grid' | 'leaderboard' | 'quality';

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

export function ScoutingCoveragePage() {
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent } =
    useEventKeyParam(STORAGE_KEY);
  const isMobile = useMobileLayout();

  const [data, setData] = useState<ScoutingCoverageResponse | null>(null);
  const [tab, setTab] = useState<CoverageTab>('grid');
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState('');

  const fetchCoverage = useCallback(async (key: string, silent = false) => {
    if (!silent) setLoading(true);
    try {
      const result = await getScoutingCoverage(key);
      setData(result);
      setErrorText('');
    } catch (err) {
      if (!silent) setErrorText((err as Error).message || 'Failed to load coverage data.');
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!eventKey) return;
    setData(null);
    void fetchCoverage(eventKey);
    const interval = window.setInterval(() => void fetchCoverage(eventKey, true), REFRESH_MS);
    return () => window.clearInterval(interval);
  }, [eventKey, fetchCoverage]);

  const summary = data?.summary;
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
      <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />
      <div className="center-page-container">
        <SurfaceCardGroup groupId="scouting-coverage">
          <SurfaceCard
            title="Scouting Coverage"
            subtitle="Who has scouted what — find the holes before alliance selection does."
            expandable={false}
            mobileCollapsible={false}
          >
            <EventPicker
              value={eventKey}
              onSelect={selectEvent}
              inputValue={eventInput}
              onInputChange={setEventInput}
              onSubmit={commitInput}
              loading={loading}
            />
            {errorText ? <p className="center-callout warning">{errorText}</p> : null}

            {summary ? (
              <div className="center-kpi-grid coverage-summary-grid">
                <div
                  className={`center-kpi-card ${
                    summary.coverage_pct >= 90
                      ? 'tone-green'
                      : summary.coverage_pct >= 60
                        ? 'tone-yellow'
                        : 'tone-red'
                  }`}
                >
                  <span>Coverage</span>
                  <strong>{summary.coverage_pct}%</strong>
                </div>
                <div className="center-kpi-card">
                  <span>Slots Covered</span>
                  <strong>
                    {summary.covered_slots}/{summary.total_slots}
                  </strong>
                </div>
                <div className="center-kpi-card">
                  <span>Entries</span>
                  <strong>{summary.total_entries}</strong>
                </div>
                <div className="center-kpi-card">
                  <span>Scouts</span>
                  <strong>{summary.scout_count}</strong>
                </div>
                <div className={`center-kpi-card ${summary.outlier_count > 0 ? 'tone-yellow' : ''}`}>
                  <span>Quality Flags</span>
                  <strong>{summary.outlier_count}</strong>
                </div>
              </div>
            ) : null}
          </SurfaceCard>

          {data ? (
            <SurfaceCard
              title="Details"
              right={!isMobile ? coverageTabs : undefined}
              expandable={false}
              mobileCollapsible={false}
            >
              {isMobile ? <div className="coverage-mobile-tabs">{coverageTabs}</div> : null}
              {tab === 'grid' ? (
                <div className="coverage-grid-wrap">
                  <table className="coverage-grid">
                    <thead>
                      <tr>
                        <th>Match</th>
                        <th colSpan={3}>Red</th>
                        <th colSpan={3}>Blue</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.grid.map((row) => (
                        <tr key={row.match_key}>
                          <td className="coverage-match-label">{row.label}</td>
                          {row.slots.map((slot) => (
                            <td
                              key={`${row.match_key}-${slot.team_key}`}
                              className={[
                                'coverage-cell',
                                slot.alliance === 'red' ? 'coverage-cell--red' : 'coverage-cell--blue',
                                slot.entry_count > 0 ? 'coverage-cell--covered' : 'coverage-cell--missing',
                              ].join(' ')}
                              title={
                                slot.entry_count > 0
                                  ? `${slot.entry_count} entr${slot.entry_count > 1 ? 'ies' : 'y'} by ${slot.scouts.join(', ') || 'unknown'}`
                                  : 'No scouting entry yet'
                              }
                            >
                              {teamNumber(slot.team_key)}
                              {slot.entry_count > 1 ? (
                                <sup className="coverage-count">{slot.entry_count}</sup>
                              ) : null}
                            </td>
                          ))}
                          {/* Pad rows that have fewer than 6 known slots. */}
                          {Array.from({ length: Math.max(0, 6 - row.slots.length) }).map((_, i) => (
                            <td key={`pad-${i}`} className="coverage-cell" />
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {tab === 'leaderboard' ? (
                data.leaderboard.length === 0 ? (
                  <p className="center-callout muted">No scouting entries yet.</p>
                ) : (
                  <div className="center-table-wrap">
                    <table className="center-table">
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>Scout</th>
                          <th>Entries</th>
                          <th>Matches</th>
                          <th>Teams</th>
                          <th>Best Streak</th>
                          <th>Last Entry</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.leaderboard.map((row, index) => (
                          <tr key={row.scout_profile}>
                            <td className="text-muted">
                              <span className={`coverage-rank-badge ${index < 3 ? `coverage-rank-badge--top${index + 1}` : ''}`}>
                                {index + 1}
                              </span>
                            </td>
                            <td className="coverage-scout-name">{row.scout_profile}</td>
                            <td>{row.entry_count}</td>
                            <td>{row.matches_covered}</td>
                            <td>{row.teams_covered}</td>
                            <td>
                              {row.best_qual_streak >= 10 ? <span className="coverage-streak-hot">Hot</span> : null}
                              {row.best_qual_streak}
                            </td>
                            <td className="text-muted">{relativeTime(row.last_entry_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              ) : null}

              {tab === 'quality' ? (
                data.outliers.length === 0 ? (
                  <p className="center-callout muted">
                    No quality flags — scouts agree with each other and with team history.
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
        </SurfaceCardGroup>
      </div>
    </>
  );
}
