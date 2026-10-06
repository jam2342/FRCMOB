import { usePageVisibility } from './usePageVisibility';
import { useSingleFlightPolling } from './useSingleFlightPolling';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import {
  approveAutoScoutDraft,
  generateAutoScoutDraft,
  getAutoScoutDraft,
  getTeamHeatmap,
  rejectAutoScoutDraft,
} from '../api';
import type {
  TeamHeatmapResponse,
} from '../api';
import {
  applyAutoScoutDraftPayload,
} from '../pages/scoutingPage.helpers';
import type {
  AutoScoutDraftRecord,
  AutoScoutFieldOverride,
  AutoScoutMeta,
  ScoutFormState,
} from '../pages/scoutingPage.types';

type UseAutoScoutDraftArgs = {
  enabled: boolean;
  eventKey: string;
  matchKey: string;
  teamKey: string;
  scoutProfile: string;
  form: ScoutFormState;
  notes: string;
  setForm: Dispatch<SetStateAction<ScoutFormState>>;
  setNotes: Dispatch<SetStateAction<string>>;
};

type FieldBadgeState = {
  label: 'Auto' | 'Low confidence' | 'Needs review' | 'Manual' | 'Blank';
  tone: 'auto' | 'warning' | 'review' | 'manual' | 'blank';
  confidence: number | null;
  evidenceCount: number;
};

function normalizeError(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message || fallback : fallback;
}

export function useAutoScoutDraft({
  enabled,
  eventKey,
  matchKey,
  teamKey,
  scoutProfile,
  form,
  notes,
  setForm,
  setNotes,
}: UseAutoScoutDraftArgs) {
  const [draft, setDraft] = useState<AutoScoutDraftRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState('');
  const [evidenceField, setEvidenceField] = useState<string | null>(null);
  const [heatmapData, setHeatmapData] = useState<TeamHeatmapResponse | null>(null);
  const [heatmapLoading, setHeatmapLoading] = useState(false);
  const [heatmapError, setHeatmapError] = useState('');
  const lastAppliedSignatureRef = useRef<string>('');

  const visible = usePageVisibility();
  const refreshInFlightRef = useRef(false);
  const ready = Boolean(eventKey && matchKey && teamKey);
  const draftSignature = draft ? `${draft.id}:${draft.draft_version}` : '';
  const managedFormFields = useMemo(
    () => Object.keys(draft?.draft_payload?.form_patch || {}),
    [draft],
  );

  useEffect(() => {
    setDraft(null);
    setError('');
    setEvidenceField(null);
    setHeatmapData(null);
    setHeatmapError('');
    lastAppliedSignatureRef.current = '';
  }, [eventKey, matchKey, teamKey]);

  const refreshDraft = useCallback(async () => {
    if (!ready || refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    try {
      const payload = await getAutoScoutDraft(eventKey, matchKey, teamKey);
      setDraft(payload.draft);
      setError('');
    } catch (nextError) {
      setError(normalizeError(nextError, 'Unable to load auto-scout draft.'));
      return false;
    } finally {
      refreshInFlightRef.current = false;
    }
  }, [eventKey, matchKey, ready, teamKey]);

  const generateDraft = useCallback(async (forceRegenerate = false) => {
    if (!ready) return;
    setLoading(true);
    try {
      const payload = await generateAutoScoutDraft({
        event_key: eventKey,
        match_key: matchKey,
        team_key: teamKey,
        force_regenerate: forceRegenerate,
      });
      setDraft(payload.draft);
      setError('');
    } catch (nextError) {
      setError(normalizeError(nextError, 'Unable to generate auto-scout draft.'));
    } finally {
      setLoading(false);
    }
  }, [eventKey, matchKey, ready, teamKey]);

  useEffect(() => {
    if (!enabled || !visible || !ready || draft || loading) return;
    void refreshDraft();
  }, [draft, enabled, loading, ready, refreshDraft, visible]);

  useSingleFlightPolling({
    enabled: enabled && ready && draft?.status === 'generating',
    visible,
    intervalMs: 3000,
    run: refreshDraft,
  });

  useEffect(() => {
    if (!enabled || !draft || !draftSignature) return;
    if (draft.status !== 'ready' && draft.status !== 'low_confidence') return;
    if (lastAppliedSignatureRef.current === draftSignature) return;
    setForm((current) => applyAutoScoutDraftPayload(current, draft.draft_payload));
    setNotes((current) => {
      if (current.trim()) return current;
      const seed = draft.draft_payload?.notes_seed?.trim() || '';
      return seed || current;
    });
    lastAppliedSignatureRef.current = draftSignature;
  }, [draft, draftSignature, enabled, setForm, setNotes]);

  const openEvidence = useCallback(async (fieldName: string) => {
    setEvidenceField(fieldName);
    if (heatmapData || heatmapLoading || !ready) return;
    setHeatmapLoading(true);
    try {
      // Drafts come from phone recordings; an operator reviewing them may be looking
      // at a recording that is itself still unreviewed.
      const payload = await getTeamHeatmap(teamKey, eventKey, {
        match_key: matchKey,
        include_unreviewed: true,
      });
      setHeatmapData(payload);
      setHeatmapError('');
    } catch (nextError) {
      setHeatmapError(normalizeError(nextError, 'Unable to load the recorded positions.'));
    } finally {
      setHeatmapLoading(false);
    }
  }, [eventKey, matchKey, ready, teamKey, heatmapData, heatmapLoading]);

  const closeEvidence = useCallback(() => setEvidenceField(null), []);

  const getFieldBadge = useCallback((fieldName: string): FieldBadgeState | null => {
    if (!draft) return null;
    const provenance = draft.field_provenance?.[fieldName] || 'needs_review';
    const confidence = typeof draft.field_confidence?.[fieldName] === 'number'
      ? draft.field_confidence[fieldName]
      : null;
    const evidenceCount = Array.isArray(draft.field_evidence_refs?.[fieldName])
      ? draft.field_evidence_refs[fieldName].length
      : 0;
    if (Object.prototype.hasOwnProperty.call(draft.draft_payload?.form_patch || {}, fieldName)) {
      const nextValue = (draft.draft_payload?.form_patch || {})[fieldName as keyof typeof draft.draft_payload.form_patch];
      if (form[fieldName as keyof ScoutFormState] !== nextValue) {
        return { label: 'Manual', tone: 'manual', confidence, evidenceCount };
      }
    }
    if (provenance === 'auto') {
      return {
        label: confidence !== null && confidence < 0.72 ? 'Low confidence' : 'Auto',
        tone: confidence !== null && confidence < 0.72 ? 'warning' : 'auto',
        confidence,
        evidenceCount,
      };
    }
    if (provenance === 'blank') {
      return { label: 'Blank', tone: 'blank', confidence, evidenceCount };
    }
    return { label: 'Needs review', tone: 'review', confidence, evidenceCount };
  }, [draft, form]);

  const prepareReviewedAutoSave = useCallback(async (): Promise<{
    autoScoutMeta: AutoScoutMeta;
    fieldOverrides: Record<string, AutoScoutFieldOverride> | null;
  } | null> => {
    if (!draft) return null;
    if (draft.status === 'approved') {
      return {
        autoScoutMeta: {
          draft_id: draft.id,
          mapper_version: draft.mapper_version,
          analysis_version: draft.analysis_version,
          approved_at_ms: draft.approved_at ? Date.parse(draft.approved_at) : null,
        },
        fieldOverrides: draft.field_overrides ?? null,
      };
    }
    if (draft.status !== 'ready' && draft.status !== 'low_confidence') return null;
    setApproving(true);
    try {
      const reviewedFormPatch = Object.fromEntries(
        managedFormFields.map((fieldName) => [fieldName, form[fieldName as keyof ScoutFormState]]),
      );
      const response = await approveAutoScoutDraft(draft.id, {
        approved_by: scoutProfile,
        draft_version: draft.draft_version,
        edited_payload: {
          form_patch: reviewedFormPatch,
          notes_seed: notes.trim() || draft.draft_payload.notes_seed || '',
          derived_insights: draft.draft_payload.derived_insights || {},
        },
      });
      if (!response.draft) {
        throw new Error('Draft approval returned no draft.');
      }
      setDraft(response.draft);
      setError('');
      return {
        autoScoutMeta: {
          draft_id: response.draft.id,
          mapper_version: response.draft.mapper_version,
          analysis_version: response.draft.analysis_version,
          approved_at_ms: response.draft.approved_at ? Date.parse(response.draft.approved_at) : null,
        },
        fieldOverrides: response.field_overrides ?? null,
      };
    } catch (nextError) {
      setError(normalizeError(nextError, 'Unable to approve auto-scout draft.'));
      throw nextError;
    } finally {
      setApproving(false);
    }
  }, [draft, form, managedFormFields, notes, scoutProfile]);

  const rejectDraft = useCallback(async (reason: string) => {
    if (!draft || (draft.status !== 'ready' && draft.status !== 'low_confidence')) return;
    setLoading(true);
    try {
      const response = await rejectAutoScoutDraft(draft.id, {
        reason,
        draft_version: draft.draft_version,
      });
      setDraft(response.draft);
      setError('');
    } catch (nextError) {
      setError(normalizeError(nextError, 'Unable to reject auto-scout draft.'));
    } finally {
      setLoading(false);
    }
  }, [draft]);

  return {
    draft,
    loading,
    approving,
    error,
    generateDraft,
    prepareReviewedAutoSave,
    rejectDraft,
    getFieldBadge,
    evidenceField,
    openEvidence,
    closeEvidence,
    heatmapData,
    heatmapLoading,
    heatmapError,
    derivedInsights: draft?.draft_payload?.derived_insights || {},
  };
}
