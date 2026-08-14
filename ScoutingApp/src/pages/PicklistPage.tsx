import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  createPicklist,
  deletePicklist,
  getEventTeamsIntel,
  getPicklist,
  listPicklists,
  listPitEntries,
  resolveMediaUrl,
  updatePicklist,
} from '../api';
import type { Picklist, PicklistSlot, PicklistSlotTier } from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { COMPARE_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { hapticTap } from '../utils/haptics';
import { asRecord, metric, parseNumber } from './centerUtils';
import './PicklistPage.css';

/* ------------------------------------------------------------------ */
/*  Constants & helpers                                                */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'scouting_center_event_key';
const SCOUT_PROFILE_STORAGE = 'scouting_manual_profile_v1';
const SAVE_DEBOUNCE_MS = 900;
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

export function PicklistPage() {
  const isMobile = useMobileLayout();
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent } =
    useEventKeyParam(STORAGE_KEY);

  const [teamPool, setTeamPool] = useState<TeamInfo[]>([]);
  const [picklists, setPicklists] = useState<Picklist[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [doc, setDoc] = useState<Picklist | null>(null);
  const [pitPhotoByTeam, setPitPhotoByTeam] = useState<Map<string, string>>(new Map());
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [statusText, setStatusText] = useState('');
  const [saving, setSaving] = useState(false);
  const [expandedTeam, setExpandedTeam] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  // Pending local edits not yet acknowledged by the server.
  const dirtyRef = useRef(false);
  const docRef = useRef<Picklist | null>(null);
  docRef.current = doc;
  const saveTimerRef = useRef<number | null>(null);

  const teamInfoByKey = useMemo(() => {
    const map = new Map<string, TeamInfo>();
    for (const team of teamPool) map.set(team.team_key, team);
    return map;
  }, [teamPool]);

  /* ---- Data loading ---------------------------------------------- */

  const fetchTeams = useCallback(async (key: string) => {
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
      setTeamPool(teams);
    } catch {
      setTeamPool([]);
    }
  }, []);

  const fetchPitPhotos = useCallback(async (key: string) => {
    try {
      const result = await listPitEntries(key);
      const map = new Map<string, string>();
      for (const entry of result.entries ?? []) {
        if (entry.photos?.length) {
          map.set(entry.team_key.toLowerCase(), entry.photos[0]);
        }
      }
      setPitPhotoByTeam(map);
    } catch {
      setPitPhotoByTeam(new Map());
    }
  }, []);

  const fetchPicklists = useCallback(async (key: string) => {
    setLoading(true);
    setErrorText('');
    try {
      const result = await listPicklists(key);
      const lists = result.picklists ?? [];
      setPicklists(lists);
      if (lists.length > 0) {
        setActiveId((current) =>
          current != null && lists.some((p) => p.id === current) ? current : lists[0].id,
        );
      } else {
        setActiveId(null);
        setDoc(null);
      }
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to load picklists.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!eventKey) return;
    void fetchPicklists(eventKey);
    void fetchTeams(eventKey);
    void fetchPitPhotos(eventKey);
  }, [eventKey, fetchPicklists, fetchTeams, fetchPitPhotos]);

  useEffect(() => {
    if (activeId == null) return;
    const fromList = picklists.find((p) => p.id === activeId);
    if (fromList) {
      setDoc(fromList);
      dirtyRef.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  /* ---- Saving with optimistic concurrency ------------------------ */

  const pushSave = useCallback(async () => {
    const current = docRef.current;
    if (!current || !dirtyRef.current) return;
    setSaving(true);
    try {
      const result = await updatePicklist(current.id, {
        version: current.version,
        slots: current.slots,
        title: current.title,
        live_mode: current.live_mode,
      });
      if (result.conflict) {
        // Someone else saved first: adopt their version, surface it clearly.
        setDoc(result.picklist);
        dirtyRef.current = false;
        setStatusText('Picklist was updated by someone else — showing the latest version.');
      } else {
        dirtyRef.current = false;
        setDoc((prev) =>
          prev && prev.id === result.picklist.id
            ? { ...prev, version: result.picklist.version }
            : prev,
        );
        setStatusText('');
      }
      setErrorText('');
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to save picklist.');
    } finally {
      setSaving(false);
    }
  }, []);

  const scheduleSave = useCallback(() => {
    dirtyRef.current = true;
    if (saveTimerRef.current != null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      void pushSave();
    }, SAVE_DEBOUNCE_MS);
  }, [pushSave]);

  useEffect(
    () => () => {
      if (saveTimerRef.current != null) window.clearTimeout(saveTimerRef.current);
    },
    [],
  );

  /* ---- Live mode polling ------------------------------------------ */

  useEffect(() => {
    if (!doc?.live_mode || doc.id == null) return;
    const interval = window.setInterval(async () => {
      if (dirtyRef.current) return; // don't clobber pending edits
      try {
        const result = await getPicklist(doc.id);
        setDoc((prev) => {
          if (!prev || prev.id !== result.picklist.id) return prev;
          if (dirtyRef.current) return prev;
          return result.picklist.version > prev.version ? result.picklist : prev;
        });
      } catch {
        // Transient polling failure — next tick retries.
      }
    }, LIVE_POLL_MS);
    return () => window.clearInterval(interval);
  }, [doc?.live_mode, doc?.id]);

  /* ---- Mutations --------------------------------------------------- */

  const mutateSlots = useCallback(
    (updater: (slots: PicklistSlot[]) => PicklistSlot[]) => {
      setDoc((prev) => {
        if (!prev) return prev;
        return { ...prev, slots: updater(prev.slots.slice()) };
      });
      scheduleSave();
    },
    [scheduleSave],
  );

  async function handleCreate(seedFromRatings: boolean) {
    if (!eventKey) return;
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
      setPicklists((prev) => [result.picklist, ...prev]);
      setActiveId(result.picklist.id);
      setDoc(result.picklist);
      dirtyRef.current = false;
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to create picklist.');
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete() {
    if (!doc) return;
    if (!window.confirm(`Delete "${doc.title}"? This cannot be undone.`)) return;
    try {
      await deletePicklist(doc.id);
      setPicklists((prev) => prev.filter((p) => p.id !== doc.id));
      setActiveId(null);
      setDoc(null);
      if (eventKey) void fetchPicklists(eventKey);
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to delete picklist.');
    }
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
    setDoc((prev) => (prev ? { ...prev, live_mode: !prev.live_mode } : prev));
    scheduleSave();
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
        <SurfaceCardGroup groupId="picklist-builder">
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
            {statusText ? <p className="center-callout muted">{statusText}</p> : null}

            {eventKey ? (
              <div className="picklist-toolbar">
                {picklists.length > 0 ? (
                  <select
                    className="center-input picklist-select"
                    value={activeId ?? ''}
                    onChange={(event) => setActiveId(Number(event.target.value))}
                    aria-label="Select picklist"
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
                  disabled={loading || teamPool.length === 0}
                  title="Create a picklist pre-ranked by team ratings"
                >
                  New from ratings
                </button>
                <button
                  type="button"
                  className="center-btn ghost"
                  onClick={() => void handleCreate(false)}
                  disabled={loading}
                >
                  New empty
                </button>
                {doc ? (
                  <>
                    <button type="button" className="center-btn ghost" onClick={addMissingTeams}>
                      Add missing teams
                    </button>
                    <button type="button" className="center-btn ghost" onClick={() => window.print()}>
                      Print / PDF
                    </button>
                    <button
                      type="button"
                      className="center-btn ghost danger"
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
                  : `${slots.length} teams ranked. Drag or use arrows to reorder.`
              }
              right={
                <span className="picklist-header-right">
                  {saving ? <span className="center-chip">Saving…</span> : null}
                  <button
                    type="button"
                    className={`center-btn ${doc.live_mode ? 'danger' : ''}`}
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
                <ol className="picklist-rows">
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
                        <span className="picklist-rank" aria-label={slot.tier === 'dnp' ? 'Do not pick' : `Rank ${rank}`}>
                          {slot.tier === 'dnp' ? 'DNP' : rank}
                        </span>
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
                                disabled={index === 0}
                              >
                                Up
                              </button>
                              <button
                                type="button"
                                className="center-btn ghost picklist-mini-btn"
                                onClick={() => moveSlot(index, 1)}
                                aria-label={`Move ${slot.team_key} down`}
                                disabled={index === slots.length - 1}
                              >
                                Down
                              </button>
                            </>
                          )}
                        </span>

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
                FRCMOB rating — then drag teams into your preferred order.
              </p>
            </SurfaceCard>
          ) : null}
        </SurfaceCardGroup>
      </div>
      {isMobile ? <div className="picklist-mobile-spacer" aria-hidden="true" /> : null}
    </>
  );
}
