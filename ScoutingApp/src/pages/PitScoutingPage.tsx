import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  deletePitPhoto,
  getEventTeamsIntel,
  listPitEntries,
  resolveMediaUrl,
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

export function PitScoutingPage() {
  const { eventKey, eventInput, setEventInput, commitInput, selectEvent } =
    useEventKeyParam(STORAGE_KEY);

  const [teams, setTeams] = useState<TeamInfo[]>([]);
  const [entriesByTeam, setEntriesByTeam] = useState<Map<string, PitScoutingEntry>>(new Map());
  const [selectedTeam, setSelectedTeam] = useState<string>('');
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [savingForm, setSavingForm] = useState(false);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [errorText, setErrorText] = useState('');
  const [statusText, setStatusText] = useState('');
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const selectedEntry = selectedTeam ? entriesByTeam.get(selectedTeam) ?? null : null;

  /* ---- Data loading ---------------------------------------------- */

  const fetchTeams = useCallback(async (key: string) => {
    setLoading(true);
    setErrorText('');
    try {
      const payload = await getEventTeamsIntel(key, {
        include_tba: true,
        include_statbotics: false,
        include_season_fallback: false,
        include_rating_details: false,
        include_rating_signals: false,
      });
      const rows = (Array.isArray(payload.teams) ? payload.teams : [])
        .map((entry) => {
          const row = asRecord(entry);
          return {
            team_key: String(row?.team_key || '').toLowerCase(),
            team_number: parseNumber(row?.team_number) ?? 0,
            nickname: typeof row?.nickname === 'string' ? row.nickname : null,
          };
        })
        .filter((row) => row.team_key.length > 0)
        .sort((a, b) => a.team_number - b.team_number);
      setTeams(rows);
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to load event teams.');
      setTeams([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchEntries = useCallback(async (key: string) => {
    try {
      const result = await listPitEntries(key);
      const map = new Map<string, PitScoutingEntry>();
      for (const entry of result.entries ?? []) {
        map.set(entry.team_key.toLowerCase(), entry);
      }
      setEntriesByTeam(map);
    } catch {
      // Pit data may simply not exist yet — leave the map empty.
    }
  }, []);

  useEffect(() => {
    if (!eventKey) return;
    setSelectedTeam('');
    void fetchTeams(eventKey);
    void fetchEntries(eventKey);
  }, [eventKey, fetchTeams, fetchEntries]);

  useEffect(() => {
    if (!selectedTeam) return;
    const entry = entriesByTeam.get(selectedTeam);
    setForm(entry?.payload ? { ...entry.payload } : {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTeam]);

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
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    if (!eventKey || !selectedTeam) return;
    setSavingForm(true);
    setErrorText('');
    try {
      const result = await upsertPitEntry({
        event_key: eventKey,
        team_key: selectedTeam,
        scout_profile: readScoutProfile() || undefined,
        payload: form,
      });
      setEntriesByTeam((prev) => {
        const next = new Map(prev);
        next.set(result.entry.team_key.toLowerCase(), result.entry);
        return next;
      });
      hapticSuccess();
      setStatusText(`Saved pit entry for #${teamNumber(selectedTeam)}.`);
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to save pit entry.');
    } finally {
      setSavingForm(false);
    }
  }

  async function handlePhotoSelected(file: File | null) {
    if (!file || !eventKey || !selectedTeam) return;
    setUploadingPhoto(true);
    setErrorText('');
    try {
      const dataUrl = await compressPhoto(file);
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
              onSelect={selectEvent}
              inputValue={eventInput}
              onInputChange={setEventInput}
              onSubmit={commitInput}
              loading={loading}
            />
            {errorText ? <p className="center-callout warning">{errorText}</p> : null}
            {statusText ? <p className="center-success-text">{statusText}</p> : null}
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
                      onClick={() => setSelectedTeam(team.team_key)}
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
                  disabled={savingForm}
                >
                  {savingForm ? 'Saving…' : 'Save entry'}
                </button>
              }
              expandable={false}
              mobileCollapsible={false}
            >
              {/* Photos */}
              <div className="pit-photos">
                {(selectedEntry?.photos ?? []).map((photo) => (
                  <figure key={photo} className="pit-photo-item">
                    <img src={resolveMediaUrl(photo)} alt={`Robot ${teamNumber(selectedTeam)}`} loading="lazy" />
                    <button
                      type="button"
                      className="pit-photo-delete"
                      onClick={() => void handleDeletePhoto(photo)}
                      aria-label="Delete photo"
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
                    disabled={uploadingPhoto}
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
                  disabled={savingForm}
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
