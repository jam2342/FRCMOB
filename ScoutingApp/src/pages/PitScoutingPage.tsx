import { useEffect, useMemo, useRef, useState } from 'react';
import {
  QueuedForSyncError,
  deletePitPhoto,
  getEventTeamsIntel,
  listPitEntries,
  pitPhotoDisplayUrl,
  upsertPitEntry,
  uploadPitPhoto,
} from '../api';
import type { PitScoutingEntry } from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { SCOUTING_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { PIT_FORM_SECTIONS } from '../config/gameFields';
import type { PitFieldDef } from '../config/gameFields';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { hapticSuccess, hapticTap } from '../utils/haptics';
import { asRecord, parseNumber } from './centerUtils';
import './PitScoutingPage.css';
import { useWorkspace } from '../features/workspace/useWorkspace';
import { readPitDraft, savePitDraft, clearConfirmedPitDraft, readPitSelection, rememberPitSelection, pitDraftPersisted } from '../features/offline/pitDrafts';
import { WorkspaceGate } from '../features/workspace/WorkspaceGate';

/* ------------------------------------------------------------------ */
/*  Constants & helpers                                                */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'scouting_center_event_key';
const SCOUT_PROFILE_STORAGE = 'scouting_manual_profile_v1';
/** Photos get downscaled client-side so uploads stay small on venue WiFi. */
const PHOTO_MAX_DIMENSION = 1280;
const PHOTO_JPEG_QUALITY = 0.82;

type TeamInfo = {
  team_key: string;
  team_number: number;
  nickname: string | null;
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

/** Downscale + re-encode a photo file to a JPEG data URL. */
async function compressPhoto(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, PHOTO_MAX_DIMENSION / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.drawImage(bitmap, 0, 0, width, height);
    return canvas.toDataURL('image/jpeg', PHOTO_JPEG_QUALITY);
  } finally {
    bitmap.close();
  }
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

function PitScoutingWorkspacePage() {
  const workspaceId = useWorkspace()!.workspace.id;
  const { eventKey, fetchTrigger, eventInput, setEventInput, commitInput, selectEvent } =
    useEventKeyParam(STORAGE_KEY);

  const [teams, setTeams] = useState<TeamInfo[]>([]);
  const [entriesByTeam, setEntriesByTeam] = useState<Map<string, PitScoutingEntry>>(new Map());
  const [selectedTeam, setSelectedTeam] = useState<string>('');
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [formDirty, setFormDirty] = useState(false);
  const [serverNotesReady, setServerNotesReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [savingForm, setSavingForm] = useState(false);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [statusText, setStatusText] = useState('');
  const [waitingForSync, setWaitingForSync] = useState(false);
  const queuedSaveRef = useRef<{ workspaceId: number; eventKey: string; team: string; form: Record<string, unknown>; editVersion: number } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const editVersionRef = useRef(0);
  const formRef = useRef<Record<string,unknown>>({});
  const contextRef = useRef({eventKey,selectedTeam});
  contextRef.current = {eventKey,selectedTeam};
  const errorRef = useRef<HTMLParagraphElement | null>(null);
  const [draftStored, setDraftStored] = useState(false);

  const selectedEntry = selectedTeam ? entriesByTeam.get(selectedTeam) ?? null : null;

  /* ---- Data loading ---------------------------------------------- */

  useEffect(() => {
    let cancelled = false;
    setSelectedTeam(''); setTeams([]); setEntriesByTeam(new Map()); setErrorText(''); setStatusText('');
    if (!eventKey) {setLoading(false); return;}
    setLoading(true); setServerNotesReady(false);
    void Promise.all([
      getEventTeamsIntel(eventKey, {include_tba:true,include_statbotics:false,include_season_fallback:false,include_rating_details:false,include_rating_signals:false}),
      listPitEntries(eventKey),
    ]).then(([payload, result]) => {
      if (cancelled) return;
      setServerNotesReady(true);
      setTeams((payload.teams ?? []).map(entry => {
        const row = asRecord(entry);
        return {team_key:String(row?.team_key || '').toLowerCase(),team_number:parseNumber(row?.team_number) ?? 0,nickname:typeof row?.nickname === 'string' ? row.nickname : null};
      }).filter(row=>row.team_key).sort((a,b)=>a.team_number-b.team_number));
      setEntriesByTeam(new Map((result.entries ?? []).map(entry=>[entry.team_key.toLowerCase(),entry])));
      const lastTeam = readPitSelection(workspaceId,eventKey);
      if (lastTeam && (payload.teams ?? []).some(team => String(team.team_key || '').toLowerCase() === lastTeam)) setSelectedTeam(lastTeam);
    }).catch(err => {
      if (cancelled) return;
      const lastTeam = readPitSelection(workspaceId,eventKey);
      if (lastTeam && readPitDraft(workspaceId,eventKey,lastTeam)) {
        setTeams([{team_key:lastTeam,team_number:Number(lastTeam.slice(3)),nickname:'Saved draft'}]); setSelectedTeam(lastTeam);
        setErrorText('Server notes could not be loaded. Your local draft is open. Reload the event after reconnecting before sharing it.');
      } else setErrorText('Could not load teams and saved pit notes. Try loading the event again. '+((err as Error).message || ''));
    }).finally(()=>{if(!cancelled) setLoading(false);});
    return () => { cancelled = true; };
  }, [eventKey, fetchTrigger, workspaceId]);

  useEffect(() => {
    if (!selectedTeam) return;
    const draft = readPitDraft(workspaceId,eventKey,selectedTeam);
    const payload = draft ?? entriesByTeam.get(selectedTeam)?.payload ?? {};
    formRef.current = {...payload}; setForm(formRef.current);
    setFormDirty(Boolean(draft)); setDraftStored(pitDraftPersisted(workspaceId,eventKey,selectedTeam));
    editVersionRef.current = 0; setStatusText('');
    // Entries are loaded before team selection; updates must not overwrite edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTeam,eventKey,workspaceId]);

  useEffect(() => {
    if (!formDirty || draftStored) return;
    const warn = (event: BeforeUnloadEvent) => {event.preventDefault();};
    window.addEventListener('beforeunload',warn);return ()=>window.removeEventListener('beforeunload',warn);
  }, [formDirty,draftStored]);

  useEffect(() => { if(errorText) errorRef.current?.scrollIntoView({block:'nearest'}); }, [errorText]);

  /* ---- Stats ------------------------------------------------------ */

  const completion = useMemo(() => {
    const done = teams.filter((team) => {
      const entry = entriesByTeam.get(team.team_key);
      return entry && Object.keys(entry.payload ?? {}).length > 0;
    }).length;
    return { done, total: teams.length };
  }, [teams, entriesByTeam]);

  /* ---- Actions ----------------------------------------------------- */

  function setField(key: string, value: unknown) {
    const next = {...formRef.current,[key]:value};
    formRef.current = next; setForm(next);
    const stored = savePitDraft(workspaceId,eventKey,selectedTeam,next);
    setDraftStored(stored);
    if (!stored) setErrorText('This device could not save your draft. Keep this page open and use Save entry before leaving.');
    setFormDirty(true);
    editVersionRef.current += 1;
  }

  function selectTeamForEditing(teamKey: string) {
    if (teamKey === selectedTeam) return;
    if (formDirty && !draftStored && !window.confirm('This draft could not be saved on your device. Discard it and switch teams?')) return;
    rememberPitSelection(workspaceId,eventKey,teamKey);
    setSelectedTeam(teamKey);
  }

  async function handleSave() {
    if (!eventKey || !selectedTeam) return;
    if (savingForm || !serverNotesReady) return;
    const invalid = PIT_FORM_SECTIONS.flatMap(section=>section.fields).find(field=>field.type === 'number' && typeof formRef.current[field.key] === 'number' && Number(formRef.current[field.key]) < 0);
    if (invalid) {setErrorText(`${invalid.label} must be zero or a positive number.`); return;}
    setSavingForm(true);
    setErrorText('');
    const savedEditVersion = editVersionRef.current;
    const savedForm = {...formRef.current};
    try {
      const result = await upsertPitEntry({
        event_key: eventKey,
        team_key: selectedTeam,
        scout_profile: readScoutProfile() || undefined,
        payload: savedForm,
      });
      if (contextRef.current.eventKey !== eventKey) return;
      setEntriesByTeam((prev) => {
        const next = new Map(prev);
        next.set(result.entry.team_key.toLowerCase(), result.entry);
        return next;
      });
      clearConfirmedPitDraft(workspaceId,eventKey,selectedTeam,savedForm);
      if (contextRef.current.selectedTeam === selectedTeam && editVersionRef.current === savedEditVersion) {setFormDirty(false);setDraftStored(false);}
      hapticSuccess();
      setStatusText(`Saved pit entry for #${teamNumber(selectedTeam)}.`);
    } catch (err) {
      if (err instanceof QueuedForSyncError) {
        // Not an error: kept on this phone and replayed on reconnect.
        setStatusText(`Saved #${teamNumber(selectedTeam)} on this phone. It will sync to your team when you're back online.`);
        queuedSaveRef.current = { workspaceId, eventKey, team: selectedTeam, form: savedForm, editVersion: savedEditVersion };
        // The queue holds this save now, so nothing is unsaved; the draft note
        // ("Save entry shares it") contradicted the save the scout just made.
        // The stored draft stays as a backup until the queue confirms.
        if (contextRef.current.selectedTeam === selectedTeam && editVersionRef.current === savedEditVersion) setFormDirty(false);
        setWaitingForSync(true);
      } else setErrorText((err as Error).message || 'Failed to save pit entry.');
    } finally {
      setSavingForm(false);
    }
  }

  // The queued message used to stay up after the edit had synced.
  useEffect(() => {
    if (!waitingForSync) return;
    const onChange = (event: Event) => {
      if ((event as CustomEvent<{ count: number }>).detail?.count === 0) {
        const queued = queuedSaveRef.current;
        queuedSaveRef.current = null;
        if (queued) {
          // Same as a save that reached the server: the draft is done unless the
          // scout kept editing after saving.
          clearConfirmedPitDraft(queued.workspaceId, queued.eventKey, queued.team, queued.form);
          if (contextRef.current.selectedTeam === queued.team && editVersionRef.current === queued.editVersion) {
            setFormDirty(false);
            setDraftStored(false);
          }
        }
        setWaitingForSync(false);
        setStatusText('Synced with your team.');
      }
    };
    window.addEventListener('offlinequeue:change', onChange);
    return () => window.removeEventListener('offlinequeue:change', onChange);
  }, [waitingForSync]);

  async function handlePhotoSelected(file: File | null) {
    if (!file || !eventKey || !selectedTeam) return;
    if (!file.type.startsWith('image/')) {setErrorText('Choose an image file, such as a JPG or PNG.'); if(fileInputRef.current) fileInputRef.current.value=''; return;}
    setUploadingPhoto(true);
    setErrorText('');
    try {
      let dataUrl: string;
      try {dataUrl = await compressPhoto(file);} catch {throw new Error('Could not read this photo. Choose another JPG or PNG and try again.');}
      const result = await uploadPitPhoto({
        event_key: eventKey,
        team_key: selectedTeam,
        scout_profile: readScoutProfile() || undefined,
        image_base64: dataUrl,
      });
      setEntriesByTeam((prev) => {
        const next = new Map(prev);
        next.set(result.entry.team_key.toLowerCase(), result.entry);
        return next;
      });
      hapticSuccess();
      setStatusText(`Photo added for #${teamNumber(selectedTeam)}.`);
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to upload photo.');
    } finally {
      setUploadingPhoto(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  async function handleDeletePhoto(photoPath: string) {
    if (!eventKey || !selectedTeam) return;
    if (!window.confirm('Delete this photo?')) return;
    try {
      const result = await deletePitPhoto({
        event_key: eventKey,
        team_key: selectedTeam,
        photo_path: photoPath,
      });
      setEntriesByTeam((prev) => {
        const next = new Map(prev);
        next.set(result.entry.team_key.toLowerCase(), result.entry);
        return next;
      });
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to delete photo.');
    }
  }

  /* ---- Field rendering --------------------------------------------- */

  function renderField(field: PitFieldDef) {
    const value = form[field.key];
    switch (field.type) {
      case 'select':
        return (
          <select
            className="center-input"
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => setField(field.key, event.target.value)}
          >
            <option value="">—</option>
            {(field.options ?? []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        );
      case 'multiselect': {
        const selected = Array.isArray(value) ? (value as string[]) : [];
        return (
          <div className="pit-multiselect">
            {(field.options ?? []).map((option) => {
              const active = selected.includes(option);
              return (
                <button
                  key={option}
                  type="button"
                  aria-pressed={active}
                  className={`center-chip clickable ${active ? 'pit-chip-active' : ''}`}
                  onClick={() => {
                    hapticTap();
                    setField(
                      field.key,
                      active ? selected.filter((item) => item !== option) : [...selected, option],
                    );
                  }}
                >
                  {option}
                </button>
              );
            })}
          </div>
        );
      }
      case 'toggle':
        return (
          <button
            type="button"
            aria-pressed={value === true}
            className={`center-btn ${value === true ? '' : 'ghost'}`}
            onClick={() => {
              hapticTap();
              setField(field.key, value !== true);
            }}
          >
            {value === true ? 'Yes' : 'No'}
          </button>
        );
      case 'number':
        return (
          <div className="pit-number-wrap">
            <input
              className="center-input"
              type="number"
              min={0}
              inputMode="decimal"
              value={typeof value === 'number' ? value : typeof value === 'string' ? value : ''}
              onChange={(event) => {
                const parsed = event.target.value === '' ? null : Number(event.target.value);
                setField(field.key, parsed != null && Number.isFinite(parsed) ? parsed : null);
              }}
            />
            {field.unit ? <span className="pit-unit">{field.unit}</span> : null}
          </div>
        );
      case 'textarea':
        return (
          <textarea
            className="center-input pit-textarea"
            rows={3}
            placeholder={field.placeholder}
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => setField(field.key, event.target.value)}
          />
        );
      default:
        return (
          <input
            className="center-input"
            type="text"
            placeholder={field.placeholder}
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => setField(field.key, event.target.value)}
          />
        );
    }
  }

  /* ---- Render ------------------------------------------------------ */

  return (
    <>
      <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />
      <div className="center-page-container">
        <SurfaceCardGroup groupId="pit-scouting">
          <SurfaceCard
            title="Pit Scouting"
            subtitle="Robot specs, claimed capabilities, and photos — one entry per team."
            expandable={false}
            mobileCollapsible={false}
            right={
              completion.total > 0 ? (
                <span className="center-chip">
                  {completion.done}/{completion.total} teams done
                </span>
              ) : null
            }
          >
            <EventPicker
              value={eventKey}
              onSelect={key => {if(formDirty && !draftStored && !window.confirm('This draft could not be saved on your device. Discard it and change events?')) {setEventInput(eventKey);return;} selectEvent(key);}}
              inputValue={eventInput}
              onInputChange={setEventInput}
              onSubmit={() => {if(formDirty && !draftStored && !window.confirm('This draft could not be saved on your device. Discard it and reload teams?')) {setEventInput(eventKey);return;} commitInput();}}
              loading={loading}
              disabled={savingForm || uploadingPhoto}
            />
            {errorText && !selectedTeam ? <p ref={errorRef} className="center-callout warning" role="alert">{errorText}</p> : null}
            {statusText ? <p className="center-success-text" role="status">{statusText}</p> : null}
          </SurfaceCard>

          {teams.length > 0 ? (
            <SurfaceCard
              title="Teams"
              subtitle="Tap a team to fill out its pit form. Green = completed, camera = has photos."
              expandable={false}
              mobileCollapsible={false}
            >
              <div className="pit-team-grid">
                {teams.map((team) => {
                  const entry = entriesByTeam.get(team.team_key);
                  const hasForm = Boolean(entry && Object.keys(entry.payload ?? {}).length > 0);
                  const hasPhotos = Boolean(entry?.photos?.length);
                  return (
                    <button
                      key={team.team_key}
                      type="button"
                      className={[
                        'pit-team-cell',
                        hasForm ? 'pit-team-cell--done' : '',
                        selectedTeam === team.team_key ? 'pit-team-cell--active' : '',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                      disabled={savingForm || uploadingPhoto}
                      onClick={() => selectTeamForEditing(team.team_key)}
                      aria-label={`Team ${team.team_number}${hasForm ? ', completed' : ''}${hasPhotos ? ', has photos' : ''}`}
                      title={team.nickname ?? undefined}
                    >
                      <strong>{team.team_number}</strong>
                      {hasPhotos ? <span className="pit-photo-indicator" aria-hidden="true" /> : null}
                    </button>
                  );
                })}
              </div>
            </SurfaceCard>
          ) : null}

          {selectedTeam ? (
            <SurfaceCard
              title={`Team ${teamNumber(selectedTeam)}`}
              subtitle={
                teams.find((team) => team.team_key === selectedTeam)?.nickname ??
                'Pit scouting entry'
              }
              right={
                <button
                  type="button"
                  className="center-btn"
                  onClick={() => void handleSave()}
                  disabled={savingForm || !serverNotesReady}
                >
                  {savingForm ? 'Saving…' : 'Save entry'}
                </button>
              }
              expandable={false}
              mobileCollapsible={false}
            >
              {formDirty ? <p className="my-team__note" role="status">{draftStored ? "Draft saved on this device. Save entry shares it with your team." : "Unsaved changes — keep this page open until you save."}</p> : null}
              {errorText ? <p ref={errorRef} className="center-callout warning" role="alert">{errorText}</p> : null}
              {/* Photos */}
              <div className="pit-photos">
                {(selectedEntry?.photos ?? []).map((photo, index) => (
                  <figure key={photo} className="pit-photo-item">
                    <img src={selectedEntry ? pitPhotoDisplayUrl(selectedEntry, index) : ''} alt={`Robot ${teamNumber(selectedTeam)}`} loading="lazy" />
                    <button
                      type="button"
                      className="pit-photo-delete"
                      onClick={() => void handleDeletePhoto(photo)}
                      aria-label="Delete photo"
                      disabled={!serverNotesReady}
                    >
                      <span aria-hidden="true">x</span>
                    </button>
                  </figure>
                ))}
                <label className={`pit-photo-add ${uploadingPhoto ? 'busy' : ''}`}>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    capture="environment"
                    onChange={(event) => void handlePhotoSelected(event.target.files?.[0] ?? null)}
                    disabled={uploadingPhoto || !serverNotesReady}
                  />
                  {uploadingPhoto ? 'Uploading…' : '+ Photo'}
                </label>
              </div>

              {/* Schema-driven form */}
              {PIT_FORM_SECTIONS.map((section) => (
                <fieldset key={section.title} className="pit-section">
                  <legend>{section.title}</legend>
                  <div className="pit-fields">
                    {section.fields.map((field) => (
                      <label key={field.key} className="pit-field">
                        <span className="pit-field-label">{field.label}</span>
                        {renderField(field)}
                      </label>
                    ))}
                  </div>
                </fieldset>
              ))}

              <div className="pit-save-row">
                <button
                  type="button"
                  className="center-btn"
                  onClick={() => void handleSave()}
                  disabled={savingForm || !serverNotesReady}
                >
                  {savingForm ? 'Saving…' : 'Save entry'}
                </button>
              </div>
            </SurfaceCard>
          ) : null}
        </SurfaceCardGroup>
      </div>
    </>
  );
}

// Team-only: pit notes are private to a workspace, so nothing loads until
// the device has joined one.
export function PitScoutingPage() {
  return (
    <WorkspaceGate feature="Pit notes" viewBar={<PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />}>
      <PitScoutingWorkspacePage />
    </WorkspaceGate>
  );
}
