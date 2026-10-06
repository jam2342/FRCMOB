import { exportPrintableReport } from '../platform/exportFile';
import { isNativeApp } from '../platform/runtime';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  createPicklist,
  deletePicklist,
  getEventTeamsIntel,
  getPicklist,
  listPicklists,
  listPitEntries,
  resolveMediaUrl,
} from '../api';
import type { Picklist, PicklistSlot, PicklistSlotTier } from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { COMPARE_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling } from '../hooks/useSingleFlightPolling';
import { hapticTap } from '../utils/haptics';
import { asRecord, metric, parseNumber } from './centerUtils';
import { slotIndexForRank } from '../features/picklists/rankMove';
import './PicklistPage.css';
import { WorkspaceGate } from '../features/workspace/WorkspaceGate';
import { useWorkspace } from '../features/workspace/useWorkspace';
import { usePicklistEditor } from '../features/picklists/usePicklistEditor';

/* ------------------------------------------------------------------ */
/*  Constants & helpers                                                */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'scouting_center_event_key';
const SCOUT_PROFILE_STORAGE = 'scouting_manual_profile_v1';
const LIVE_POLL_MS = 4000;

type TeamInfo = {
  team_key: string;
  team_number: number;
  nickname: string | null;
  rating_0_100: number | null;
};

function readScoutProfile(): string {
  try {
    return String(window.localStorage.getItem(SCOUT_PROFILE_STORAGE) || '').trim();
  } catch {
    return '';
  }
}

function teamNumber(teamKey: string): string {
  return teamKey.replace(/^frc/i, '');
}

const TIER_LABELS: Record<PicklistSlotTier, string> = {
  first: '1st pick',
  second: '2nd pick',
  dnp: 'DNP',
};

const NEXT_TIER: Record<PicklistSlotTier, PicklistSlotTier> = {
  first: 'second',
  second: 'dnp',
  dnp: 'first',
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

function PicklistWorkspacePage() {
  const isMobile = useMobileLayout();
  const pageVisible = usePageVisibility();
  const { online } = useOnlineStatus();
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent, fetchTrigger } =
    useEventKeyParam(STORAGE_KEY);

  const [teamPool, setTeamPool] = useState<TeamInfo[]>([]);
  const [picklists, setPicklists] = useState<Picklist[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const workspace = useWorkspace()!;
  const editor = usePicklistEditor(workspace.workspace.id);
  const { doc, saving, select: selectDoc, edit: editDoc } = editor;
  const [pitPhotoByTeam, setPitPhotoByTeam] = useState<Map<string, string>>(new Map());
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [expandedTeam, setExpandedTeam] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  // Phones can't drag, and Up/Down is one place per tap. Tapping a rank opens
  // "move to rank N" so a long move is one action.
  const [movingTeam, setMovingTeam] = useState<string | null>(null);
  const [moveTarget, setMoveTarget] = useState('');

  const loadSequence = useRef(0);
  const teamInfoByKey = useMemo(() => {
    const map = new Map<string, TeamInfo>();
    for (const team of teamPool) map.set(team.team_key, team);
    return map;
  }, [teamPool]);

  /* ---- Data loading ---------------------------------------------- */

  const fetchTeams = useCallback(async (key: string, sequence: number) => {
    try {
      const payload = await getEventTeamsIntel(key, {
        include_tba: true,
        include_statbotics: false,
        include_season_fallback: true,
        include_rating_details: false,
        include_rating_signals: false,
      });
      const teams = (Array.isArray(payload.teams) ? payload.teams : [])
        .map((entry) => {
          const row = asRecord(entry);
          const rating = asRecord(row?.rating);
          return {
            team_key: String(row?.team_key || '').toLowerCase(),
            team_number: parseNumber(row?.team_number) ?? 0,
            nickname: typeof row?.nickname === 'string' ? row.nickname : null,
            rating_0_100: parseNumber(rating?.rating_0_100),
          };
        })
        .filter((row) => row.team_key.length > 0)
        .sort((a, b) => (b.rating_0_100 ?? 0) - (a.rating_0_100 ?? 0));
      if (loadSequence.current === sequence) setTeamPool(teams);
    } catch {
      if (loadSequence.current === sequence) setTeamPool([]);
    }
  }, []);

  const fetchPitPhotos = useCallback(async (key: string, sequence: number) => {
    try {
      const result = await listPitEntries(key);
      const map = new Map<string, string>();
      for (const entry of result.entries ?? []) {
        if (entry.photos?.length) {
          map.set(entry.team_key.toLowerCase(), entry.photo_urls?.[0] || entry.photos[0]);
        }
      }
      if (loadSequence.current === sequence) setPitPhotoByTeam(map);
    } catch {
      if (loadSequence.current === sequence) setPitPhotoByTeam(new Map());
    }
  }, []);

  const fetchPicklists = useCallback(async (key: string, sequence: number) => {
    setLoading(true);
    setErrorText('');
    try {
      const result = await listPicklists(key);
      if (loadSequence.current !== sequence) return;
      const lists = result.picklists ?? [];
      setPicklists(lists);
      if (lists.length > 0) {
        setActiveId((current) =>
          current != null && lists.some((p) => p.id === current) ? current : lists[0].id,
        );
      } else {
        setActiveId(null);
        selectDoc(null);
      }
    } catch (err) {
      if (loadSequence.current === sequence) setErrorText((err as Error).message || 'Failed to load picklists.');
    } finally {
      if (loadSequence.current === sequence) setLoading(false);
    }
  }, [selectDoc]);

  useEffect(() => {
    const sequence = ++loadSequence.current;
    setActiveId(null);
    selectDoc(null);
    setTeamPool([]);
    setPitPhotoByTeam(new Map());
    setExpandedTeam(null);
    if (eventKey) {
      void fetchPicklists(eventKey, sequence);
      void fetchTeams(eventKey, sequence);
      void fetchPitPhotos(eventKey, sequence);
    }
    return () => { loadSequence.current = sequence + 1; };
  }, [eventKey, fetchTrigger, fetchPicklists, fetchTeams, fetchPitPhotos, selectDoc]);

  useEffect(() => {
    if (activeId == null) return;
    const fromList = picklists.find((p) => p.id === activeId);
    if (fromList) selectDoc(fromList);
  }, [activeId, picklists, selectDoc]);

  /* ---- Live mode polling ------------------------------------------ */

  const pollLivePicklist = useCallback(async (): Promise<boolean> => {
    if (!doc?.live_mode || editor.pending) return true;
    const sequence = loadSequence.current;
    try {
      const result = await getPicklist(doc.id);
      if (loadSequence.current === sequence && result.picklist.version > doc.version) {
        setPicklists((prev) => prev.map((p) => p.id === result.picklist.id ? result.picklist : p));
      }
      return true;
    } catch { return false; }
  }, [doc, editor.pending]);

  useSingleFlightPolling({
    enabled: Boolean(doc?.live_mode && doc.id != null),
    visible: pageVisible && online,
    intervalMs: LIVE_POLL_MS,
    run: pollLivePicklist,
    backoffMultiplier: 2,
    minBackoffMs: LIVE_POLL_MS,
    maxBackoffMs: 60000,
  });

  /* ---- Mutations --------------------------------------------------- */

  const mutateSlots = useCallback(
    (updater: (slots: PicklistSlot[]) => PicklistSlot[]) => {
      editDoc((prev) => ({ ...prev, slots: updater(prev.slots.slice()) }));
    }, [editDoc],
  );

  async function handleCreate(seedFromRatings: boolean) {
    if (!eventKey) return;
    const sequence = loadSequence.current;
    setLoading(true);
    setErrorText('');
    try {
      const slots: Partial<PicklistSlot>[] = seedFromRatings
        ? teamPool.map((team, index) => ({
            team_key: team.team_key,
            tier: index < 8 ? 'first' : 'second',
          }))
        : [];
      const result = await createPicklist({
        event_key: eventKey,
        title: `Picklist ${new Date().toLocaleDateString()}`,
        created_by: readScoutProfile() || undefined,
        slots,
      });
      if (loadSequence.current !== sequence) return;
      setPicklists((prev) => [result.picklist, ...prev]);
      setActiveId(result.picklist.id);
      selectDoc(result.picklist);
    } catch (err) {
      if (loadSequence.current === sequence) setErrorText((err as Error).message || 'Failed to create picklist.');
    } finally {
      if (loadSequence.current === sequence) setLoading(false);
    }
  }

  async function handleDelete() {
    if (!doc || saving || deleting) return;
    if (!window.confirm(`Delete "${doc.title}"? This cannot be undone.`)) return;
    const sequence = loadSequence.current;
    editor.pause();
    setDeleting(true);
    try {
      await deletePicklist(doc.id);
      editor.remove(doc.id);
      if (loadSequence.current !== sequence) return;
      setPicklists((prev) => prev.filter((p) => p.id !== doc.id));
      setActiveId(null);
      selectDoc(null);
      if (eventKey) void fetchPicklists(eventKey, sequence);
    } catch (err) {
      if (loadSequence.current === sequence) setErrorText((err as Error).message || 'Failed to delete picklist.');
    } finally { setDeleting(false); }
  }

  function moveSlot(index: number, delta: number) {
    mutateSlots((slots) => {
      const target = index + delta;
      if (target < 0 || target >= slots.length) return slots;
      const [item] = slots.splice(index, 1);
      slots.splice(target, 0, item);
      return slots;
    });
    hapticTap();
  }

  function moveSlotToIndex(from: number, to: number) {
    mutateSlots((slots) => {
      if (from === to || from < 0 || from >= slots.length) return slots;
      const [item] = slots.splice(from, 1);
      slots.splice(Math.max(0, Math.min(to, slots.length)), 0, item);
      return slots;
    });
  }

  function moveSlotToRank(from: number, rank: number) {
    moveSlotToIndex(from, slotIndexForRank(slots, rank));
    hapticTap();
  }

  function cycleTier(index: number) {
    mutateSlots((slots) => {
      const slot = { ...slots[index] };
      slot.tier = NEXT_TIER[slot.tier];
      if (slot.tier !== 'dnp') slot.dnp_reason = '';
      slots[index] = slot;
      return slots;
    });
    hapticTap();
  }

  function setSlotField(index: number, field: 'notes' | 'dnp_reason', value: string) {
    mutateSlots((slots) => {
      slots[index] = { ...slots[index], [field]: value };
      return slots;
    });
  }

  function markPicked(index: number) {
    mutateSlots((slots) => {
      const pickedCount = slots.filter(
        (slot) => slot.status === 'picked' || slot.status === 'captain',
      ).length;
      const alliance = Math.min(8, Math.floor(pickedCount / 3) + 1);
      slots[index] = { ...slots[index], status: 'picked', picked_by_alliance: alliance };
      return slots;
    });
    hapticTap();
  }

  function markDeclined(index: number) {
    mutateSlots((slots) => {
      slots[index] = { ...slots[index], status: 'declined', picked_by_alliance: null };
      return slots;
    });
    hapticTap();
  }

  function resetStatus(index: number) {
    mutateSlots((slots) => {
      slots[index] = { ...slots[index], status: 'available', picked_by_alliance: null };
      return slots;
    });
  }

  function addMissingTeams() {
    const existing = new Set((doc?.slots ?? []).map((slot) => slot.team_key));
    const missing = teamPool.filter((team) => !existing.has(team.team_key));
    if (missing.length === 0) return;
    mutateSlots((slots) => [
      ...slots,
      ...missing.map((team) => ({
        team_key: team.team_key,
        tier: 'second' as PicklistSlotTier,
        status: 'available' as const,
        picked_by_alliance: null,
        dnp_reason: '',
        notes: '',
      })),
    ]);
  }

  function toggleLiveMode() {
    editDoc((prev) => ({ ...prev, live_mode: !prev.live_mode }));
  }

  /* ---- Derived ----------------------------------------------------- */

  const slots = useMemo(() => doc?.slots ?? [], [doc?.slots]);
  const nextAvailableIndex = useMemo(() => {
    return slots.findIndex((slot) => slot.status === 'available' && slot.tier !== 'dnp');
  }, [slots]);
  const availableCount = slots.filter((s) => s.status === 'available' && s.tier !== 'dnp').length;
  const pickedCount = slots.filter((s) => s.status === 'picked' || s.status === 'captain').length;

  /* ---- Render ------------------------------------------------------ */

  return (
    <>
      <PageViewBar items={COMPARE_VIEWS} />
      <div className="center-page-container">

          <SurfaceCard
            title="Picklist Builder"
            subtitle="Hand-ordered alliance selection list. Shared with your whole team — edits sync automatically."
            className="no-print"
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
            {editor.notice ? (
              <div className="center-callout" role="status">
                <p>{editor.notice}</p>
                {editor.conflict ? (
                  <>
                    <button className="center-btn" type="button" onClick={editor.retry} disabled={saving || deleting}>Save my changes</button>
                    <button className="center-btn ghost" type="button" onClick={() => {
                      if (window.confirm('Discard your local edits and show the shared version?')) editor.discard();
                    }} disabled={saving || deleting}>Use shared version</button>
                  </>
                ) : <button className="center-btn" type="button" onClick={editor.retry} disabled={saving || deleting}>Retry saving</button>}
              </div>
            ) : null}

            {eventKey ? (
              <div className="picklist-toolbar">
                {picklists.length > 0 ? (
                  <select
                    className="center-input picklist-select"
                    value={activeId ?? ''}
                    onChange={(event) => setActiveId(Number(event.target.value))}
                    aria-label="Select picklist"
                    disabled={deleting}
                  >
                    {picklists.map((list) => (
                      <option key={list.id} value={list.id}>
                        {list.title}
                      </option>
                    ))}
                  </select>
                ) : null}
                <button
                  type="button"
                  className="center-btn"
                  onClick={() => void handleCreate(true)}
                  disabled={loading || deleting || teamPool.length === 0}
                  title="Create a picklist pre-ranked by team ratings"
                >
                  New from ratings
                </button>
                <button
                  type="button"
                  className="center-btn ghost"
                  onClick={() => void handleCreate(false)}
                  disabled={loading || deleting}
                >
                  New empty
                </button>
                {doc ? (
                  <>
                    <button type="button" className="center-btn ghost" disabled={deleting} onClick={addMissingTeams}>
                      Add missing teams
                    </button>
                    <button type="button" className="center-btn ghost" onClick={() => void exportPrintableReport('picklist.html').catch(() => window.alert('The report could not be exported. Please try again.'))}>
                      {isNativeApp() ? 'Save report' : 'Print / PDF'}
                    </button>
                    <button
                      type="button"
                      className="center-btn ghost danger"
                      disabled={saving || deleting}
                      onClick={() => void handleDelete()}
                    >
                      Delete
                    </button>
                  </>
                ) : null}
              </div>
            ) : null}
          </SurfaceCard>

          {doc ? (
            <SurfaceCard
              title={doc.title}
              subtitle={
                doc.live_mode
                  ? `LIVE — ${pickedCount} picked, ${availableCount} still available. Tap a team as it gets picked or declines.`
                  : `${slots.length} team${slots.length === 1 ? '' : 's'} ranked. Tap a rank number to move a team, or use the arrows.`
              }
              right={
                <span className="picklist-header-right">
                  {saving ? <span className="center-chip">Saving…</span> : null}
                  <button
                    type="button"
                    className={`center-btn ${doc.live_mode ? 'danger' : ''}`}
                    disabled={deleting}
                    onClick={toggleLiveMode}
                  >
                    {doc.live_mode ? 'End live mode' : 'Start alliance selection'}
                  </button>
                </span>
              }
              expandable={false}
              mobileCollapsible={false}
            >
              {slots.length === 0 ? (
                <p className="center-callout muted">
                  Empty picklist. Use “Add missing teams” to pull in every team at this event.
                </p>
              ) : (
                <ol className="picklist-rows" inert={deleting ? true : undefined}>
                  {slots.map((slot, index) => {
                    const info = teamInfoByKey.get(slot.team_key);
                    const crossed = slot.status === 'picked' || slot.status === 'declined';
                    const isNext = doc.live_mode && index === nextAvailableIndex;
                    const photo = pitPhotoByTeam.get(slot.team_key);
                    const isExpanded = expandedTeam === slot.team_key;
                    const rank =
                      slots.slice(0, index).filter((s) => s.tier !== 'dnp').length + 1;
                    return (
                      <li
                        key={slot.team_key}
                        className={[
                          'picklist-row',
                          crossed ? `picklist-row--${slot.status}` : '',
                          slot.tier === 'dnp' ? 'picklist-row--dnp' : '',
                          isNext ? 'picklist-row--next' : '',
                          dragIndex === index ? 'picklist-row--dragging' : '',
                        ]
                          .filter(Boolean)
                          .join(' ')}
                        draggable={!doc.live_mode}
                        onDragStart={() => setDragIndex(index)}
                        onDragEnd={() => setDragIndex(null)}
                        onDragOver={(event) => {
                          event.preventDefault();
                          if (dragIndex != null && dragIndex !== index) {
                            moveSlotToIndex(dragIndex, index);
                            setDragIndex(index);
                          }
                        }}
                      >
                        {slot.tier === 'dnp' || doc.live_mode ? (
                          <span className="picklist-rank" aria-label={slot.tier === 'dnp' ? 'Do not pick' : `Rank ${rank}`}>
                            {slot.tier === 'dnp' ? 'DNP' : rank}
                          </span>
                        ) : (
                          <button
                            type="button"
                            className="picklist-rank picklist-rank--button"
                            aria-label={`Rank ${rank}. Move #${teamNumber(slot.team_key)} to another rank`}
                            aria-expanded={movingTeam === slot.team_key}
                            onClick={() => {
                              setMovingTeam(movingTeam === slot.team_key ? null : slot.team_key);
                              setMoveTarget('');
                            }}
                          >
                            {rank}
                          </button>
                        )}
                        {photo ? (
                          <img
                            className="picklist-photo"
                            src={resolveMediaUrl(photo)}
                            alt={`Robot ${teamNumber(slot.team_key)}`}
                            loading="lazy"
                          />
                        ) : null}
                        <button
                          type="button"
                          className="picklist-team"
                          onClick={() => setExpandedTeam(isExpanded ? null : slot.team_key)}
                        >
                          <strong>#{teamNumber(slot.team_key)}</strong>
                          {info?.nickname ? <span className="picklist-nickname">{info.nickname}</span> : null}
                          {info?.rating_0_100 != null ? (
                            <span className="picklist-rating">{metric(info.rating_0_100, 0)}</span>
                          ) : null}
                        </button>

                        <button
                          type="button"
                          className={`picklist-tier picklist-tier--${slot.tier}`}
                          onClick={() => cycleTier(index)}
                          disabled={doc.live_mode}
                          title="Cycle pick tier (1st → 2nd → DNP)"
                        >
                          {TIER_LABELS[slot.tier]}
                        </button>

                        {slot.status === 'picked' && slot.picked_by_alliance ? (
                          <span className="picklist-status picked">A{slot.picked_by_alliance}</span>
                        ) : null}
                        {slot.status === 'declined' ? (
                          <span className="picklist-status declined">Declined</span>
                        ) : null}

                        <span className="picklist-actions">
                          {doc.live_mode ? (
                            slot.status === 'available' ? (
                              <>
                                <button
                                  type="button"
                                  className="center-btn picklist-mini-btn"
                                  onClick={() => markPicked(index)}
                                >
                                  Picked
                                </button>
                                <button
                                  type="button"
                                  className="center-btn ghost picklist-mini-btn"
                                  onClick={() => markDeclined(index)}
                                >
                                  Declined
                                </button>
                              </>
                            ) : (
                              <button
                                type="button"
                                className="center-btn ghost picklist-mini-btn"
                                onClick={() => resetStatus(index)}
                              >
                                Undo
                              </button>
                            )
                          ) : (
                            <>
                              <button
                                type="button"
                                className="center-btn ghost picklist-mini-btn"
                                onClick={() => moveSlot(index, -1)}
                                aria-label={`Move ${slot.team_key} up`}
                                title="Move up"
                                disabled={index === 0}
                              >
                                <span aria-hidden="true">↑</span>
                              </button>
                              <button
                                type="button"
                                className="center-btn ghost picklist-mini-btn"
                                onClick={() => moveSlot(index, 1)}
                                aria-label={`Move ${slot.team_key} down`}
                                title="Move down"
                                disabled={index === slots.length - 1}
                              >
                                <span aria-hidden="true">↓</span>
                              </button>
                            </>
                          )}
                        </span>

                        {movingTeam === slot.team_key && !doc.live_mode && slot.tier !== 'dnp' ? (
                          <form
                            className="picklist-move"
                            onSubmit={(event) => {
                              event.preventDefault();
                              const target = Number(moveTarget);
                              if (!Number.isInteger(target) || target < 1) return;
                              moveSlotToRank(index, target);
                              setMovingTeam(null);
                            }}
                          >
                            <label className="picklist-move-label" htmlFor={`picklist-move-${slot.team_key}`}>
                              Move #{teamNumber(slot.team_key)} to rank
                            </label>
                            <input
                              id={`picklist-move-${slot.team_key}`}
                              className="center-input picklist-move-input"
                              type="number"
                              inputMode="numeric"
                              min={1}
                              max={slots.filter((s) => s.tier !== 'dnp').length}
                              value={moveTarget}
                              onChange={(event) => setMoveTarget(event.target.value)}
                              autoFocus
                            />
                            <button type="submit" className="center-btn picklist-mini-btn" disabled={!moveTarget}>Move</button>
                            <button type="button" className="center-btn ghost picklist-mini-btn" onClick={() => setMovingTeam(null)}>
                              Cancel
                            </button>
                          </form>
                        ) : null}
                        {isExpanded ? (
                          <div className="picklist-detail">
                            <label className="picklist-detail-field">
                              <span>Notes</span>
                              <input
                                className="center-input"
                                type="text"
                                value={slot.notes}
                                placeholder="Why this rank? Strengths, pairings…"
                                onChange={(event) => setSlotField(index, 'notes', event.target.value)}
                              />
                            </label>
                            {slot.tier === 'dnp' ? (
                              <label className="picklist-detail-field">
                                <span>Do-not-pick reason</span>
                                <input
                                  className="center-input"
                                  type="text"
                                  value={slot.dnp_reason}
                                  placeholder="Why avoid this team?"
                                  onChange={(event) =>
                                    setSlotField(index, 'dnp_reason', event.target.value)
                                  }
                                />
                              </label>
                            ) : null}
                          </div>
                        ) : null}
                        {!isExpanded && (slot.notes || slot.dnp_reason) ? (
                          <span className="picklist-note-preview">
                            {slot.dnp_reason || slot.notes}
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ol>
              )}
            </SurfaceCard>
          ) : eventKey && !loading ? (
            <SurfaceCard
              title="No picklist yet"
              subtitle="Create one to start ranking teams."
              expandable={false}
              mobileCollapsible={false}
            >
              <p className="center-callout muted">
                “New from ratings” seeds the list with every team at this event, ordered by their
                FRCMOB rating — then put them in your order: tap a rank number to move a team,
                use the arrows, or drag on a computer.
              </p>
            </SurfaceCard>
          ) : null}

      </div>
      {isMobile ? <div className="picklist-mobile-spacer" aria-hidden="true" /> : null}
    </>
  );
}

// Team-only: picklists are private to a workspace, so nothing loads until
// the device has joined one.
export function PicklistPage() {
  return (
    <WorkspaceGate feature="Picklists" viewBar={<PageViewBar items={COMPARE_VIEWS} />}>
      <PicklistWorkspacePage />
    </WorkspaceGate>
  );
}
