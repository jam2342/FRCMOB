import { isNativeApp } from '../../platform/runtime';
// Getting a phone ready for an event with no signal: the recorder's detector (model +
// runtime) and the event's data, fetched while there is Wi-Fi so the service worker and
// the API cache have them later. Everything here is best effort -- a failed step leaves
// the rest usable.

import ortWasmUrl from 'onnxruntime-web/ort-wasm-simd-threaded.jsep.wasm?url';
import opencvUrl from '@techstark/opencv-js/dist/opencv.js?url';

import {
  beginOfflineCacheAudit,
  hasSavedApiResponse,
  getEventAwards,
  getEventPredictions,
  getEventRankings,
  getEventRatingsLive,
  getEventTeamLiveForm,
  getEventTeamsHistory,
  getTeamIntel,
  searchEvents,
  getEventRatings,
  getEventSchedule,
  getEventScheduleWithSynergy,
  getEventTeamsIntel,
  listPicklists,
  listPitEntries,
} from '../../api';
import { CURRENT_SEASON_YEAR, FALLBACK_SEASON_YEAR } from '../../pages/centerUtils';
import { getWorkspaceSession } from '../workspace/workspaceSession';
import { appShellReadyOffline, prepareAppShellOffline } from '../../routePrefetch';

export type Progress = { done: number; total: number; label: string };

export type SavedEventStep = { label: string; required: boolean; saved: number; total: number; savedAt: number | null; keys: string[] };
export type SavedEventPack = { eventKey: string; workspaceId: number; checkedAt: number; expectedSteps: number; steps: SavedEventStep[] };
const packKey = (key: string) => `frcmob_offline_pack_v2:${key.trim().toLowerCase()}`;

export function readSavedEventPack(eventKey: string): SavedEventPack | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(packKey(eventKey)) || 'null') as SavedEventPack | null;
    return value?.eventKey === eventKey.trim().toLowerCase()
      && value.workspaceId === (getWorkspaceSession()?.workspace.id ?? 0)
      && Array.isArray(value.steps) ? value : null;
  } catch { return null; }
}

export function isEventPackReady(pack: SavedEventPack | null): boolean {
  return Boolean(pack && pack.expectedSteps > 0 && pack.steps.length === pack.expectedSteps && pack.steps.every((step) => !step.required || (step.total > 0 && step.saved === step.total && step.savedAt !== null)));
}

export async function verifySavedEventPack(pack: SavedEventPack | null): Promise<SavedEventPack | null> {
  if (!pack) return null;
  const steps = await Promise.all(pack.steps.map(async (step) => {
    const found = await Promise.all((step.keys || []).map(hasSavedApiResponse));
    const saved = found.filter(Boolean).length;
    return { ...step, saved, savedAt: saved === step.total ? step.savedAt : null };
  }));
  return { ...pack, steps };
}

export type RecorderAsset = { url: string; label: string };

// The detector files this device needs: its model (per engine, see modelArtifact.ts) and
// the onnxruntime-web runtime that executes it (~27 MB).
export async function recorderAssets(): Promise<RecorderAsset[]> {
  const { pickDeviceModel } = await import('../onDevice/detector');
  const model = await pickDeviceModel();
  return [
    { url: model.url, label: 'detector model' },
    { url: ortWasmUrl, label: 'detector engine' },
    { url: opencvUrl, label: 'field tracker' },
  ];
}

export async function isSavedOnDevice(url: string): Promise<boolean> {
  if (isNativeApp()) {
    // Native builds validate and install the model/runtime files; check the local asset handler.
    try {
      const asset = new URL(url, window.location.href);
      if (asset.protocol !== window.location.protocol || asset.host !== window.location.host) return false;
      return (await fetch(asset.href, { method: 'HEAD' })).ok;
    } catch { return false; }
  }
  if (typeof caches === 'undefined') return false;
  try {
    return Boolean(await caches.match(new URL(url, window.location.href).href));
  } catch {
    return false;
  }
}

export async function recorderReadyOffline(): Promise<boolean> {
  const assets = await recorderAssets();
  const saved = await Promise.all(assets.map((a) => isSavedOnDevice(a.url)));
  return saved.every(Boolean) && await appShellReadyOffline();
}

// Fetch through the service worker (which keeps a copy), reporting bytes as they arrive.
async function download(url: string, onBytes: (received: number, total: number) => void): Promise<void> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} answered ${res.status}`);
  // Cache Storage writes can continue after fetch() finishes. Wait for the
  // committed copy so the readiness check never races the service worker.
  const copy = res.clone();
  const cacheWrite = caches.open('frcmob-offline-models-v1').then((cache) =>
    cache.put(new Request(new URL(url, window.location.href).href), copy),
  );
  const total = Number(res.headers.get('content-length') || 0);
  const reader = res.body?.getReader();
  const consume = async () => {
    if (!reader) {
      await res.arrayBuffer();
      onBytes(total, total);
      return;
    }
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.byteLength;
      onBytes(received, total);
    }
  };
  try {
    // Attach both rejection handlers immediately: a cache/quota failure can
    // arrive long before the network stream finishes (or is interrupted).
    await Promise.all([cacheWrite, consume()]);
  } catch (error) {
    if (reader) await reader.cancel().catch(() => {});
    throw error;
  }
}

export async function prepareRecorderOffline(onProgress: (p: Progress) => void): Promise<void> {
  onProgress({ done: 0, total: 1, label: 'app pages' });
  await prepareAppShellOffline();
  const assets = await recorderAssets();
  const missing: RecorderAsset[] = [];
  for (const a of assets) if (!(await isSavedOnDevice(a.url))) missing.push(a);
  const totals = new Map<string, { received: number; total: number }>();
  const report = (label: string) => {
    let received = 0;
    let total = 0;
    for (const v of totals.values()) {
      received += v.received;
      total += v.total;
    }
    onProgress({ done: received, total, label });
  };
  for (const a of missing) {
    await download(a.url, (received, total) => {
      totals.set(a.url, { received, total });
      report(a.label);
    });
  }
  if (!(await recorderReadyOffline())) throw new Error('The detector did not finish saving on this device. Try again on Wi-Fi.');
}

// The requests the event-day pages make, with the options they make them with: the API
// cache is keyed by exact URL, so a near miss would be no help offline.
export async function prepareEventOffline(eventKey: string, onProgress: (p: Progress) => void): Promise<number> {
  const key = eventKey.trim().toLowerCase();
  const steps: [string, boolean, () => Promise<unknown>][] = [
    ['schedule', true, () => getEventSchedule(key, false)],
    ['schedule and teams', true, () => getEventSchedule(key, false, { includeTeams: true })],
    ['schedule without live results', false, () => getEventSchedule(key, false, { includeLiveResults: false })],
    ['match predictions', false, () => getEventScheduleWithSynergy(key, { refresh: false, include_pair_breakdown: true })],
    ['team details', true, () => getEventTeamsIntel(key, { include_tba: true, include_statbotics: false, include_season_fallback: true })],
    ['pit teams', false, () => getEventTeamsIntel(key, { include_tba: true, include_statbotics: false, include_season_fallback: false })],
    ['team list', true, () => getEventTeamsIntel(key, { include_tba: false, include_statbotics: false, include_season_fallback: true })],
    ['ratings', false, () => getEventRatings(key)],
    ['live ratings', false, () => getEventRatingsLive(key)],
    ['rankings', false, () => getEventRankings(key)],
    ['team form', false, () => getEventTeamLiveForm(key, { form_window: 5, live_window_sec: 180 })],
    ['team history', false, () => getEventTeamsHistory(key, 6, false)],
    ['awards', false, () => getEventAwards(key)],
    ['predictions', false, () => getEventPredictions(key)],
    ['prediction schedule', false, () => getEventScheduleWithSynergy(key, { include_pair_breakdown: false })],
    ['strategy form', false, () => getEventTeamLiveForm(key)],
    ['event list', false, () => searchEvents(key.slice(0, 4))],
  ];
  if (getWorkspaceSession()) {
    steps.push(['picklists', true, () => listPicklists(key)], ['pit scouting', true, () => listPitEntries(key)]);
  }
  steps.push([
    'team pages', true,
    async () => {
      const intel = (await getEventTeamsIntel(key, { include_tba: true, include_statbotics: false, include_season_fallback: true })) as unknown as { teams?: { team_key?: string }[] };
      const teamKeys = (intel.teams ?? []).map((t) => String(t.team_key || '').toLowerCase()).filter(Boolean);
      if (!teamKeys.length) throw new Error('No teams were returned for the event');
      for (let i = 0; i < teamKeys.length; i += 4) {
        await Promise.allSettled(teamKeys.slice(i, i + 4).map((teamKey) =>
          getTeamIntel(teamKey, {
            event_key: key,
            preferred_year: CURRENT_SEASON_YEAR,
            fallback_year: FALLBACK_SEASON_YEAR,
            include_tba: true,
            include_statbotics: false,
            allow_season_fallback: true,
            auto_heal_ratings: true,
          }, { timeoutMs: 25000 }),
        ));
        onProgress({ done: steps.length - 1, total: steps.length, label: `team pages ${Math.min(i + 4, teamKeys.length)}/${teamKeys.length}` });
      }
    },
  ]);

  const audit = beginOfflineCacheAudit((raw) => {
    const url = new URL(raw, window.location.href);
    return url.pathname.includes('/'+key+'/') ||
      (url.pathname.includes('/teams/frc') && url.searchParams.get('event_key') === key) ||
      (url.pathname.endsWith('/events/search') && url.searchParams.get('q') === key.slice(0,4));
  });
  const workspaceId = getWorkspaceSession()?.workspace.id ?? 0;
  const pack: SavedEventPack = { eventKey: key, workspaceId, checkedAt: Date.now(), expectedSteps: steps.length, steps: [] };
  let saved = 0;
  try {
    for (let i = 0; i < steps.length; i++) {
      if ((getWorkspaceSession()?.workspace.id ?? 0) !== workspaceId) throw new Error('Team changed during the download. Start it again for your current team.');
      const [label, required, load] = steps[i];
      onProgress({ done: i, total: steps.length, label });
      const start = audit.calls.length;
      let loaded = false;
      try { await load(); loaded = true; } catch { /* record the failed request below */ }
      if ((getWorkspaceSession()?.workspace.id ?? 0) !== workspaceId) throw new Error('Team changed during the download. Start it again for your current team.');
      const calls = audit.calls.slice(start);
      const savedCount = calls.filter((call) => call.saved).length;
      const complete = loaded && calls.length > 0 && savedCount === calls.length;
      pack.steps.push({ label, required, saved: savedCount, total: calls.length || 1, savedAt: complete ? Date.now() : null, keys: calls.map((call) => call.key) });
      if (complete) saved++;
      pack.checkedAt = Date.now();
      try { window.localStorage.setItem(packKey(key), JSON.stringify(pack)); } catch { /* storage unavailable: UI stays unverified */ }
    }
  } finally { audit.stop(); }
  onProgress({ done: steps.length, total: steps.length, label: 'done' });
  return saved;
}

export function eventSavedAt(eventKey: string): number | null {
  const pack = readSavedEventPack(eventKey);
  if (!isEventPackReady(pack)) return null;
  return Math.min(...pack!.steps.filter((step) => step.required).map((step) => step.savedAt || 0));
}
