import { useEffect, useMemo, useRef, useState } from 'react';
import {
  QueuedForSyncError,
  deletePitPhoto,
  getEventTeamsIntel,
  listPitEntries,
  upsertPitEntry,
  uploadPitPhoto,
} from '../../api';
import type { PitScoutingEntry } from '../../api';
import { PIT_FORM_SECTIONS } from '../../config/gameFields';
import { useEventKeyParam } from '../../hooks/useEventKeyParam';
import { asRecord, parseNumber } from '../../pages/centerUtils';
import { listQueuedMutations } from '../../utils/offlineQueue';
import { hapticSuccess } from '../../utils/haptics';
import { useWorkspace } from '../workspace/useWorkspace';
import { readPitDraft, readPitDraftSavedAt, savePitDraft, clearConfirmedPitDraft, readPitSelection, rememberPitSelection, pitDraftPersisted } from '../offline/pitDrafts';
import { hasPitEntry, nextUnscoutedTeam, teamNumber } from './pitTeams';
import type { PitTeam } from './pitTeams';
import type { PitSaveStatus } from './PitSaveBar';

const STORAGE_KEY = 'scouting_center_event_key';
const SCOUT_PROFILE_STORAGE = 'scouting_manual_profile_v1';
/** Photos get downscaled client-side so uploads stay small on venue WiFi. */
const PHOTO_MAX_DIMENSION = 1280;
const PHOTO_JPEG_QUALITY = 0.82;

type StatusNote = { tone: 'saved' | 'queued' | 'warn'; text: string };
type SaveOutcome = 'saved' | 'queued' | 'failed';

function readScoutProfile(): string {
  try {
    return String(window.localStorage.getItem(SCOUT_PROFILE_STORAGE) || '').trim();
  } catch {
    return '';
  }
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

export function usePitScouting() {
  const session = useWorkspace()!;
  const workspaceId = session.workspace.id;
  // Credit the member who is signed in on this device; the name typed into the manual
  // scouting profile belongs to whoever used this phone last.
  const memberName = String(session.me?.display_name ?? '').trim();
  const { eventKey, fetchTrigger, eventInput, setEventInput, commitInput, selectEvent } =
    useEventKeyParam(STORAGE_KEY);

  const [teams, setTeams] = useState<PitTeam[]>([]);
  const [entriesByTeam, setEntriesByTeam] = useState<Map<string, PitScoutingEntry>>(new Map());
  const [selectedTeam, setSelectedTeam] = useState<string>('');
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [formDirty, setFormDirty] = useState(false);
  const [serverNotesReady, setServerNotesReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [savingForm, setSavingForm] = useState(false);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [note, setNote] = useState<StatusNote | null>(null);
  const [waitingForSync, setWaitingForSync] = useState(false);
  const [draftStored, setDraftStored] = useState(false);
  const queuedSaveRef = useRef<{ id: string; workspaceId: number; eventKey: string; team: string; form: Record<string, unknown>; editVersion: number } | null>(null);
  const carriedNoteRef = useRef<StatusNote | null>(null);
  const editVersionRef = useRef(0);
  const formRef = useRef<Record<string, unknown>>({});
  const contextRef = useRef({ eventKey, selectedTeam });
  contextRef.current = { eventKey, selectedTeam };

  const selectedEntry = selectedTeam ? entriesByTeam.get(selectedTeam) ?? null : null;
  const scoutName = memberName || readScoutProfile() || undefined;

  /* ---- Data loading ---------------------------------------------- */

  useEffect(() => {
    let cancelled = false;
    setSelectedTeam(''); setTeams([]); setEntriesByTeam(new Map()); setErrorText(''); setNote(null);
    if (!eventKey) { setLoading(false); return; }
    setLoading(true); setServerNotesReady(false);
    void Promise.all([
      getEventTeamsIntel(eventKey, { include_tba: true, include_season_fallback: false, include_rating_details: false, include_rating_signals: false }),
      listPitEntries(eventKey),
    ]).then(([payload, result]) => {
      if (cancelled) return;
      setServerNotesReady(true);
      setTeams((payload.teams ?? []).map(entry => {
        const row = asRecord(entry);
        return { team_key: String(row?.team_key || '').toLowerCase(), team_number: parseNumber(row?.team_number) ?? 0, nickname: typeof row?.nickname === 'string' ? row.nickname : null };
      }).filter(row => row.team_key).sort((a, b) => a.team_number - b.team_number));
      setEntriesByTeam(new Map((result.entries ?? []).map(entry => [entry.team_key.toLowerCase(), entry])));
      const lastTeam = readPitSelection(workspaceId, eventKey);
      if (lastTeam && (payload.teams ?? []).some(team => String(team.team_key || '').toLowerCase() === lastTeam)) setSelectedTeam(lastTeam);
    }).catch(err => {
      if (cancelled) return;
      const lastTeam = readPitSelection(workspaceId, eventKey);
      if (lastTeam && readPitDraft(workspaceId, eventKey, lastTeam)) {
        setTeams([{ team_key: lastTeam, team_number: Number(lastTeam.slice(3)), nickname: 'Saved draft' }]); setSelectedTeam(lastTeam);
        setErrorText('Server notes could not be loaded. Your local draft is open. Reload the event after reconnecting before sharing it.');
      } else setErrorText('Could not load teams and saved pit notes. Try loading the event again. ' + ((err as Error).message || ''));
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [eventKey, fetchTrigger, workspaceId]);

  useEffect(() => {
    if (!selectedTeam) return;
    const draft = readPitDraft(workspaceId, eventKey, selectedTeam);
    const payload = draft ?? entriesByTeam.get(selectedTeam)?.payload ?? {};
    formRef.current = { ...payload }; setForm(formRef.current);
    setFormDirty(Boolean(draft)); setDraftStored(pitDraftPersisted(workspaceId, eventKey, selectedTeam));
    editVersionRef.current = 0;
    // A teammate saved after this draft was written: say so before a save replaces theirs.
    const serverUpdated = Date.parse(entriesByTeam.get(selectedTeam)?.updated_at ?? '');
    const draftSavedAt = draft ? readPitDraftSavedAt(workspaceId, eventKey, selectedTeam) : null;
    const carried = carriedNoteRef.current;
    carriedNoteRef.current = null;
    setNote(draftSavedAt !== null && Number.isFinite(serverUpdated) && serverUpdated > draftSavedAt
      ? { tone: 'warn', text: "A teammate saved newer notes for this team after your draft. You're seeing your draft; saving it replaces their notes." }
      : carried);
    // Entries are loaded before team selection; updates must not overwrite edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTeam, eventKey, workspaceId]);

  useEffect(() => {
    if (!formDirty || draftStored) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [formDirty, draftStored]);

  /* ---- Stats ------------------------------------------------------ */

  const completion = useMemo(() => ({
    done: teams.filter(team => hasPitEntry(entriesByTeam.get(team.team_key))).length,
    total: teams.length,
  }), [teams, entriesByTeam]);

  /* ---- Actions ----------------------------------------------------- */

  function setField(key: string, value: unknown) {
    const next = { ...formRef.current, [key]: value };
    if (value === undefined) delete next[key];
    formRef.current = next; setForm(next);
    const stored = savePitDraft(workspaceId, eventKey, selectedTeam, next);
    setDraftStored(stored);
    if (!stored) setErrorText('This device could not save your draft. Keep this page open and use Save before leaving.');
    setFormDirty(true);
    editVersionRef.current += 1;
    // "Saved" no longer describes the form once it changes.
    setNote(current => (current?.tone === 'warn' ? current : null));
  }

  function openTeam(teamKey: string) {
    rememberPitSelection(workspaceId, eventKey, teamKey);
    setSelectedTeam(teamKey);
  }

  function selectTeamForEditing(teamKey: string) {
    if (teamKey === selectedTeam) return;
    if (formDirty && !draftStored && !window.confirm('This draft could not be saved on your device. Discard it and switch teams?')) return;
    openTeam(teamKey);
  }

  async function save(): Promise<SaveOutcome> {
    if (!eventKey || !selectedTeam) return 'failed';
    if (savingForm || !serverNotesReady) return 'failed';
    const invalid = PIT_FORM_SECTIONS.flatMap(section => section.fields).find(field => field.type === 'number' && typeof formRef.current[field.key] === 'number' && Number(formRef.current[field.key]) < 0);
    if (invalid) { setErrorText(`${invalid.label} must be zero or a positive number.`); return 'failed'; }
    setSavingForm(true);
    setErrorText('');
    const saveStarted = Date.now();
    const savedEditVersion = editVersionRef.current;
    const savedForm = { ...formRef.current };
    try {
      const result = await upsertPitEntry({
        event_key: eventKey,
        team_key: selectedTeam,
        scout_profile: scoutName,
        payload: savedForm,
      });
      if (contextRef.current.eventKey !== eventKey) return 'failed';
      setEntriesByTeam((prev) => {
        const next = new Map(prev);
        next.set(result.entry.team_key.toLowerCase(), result.entry);
        return next;
      });
      clearConfirmedPitDraft(workspaceId, eventKey, selectedTeam, savedForm);
      if (contextRef.current.selectedTeam === selectedTeam && editVersionRef.current === savedEditVersion) { setFormDirty(false); setDraftStored(false); }
      hapticSuccess();
      setNote({ tone: 'saved', text: `Saved pit entry for #${teamNumber(selectedTeam)}.` });
      return 'saved';
    } catch (err) {
      if (err instanceof QueuedForSyncError) {
        // Not an error: kept on this phone and replayed on reconnect.
        setNote({ tone: 'queued', text: `Saved #${teamNumber(selectedTeam)} on this phone. It will sync to your team when you're back online.` });
        const queued = (await listQueuedMutations()).filter(item => {
          if (!item.url.endsWith('/pit-scouting') || Date.parse(item.queuedAt) < saveStarted) return false;
          try {
            const body = JSON.parse(item.body || '{}');
            return body.event_key === eventKey && body.team_key === selectedTeam && JSON.stringify(body.payload) === JSON.stringify(savedForm);
          } catch { return false; }
        }).at(-1);
        queuedSaveRef.current = queued ? { id: queued.id, workspaceId, eventKey, team: selectedTeam, form: savedForm, editVersion: savedEditVersion } : null;
        // The queue holds this save now, so nothing is unsaved; the draft note
        // ("Save shares it") contradicted the save the scout just made.
        // The stored draft stays as a backup until the queue confirms.
        if (contextRef.current.selectedTeam === selectedTeam && editVersionRef.current === savedEditVersion) setFormDirty(false);
        setWaitingForSync(true);
        return 'queued';
      }
      setErrorText((err as Error).message || 'Failed to save pit entry.');
      return 'failed';
    } finally {
      setSavingForm(false);
    }
  }

  async function saveAndNext() {
    const team = selectedTeam;
    const outcome = await save();
    if (outcome === 'failed' || contextRef.current.selectedTeam !== team) return;
    const next = nextUnscoutedTeam(teams, entriesByTeam, team);
    if (!next) {
      setNote({ tone: 'saved', text: `Saved #${teamNumber(team)}. Every team has notes now.` });
      return;
    }
    carriedNoteRef.current = {
      tone: outcome === 'queued' ? 'queued' : 'saved',
      text: outcome === 'queued' ? `Saved #${teamNumber(team)} on this phone, will sync. Now on #${teamNumber(next)}.` : `Saved #${teamNumber(team)}. Now on #${teamNumber(next)}.`,
    };
    openTeam(next);
  }

  // Queue counts exclude rejected writes; confirm this save by its own id.
  useEffect(() => {
    if (!waitingForSync) return;
    let cancelled = false;
    const onChange = async () => {
      const queued = queuedSaveRef.current;
      const rows = await listQueuedMutations();
      if (!queued) {
        // The save couldn't be matched to its queue row; fall back to "everything sent".
        if (cancelled) return;
        if (rows.length === 0) {
          setWaitingForSync(false);
          setNote({ tone: 'saved', text: 'Synced with your team.' });
        }
        return;
      }
      const row = rows.find(item => item.id === queued.id);
      if (cancelled || queuedSaveRef.current !== queued) return;
      if (row?.failure) {
        setWaitingForSync(false);
        setNote(null);
        setErrorText("Your team's server didn't accept this save. Your notes are still here; try saving again.");
        if (contextRef.current.eventKey === queued.eventKey && contextRef.current.selectedTeam === queued.team) setFormDirty(true);
        return;
      }
      if (!row) {
        queuedSaveRef.current = null;
        // Same as a save that reached the server: the draft is done unless the
        // scout kept editing after saving.
        clearConfirmedPitDraft(queued.workspaceId, queued.eventKey, queued.team, queued.form);
        if (contextRef.current.eventKey === queued.eventKey && contextRef.current.selectedTeam === queued.team && editVersionRef.current === queued.editVersion) {
          setFormDirty(false);
          setDraftStored(false);
        }
        setWaitingForSync(false);
        setNote({ tone: 'saved', text: 'Synced with your team.' });
      }
    };
    const check = () => { void onChange(); };
    window.addEventListener('offlinequeue:change', check);
    window.addEventListener('offlinequeue:dropped', check);
    check();
    return () => {
      cancelled = true;
      window.removeEventListener('offlinequeue:change', check);
      window.removeEventListener('offlinequeue:dropped', check);
    };
  }, [waitingForSync]);

  async function addPhoto(file: File | null) {
    if (!file || !eventKey || !selectedTeam) return;
    if (!file.type.startsWith('image/')) { setErrorText('Choose an image file, such as a JPG or PNG.'); return; }
    setUploadingPhoto(true);
    setErrorText('');
    try {
      let dataUrl: string;
      try { dataUrl = await compressPhoto(file); } catch { throw new Error('Could not read this photo. Choose another JPG or PNG and try again.'); }
      const result = await uploadPitPhoto({
        event_key: eventKey,
        team_key: selectedTeam,
        scout_profile: scoutName,
        image_base64: dataUrl,
      });
      setEntriesByTeam((prev) => {
        const next = new Map(prev);
        next.set(result.entry.team_key.toLowerCase(), result.entry);
        return next;
      });
      hapticSuccess();
      setNote({ tone: 'saved', text: `Photo added for #${teamNumber(selectedTeam)}.` });
    } catch (err) {
      // Photos aren't queued for later like notes are (they're megabytes each), so say
      // plainly that this one didn't stick, instead of a browser "Failed to fetch".
      const offline = typeof navigator !== 'undefined' && navigator.onLine === false;
      const network = err instanceof TypeError || /failed to fetch|load failed|network/i.test((err as Error).message || '');
      setErrorText(offline || network
        ? "No connection, so this photo wasn't saved. Photos need signal; take it again once you're back online. Your notes still save on this phone."
        : (err as Error).message || 'Failed to upload photo.');
    } finally {
      setUploadingPhoto(false);
    }
  }

  async function deletePhoto(photoPath: string) {
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

  /* ---- Save bar status ---------------------------------------------- */

  const saveStatus: PitSaveStatus = (() => {
    if (errorText) return { tone: 'error', text: errorText };
    if (savingForm) return { tone: 'saving', text: 'Saving…' };
    if (note) return note;
    if (formDirty) {
      return draftStored
        ? { tone: 'dirty', text: 'Draft kept on this phone. Save shares it with your team.' }
        : { tone: 'dirty', text: 'Unsaved changes. Keep this page open until you save.' };
    }
    return hasPitEntry(selectedEntry) ? { tone: 'idle', text: 'Saved. No changes since.' } : { tone: 'idle', text: 'Not saved yet.' };
  })();

  return {
    eventKey, eventInput, setEventInput, commitInput, selectEvent,
    teams, entriesByTeam, selectedTeam, selectedEntry, form,
    formDirty, draftStored, serverNotesReady, loading, savingForm, uploadingPhoto,
    errorText, completion, saveStatus,
    setField, selectTeamForEditing, save, saveAndNext, addPhoto, deletePhoto,
  };
}
