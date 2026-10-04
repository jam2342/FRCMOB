// Run against an opt-in simulator build. Never includes real credentials or server writes.
import { execFileSync } from 'node:child_process';
import { readFileSync, rmSync, existsSync, readdirSync } from 'node:fs';
import assert from 'node:assert/strict';
const booted = JSON.parse(execFileSync('xcrun', ['simctl', 'list', 'devices', 'booted', '-j'], {encoding: 'utf8'}));
const device = process.env.IOS_SIMULATOR_ID || Object.values(booted.devices).flat().find(device => device.name.startsWith('iPhone'))?.udid;
assert.ok(device, 'Boot an iPhone simulator or set IOS_SIMULATOR_ID.');
const appId = 'app.frcmob.scouting';
const run = (...args) => execFileSync('xcrun', ['simctl', ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
const container = run('get_app_container', device, appId, 'data');
const output = `${container}/Documents/native-smoke.json`;
const stage = process.argv[2];
assert.ok(['seed', 'verify', 'clean', 'recorder', 'share', 'inference', 'migration-seed', 'migration-verify', 'signout', 'signedout-verify', 'video', 'reinstall', 'csv', 'html', 'recording-flow', 'recording-restart', 'live-read', 'event-prepare', 'event-offline', 'local-server'].includes(stage));
const common = `
const check = (ok, message) => { if (!ok) throw new Error(message); };
const native = (method, args) => Capacitor.nativePromise('SecureStorage', method, args);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async (condition, message) => { for (let i=0;i<100;i++) { if (condition()) return; await pause(100); } check(false, message); };
const openDB = () => new Promise((resolve, reject) => {
  const req = indexedDB.open('frcmob-ondevice', 1);
  req.onupgradeneeded = () => { for (const name of ['sessions', 'calibrations']) if (!req.result.objectStoreNames.contains(name)) req.result.createObjectStore(name, {keyPath: 'id'}); };
  req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);
});
const transact = (db, mode, fn) => new Promise((resolve, reject) => {
  const tx = db.transaction('sessions', mode); const req = fn(tx.objectStore('sessions'));
  tx.oncomplete = () => resolve(req.result); tx.onerror = () => reject(tx.error);
});
`;
const detectorAsset = readdirSync('dist-native/assets').find(name => /^detector-.*\.js$/.test(name));
const csvAsset = readdirSync('dist-native/assets').find(name => /^csvExport-.*\.js$/.test(name));
const reportAsset = readdirSync('dist-native/assets').find(name => /^exportFile-.*\.js$/.test(name));
const offlineAsset = readdirSync('dist-native/assets').find(name => /^offlineReady-.*\.js$/.test(name));
const scripts = {
  'local-server': `
check(!window.__simulatorOffline, 'Local server bridge must be enabled');
const base = 'http://127.0.0.1:4182';
const request = (path, method='GET', data, token) => Capacitor.nativePromise('CapacitorHttp','request',{url:base+path,method,headers:{'Content-Type':'application/json',...(token?{'X-Workspace-Access':token}:{})},...(data?{data}:{}),connectTimeout:10000,readTimeout:10000});
const health = await request('/health');check(health.status===200 && health.data.disposable_beta, 'Wrong integration server');
const before = (await request('/test-evidence')).data;
const leader = await request('/workspaces','POST',{name:'Native disposable beta',display_name:'Native Leader'});check(leader.status===200,'Create failed');
const joined = await request('/workspaces/join','POST',{join_code:leader.data.join_code,display_name:'Native Scout'});check(joined.status===200,'Join failed');
const token = joined.data.access.token;const workspaceId=joined.data.workspace.id;
check((await request('/workspaces/me','GET',undefined,token)).status===200,'Member access failed');
const recording={id:'native-beta-'+Date.now(),eventKey:'2026test',matchKey:'2026test_qm1',workspaceId,payload:{schemaVersion:'on_device_session_v2',pointsByTeam:{frc254:[{timeSec:1,fieldX:1,fieldY:1},{timeSec:2,fieldX:2,fieldY:1}]},sampledFrameCount:2,shift1ActiveAlliance:'red',captureSource:'video',poseSource:'static',identitySource:'manual',timingSource:'video_offset'}};
check((await request('/tracks/on-device-session','POST',recording,token)).status===200,'Native recording sync failed');
check((await request('/tracks/on-device-session','POST',recording,token)).status===200,'Native replay failed');
check((await request('/tracks/on-device-session','POST',{...recording,workspaceId:workspaceId+1},token)).status===409,'Wrong-workspace upload accepted');
const after=(await request('/test-evidence')).data;check(after.sessions===before.sessions+1 && after.tracks===before.tracks+2,'Replay duplicated persisted data');
check((await request('/workspaces/me/members/'+joined.data.me.id+'/remove','POST',{rotate_join_code:true},leader.data.access.token)).status===200,'Removal failed');
check((await request('/workspaces/me','GET',undefined,token)).status===401,'Removed native member retained access');
return {passed:true,realNativeHTTP:true,realBackend:true,join:true,sync:true,idempotentRetry:true,workspaceMismatchRejected:true,revocation:true,productionWrites:0};`,
  'live-read': `
check(!window.__simulatorOffline, 'live network blocked');
const statuses = {};
for (const path of ['/health', '/matches/event/2026arc/schedule?include_teams=true', '/teams/event/2026arc/intel?include_tba=false&include_statbotics=false']) {
  const response = await fetch('https://scouting-app-iryg.vercel.app/api'+path);
  check(response.ok, 'Native HTTP failed: '+path+' '+response.status);
  const data = await response.json(); check(data && typeof data === 'object', 'Invalid JSON');
  statuses[path] = response.status;
}
return {passed: true, nativeLiveHTTP: true, statuses, serverWrites: 0};`,
  'event-prepare': `
check(!window.__simulatorOffline, 'live network blocked');
check(!(await native('get', {key: 'frcmob_workspace_session_v1'})).value, 'Use a signed-out disposable installation');
const original = window.fetch.bind(window); let reads = 0;
window.fetch = (input, options) => {
  const url = new URL(typeof input === 'string' ? input : input.url, location.href);
  const method = options?.method || (typeof input === 'object' && input.method) || 'GET';
  check(['GET','HEAD'].includes(method.toUpperCase()), 'Writes forbidden in preparation probe');
  if (url.protocol === 'https:') {check(url.host === 'scouting-app-iryg.vercel.app', 'Unexpected server'); reads++;}
  return original(input, options);
};
const module = await import('/assets/${offlineAsset}');
const prepare = Object.values(module).find(fn => typeof fn === 'function' && String(fn).includes('Team changed during the download') && String(fn).includes('steps:'));
check(prepare, 'Preparation export missing');
const saved = await prepare('2026arc', () => {});
const pack = JSON.parse(localStorage.getItem('frcmob_offline_pack_v2:2026arc'));
check(pack && pack.workspaceId === 0 && pack.steps.length === pack.expectedSteps, 'Incomplete preparation: '+JSON.stringify({saved,expected:pack?.expectedSteps,stored:pack?.steps.length}));
const failed = pack.steps.filter(step => step.required && (!step.savedAt || step.saved !== step.total));
check(!failed.length, 'Required steps failed: '+failed.map(step => step.label).join(', '));
return {passed: true, eventKey: pack.eventKey, savedSteps: saved, expectedSteps: pack.expectedSteps, teamPages: pack.steps.find(step=>step.label==='team pages').total, reads, serverWrites: 0};`,
  'event-offline': `
check(window.__simulatorOffline, 'network fixture disabled');
const module = await import('/assets/${offlineAsset}');
const pack = JSON.parse(localStorage.getItem('frcmob_offline_pack_v2:2026arc'));
check(pack, 'Event pack lost on restart');
const verify = Object.values(module).find(fn => typeof fn === 'function' && String(fn).includes('Promise.all') && String(fn).includes('.keys') && String(fn).includes('savedAt'));
const ready = Object.values(module).find(fn => typeof fn === 'function' && String(fn).includes('expectedSteps') && String(fn).includes('.every'));
check(verify && ready && ready(await verify(pack)), 'Saved responses missing');
location.hash = '/events?event=2026arc&tab=schedule'; await pause(3000);
check(document.body.innerText.includes('Archimedes'), 'Cached event did not render');
check(document.querySelectorAll('.event-schedule-mobile-main').length > 0, 'Cached schedule rows did not render');
check(document.documentElement.scrollWidth <= innerWidth + 2, 'Offline event overflow');
return {passed: true, eventSurvivedRestart: true, requiredCacheVerified: true, offlineEventRendered: true};`,
  'recording-flow': `
check(window.__simulatorOffline, 'network fixture disabled');
window.__simulatorMockSchedule = {ok: true, event_key: '2026test', event_name: 'Simulator Fixture', matches: [{match_key: '2026test_qm1', display_name: 'Qual 1', comp_level: 'qm', set_number: 1, match_number: 1, is_completed: false, red: [254,1678,1690].map(n => ({team_key: 'frc'+n, team_number: n})), blue: [2046,2910,868].map(n => ({team_key: 'frc'+n, team_number: n}))}]};
location.hash = '/scouting/record?event=2026test&match=2026test_qm1';
const button = text => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === text);
await until(() => button('Load match teams'), 'setup screen'); button('Load match teams').click();
await until(() => button('Continue to calibration'), 'match teams did not load'); button('Continue to calibration').click();
await until(() => document.querySelector('input[type=file][accept="image/*"]'), 'calibration input');
const content = await Capacitor.nativePromise('Filesystem', 'readFile', {directory: 'DOCUMENTS', path: 'smoke-video.mp4'});
const bytes = Uint8Array.from(atob(content.data), c => c.charCodeAt(0));
const clip = new File([bytes], 'fixture.mp4', {type: 'video/mp4'});
const url = URL.createObjectURL(clip); const video = document.createElement('video'); video.muted = true; video.playsInline = true; video.src = url;
await new Promise((resolve, reject) => {video.onloadeddata = resolve; video.onerror = () => reject(new Error('Fixture decode')); video.load();});
const photo = document.createElement('canvas'); photo.width = video.videoWidth; photo.height = video.videoHeight; photo.getContext('2d').drawImage(video, 0, 0);
const blob = await new Promise(resolve => photo.toBlob(resolve, 'image/png'));
const upload = (input, file) => {const transfer = new DataTransfer(); transfer.items.add(file); input.files = transfer.files; input.dispatchEvent(new Event('change', {bubbles: true}));};
upload(document.querySelector('input[type=file][accept="image/*"]'), new File([blob], 'fixture.png', {type: 'image/png'}));
await until(() => document.querySelector('.field-calibration canvas'), 'calibration image');
const canvas = document.querySelector('.field-calibration canvas');
// Deliberately synthetic corners: this checks the user flow, not field-position accuracy.
for (const [x,y] of [[0.02,0.02],[0.98,0.02],[0.98,0.98],[0.02,0.98]]) {const rect = canvas.getBoundingClientRect(); canvas.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: rect.left+x*rect.width, clientY: rect.top+y*rect.height})); await pause(200);}
await until(() => button('Use this calibration') && !button('Use this calibration').disabled, 'four-tap calibration'); button('Use this calibration').click();
await until(() => button('Upload video'), 'capture screen'); button('Upload video').click();
await until(() => document.querySelector('input[type=file][accept="video/*"]'), 'video input'); await pause(1000); upload(document.querySelector('input[type=file][accept="video/*"]'), clip);
for (let i=0;i<600 && !document.querySelector('select[aria-label^="Assign track"]');i++) {const err = document.querySelector('.video-file-processor .field-calibration__error'); if (err) throw new Error(err.textContent); await pause(100);}
const assignments = [...document.querySelectorAll('select[aria-label^="Assign track"]')]; check(assignments.length > 0, 'no identity paths: '+document.body.innerText.slice(-900));
for (const select of assignments) {Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, select.options[1].value); select.dispatchEvent(new Event('change',{bubbles:true})); await pause(100);}
const finish = [...document.querySelectorAll('button')].find(b => b.textContent.startsWith('Finish & sync'));
check(finish && !finish.disabled, 'finish disabled'); finish.click();
await until(() => document.body.innerText.includes('Saved on this device.'), 'local-first save');
const db = await openDB(); const sessions = await transact(db, 'readonly', store => store.getAll()); db.close();
const saved = sessions.filter(row => row.matchKey === '2026test_qm1' && row.id !== 'simulator-smoke-recording').at(-1);
check(saved && !saved.synced && saved.payload.schemaVersion === 'on_device_session_v2', 'stored recording');
check(saved.payload.sampledFrameCount >= 3 && Object.values(saved.payload.pointsByTeam).some(points => points.length >= 3), 'stored track points');
check(!JSON.stringify(saved).includes('token'), 'credential in recording');
localStorage.setItem('simulator-flow-session-id', saved.id);
const calibrationDB = await openDB(); const calibrationRequest = calibrationDB.transaction('calibrations').objectStore('calibrations').get('current');
const calibration = await new Promise((resolve, reject) => {calibrationRequest.onsuccess = () => resolve(calibrationRequest.result); calibrationRequest.onerror = () => reject(calibrationRequest.error);}); calibrationDB.close();
localStorage.setItem('simulator-flow-calibration-time', String(calibration.createdAt)); URL.revokeObjectURL(url);
return {passed: true, matchSetup: true, fourTapCalibration: true, fileImport: true, pathsAssigned: assignments.length, sampledFrames: saved.payload.sampledFrameCount, localFirstSave: true, noCredentialsInRecording: true, identityAssignmentsSynthetic: true, serverWritesBlocked: true};`,
  'recording-restart': `
check(window.__simulatorOffline, 'network fixture disabled');
const id = localStorage.getItem('simulator-flow-session-id'); check(id, 'flow fixture missing');
const db = await openDB(); const saved = await transact(db, 'readonly', store => store.get(id)); db.close();
check(saved && !saved.synced && saved.payload.sampledFrameCount >= 3, 'imported recording lost on restart');
location.hash = '/my-team';
await until(() => document.body.innerText.includes('Export recording'), 'restart recovery missing');
return {passed: true, importedRecordingPersists: true, recoveryVisible: true, serverWritesBlocked: true};`,
  reinstall: `
check((await native('get', {key: 'simulator-smoke-only'})).value === 'fake-local-fixture', 'Keychain reinstall persistence');
check(!localStorage.getItem('simulator-smoke-ciphertext'), 'WebView data survived uninstall');
const db = await openDB(); const row = await transact(db, 'readonly', store => store.get('simulator-smoke-recording')); db.close();
check(!row, 'recording survived uninstall');
return {passed: true, keychainSurvivesUninstall: true, webViewDataRemovedByUninstall: true, recordingsRemovedByUninstall: true};`,
  html: `
const module = await import('/assets/${reportAsset}'); const exportReport = Object.values(module).find(fn => typeof fn === 'function' && fn.length === 1);
check(exportReport, 'report exporter missing');
const fixture = document.createElement('div'); fixture.innerHTML = '<h2>Simulator Report</h2><table><tbody><tr><td>254 &amp; 1678</td></tr></tbody></table><input value="should-not-export"><button>Should not export</button>';
(document.querySelector('main') || document.getElementById('root')).append(fixture);
void exportReport('simulator-report.html').catch(() => {}); await pause(2000); fixture.remove();
return {passed: true, nativeHTMLExportStarted: true};`,
  csv: `
const module = await import('/assets/${csvAsset}'); const download = Object.values(module).find(fn => typeof fn === 'function');
check(download, 'CSV exporter missing');
void download('simulator-smoke.csv', ['Team', 'Notes'], [[254, 'comma, quote " and line']]).catch(() => {}); await pause(2000);
return {passed: true, nativeCSVExportStarted: true};`,
  'migration-seed': `
check(window.__simulatorOffline, 'network fixture disabled');
await native('remove', {key: 'frcmob_workspace_session_v1'});
localStorage.setItem('frcmob_workspace_session_v1', JSON.stringify({token: 'fake-ios-migration-token', expiresAt: Date.now()/1000 + 3600, workspace: {id: 999999, name: 'Simulator Fixture', frc_team_number: null}, me: {id: 999999, display_name: 'Fixture Scout', role: 'member'}}));
localStorage.setItem('frcmob_offline_queue_v1', JSON.stringify([{id: 'simulator-smoke-edit', queuedAt: new Date().toISOString(), url: '/simulator-fixture-never-send', method: 'PUT', body: '{"notes":"fixture edit"}', headers: {'X-Workspace-Access': 'fake-ios-migration-token'}, attempts: 0, label: 'Fixture edit', failure: {reason: 'conflict', status: 409}}]));
return {passed: true, migrationFixtureSeeded: true, serverRequestsBlocked: true};`,
  'migration-verify': `
check(window.__simulatorOffline, 'network fixture disabled');
const session = JSON.parse((await native('get', {key: 'frcmob_workspace_session_v1'})).value);
check(session.workspace.id === 999999 && session.token === 'fake-ios-migration-token', 'workspace Keychain migration');
check(!localStorage.getItem('frcmob_workspace_session_v1'), 'plaintext session retained');
check(!localStorage.getItem('frcmob_offline_queue_v1'), 'plaintext queue retained');
const db = await new Promise((resolve, reject) => {const req = indexedDB.open('frcmob_offline_queue', 1); req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);});
const stored = await new Promise((resolve, reject) => {const req = db.transaction('mutations').objectStore('mutations').get('simulator-smoke-edit'); req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);}); db.close();
check(stored?.secureVersion === 1 && stored.ciphertext && !JSON.stringify(stored).includes('fake-ios-migration-token'), 'queue encryption');
const edit = JSON.parse((await native('decrypt', {value: stored.ciphertext})).value);
check(edit.body === '{"notes":"fixture edit"}' && edit.failure.status === 409, 'edit preserved');
location.hash = '/my-team'; await pause(1200);
check(document.body.innerText.includes('Simulator Fixture'), 'workspace restored UI');
return {passed: true, workspaceMigratedToKeychain: true, queuedEditEncrypted: true, originalsPreserved: true, plaintextRemoved: true, serverRequestsBlocked: true};`,
  signout: `
check(window.__simulatorOffline, 'network fixture disabled');
location.hash = '/my-team'; await pause(1000);
window.__simulatorAllowMockLeave = true;
const leave = [...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Leave Simulator Fixture');
check(leave, 'leave workspace button'); leave.click(); await pause(300);
const confirm = [...document.querySelectorAll('[role="dialog"] button')].find(button => button.textContent.trim() === 'Leave');
check(confirm, 'leave confirmation'); confirm.click(); await pause(1500);
check((await native('get', {key: 'frcmob_workspace_session_v1'})).value === null, 'Keychain signout');
check(document.body.innerText.includes('Join your team'), 'signout UI');
return {passed: true, keychainCleared: true, signedOut: true, serverLeaveMocked: true};`,
  'signedout-verify': `
check((await native('get', {key: 'frcmob_workspace_session_v1'})).value === null, 'workspace restored after signout');
location.hash = '/my-team'; await pause(1000);
check(document.body.innerText.includes('Join your team'), 'signed-out restart UI');
return {passed: true, signedOutAfterRestart: true};`,
  seed: `
await native('set', {key: 'simulator-smoke-only', value: 'fake-local-fixture'});
check((await native('get', {key: 'simulator-smoke-only'})).value === 'fake-local-fixture', 'Keychain round-trip');
const encrypted = (await native('encrypt', {value: 'fake-private-edit'})).value;
check(!encrypted.includes('fake-private-edit'), 'plaintext ciphertext');
localStorage.setItem('simulator-smoke-ciphertext', encrypted);
check((await native('decrypt', {value: encrypted})).value === 'fake-private-edit', 'decrypt round-trip');
let rejected = false; try { await native('decrypt', {value: 'AAAA' + encrypted.slice(4)}); } catch { rejected = true; }
check(rejected, 'tampered ciphertext accepted');
const db = await openDB();
await transact(db, 'readwrite', store => store.put({id: 'simulator-smoke-recording', matchKey: '2026test_qm1', eventKey: '2026test', createdAt: 1, synced: false, workspaceId: null, syncFailure: {kind: 'rejected', status: 409, at: 1}, payload: {pointsByTeam: {frc254: [{t: 1, x: 2, y: 3}]}}})); db.close();
localStorage.setItem('frcmob_tour_prompt_dismissed_v1', '1');
location.hash = '/my-team'; window.dispatchEvent(new Event('frcmob:session-change')); await pause(1000);
check(document.body.innerText.includes('Export recording'), 'recording recovery missing');
return {passed: true, keychainRoundTrip: true, authenticatedEncryption: true, tamperRejected: true, recoveryVisible: true};`,
  verify: `
check((await native('get', {key: 'simulator-smoke-only'})).value === 'fake-local-fixture', 'Keychain persistence');
check((await native('decrypt', {value: localStorage.getItem('simulator-smoke-ciphertext')})).value === 'fake-private-edit', 'encryption key persistence');
const db = await openDB(); const session = await transact(db, 'readonly', store => store.get('simulator-smoke-recording')); db.close();
check(session?.payload?.pointsByTeam?.frc254?.length === 1 && !session.synced, 'recording persistence');
location.hash = '/my-team'; await pause(1000);
await until(() => document.body.innerText.includes('Export recording'), 'recovery UI after restart');
check(document.documentElement.scrollWidth <= innerWidth + 2, 'phone overflow');
return {passed: true, keychainPersists: true, encryptionKeyPersists: true, recordingPersists: true, recoveryAfterRestart: true, overflow: false};`,
  share: `
location.hash = '/my-team'; await pause(1500);
const button = [...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Export recording');
check(button, 'export button missing'); button.click(); await pause(2000);
return {passed: true, recoveryExportClicked: true};`,
  clean: `
try { await Capacitor.nativePromise('Filesystem', 'deleteFile', {directory: 'DOCUMENTS', path: 'smoke-video.mp4'}); } catch { /* optional video fixture absent */ }
const queueDB = await new Promise((resolve, reject) => {const req = indexedDB.open('frcmob_offline_queue', 1); req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);});
const queueTx = queueDB.transaction('mutations', 'readwrite'); queueTx.objectStore('mutations').delete('simulator-smoke-edit'); await new Promise((resolve, reject) => {queueTx.oncomplete = resolve; queueTx.onerror = () => reject(queueTx.error);}); queueDB.close();
await native('remove', {key: 'simulator-smoke-only'});
check((await native('get', {key: 'simulator-smoke-only'})).value === null, 'Keychain removal');
localStorage.removeItem('simulator-smoke-ciphertext');
const db = await openDB(); await transact(db, 'readwrite', store => store.delete('simulator-smoke-recording'));
const flowId = localStorage.getItem('simulator-flow-session-id'); if (flowId) await transact(db, 'readwrite', store => store.delete(flowId));
const fixtures = await transact(db, 'readonly', store => store.getAll());
for (const row of fixtures) if (row.matchKey === '2026test_qm1' && row.payload?.sampledFrameCount === 9 && row.payload?.schemaVersion === 'on_device_session_v2') await transact(db, 'readwrite', store => store.delete(row.id));
const calibrationTx = db.transaction('calibrations', 'readwrite'); const calibrationStore = calibrationTx.objectStore('calibrations'); const request = calibrationStore.get('current'); request.onsuccess = () => {if (String(request.result?.createdAt) === localStorage.getItem('simulator-flow-calibration-time')) calibrationStore.delete('current');};
await new Promise((resolve, reject) => {calibrationTx.oncomplete = resolve; calibrationTx.onerror = () => reject(calibrationTx.error);}); db.close();
localStorage.removeItem('simulator-flow-session-id'); localStorage.removeItem('simulator-flow-calibration-time');
if (localStorage.getItem('scouting_center_event_key') === '2026test') localStorage.removeItem('scouting_center_event_key');
if (localStorage.getItem('scouting_center_match_key') === '2026test_qm1') localStorage.removeItem('scouting_center_match_key');
window.dispatchEvent(new Event('frcmob:session-change'));
location.hash = '/';
return {passed: true, fixturesRemoved: true};`,
  video: `
check(window.__simulatorOffline, 'network fixture disabled');
const file = await Capacitor.nativePromise('Filesystem', 'readFile', {directory: 'DOCUMENTS', path: 'smoke-video.mp4'});
const bytes = Uint8Array.from(atob(file.data), char => char.charCodeAt(0));
const url = URL.createObjectURL(new Blob([bytes], {type: 'video/mp4'}));
const video = document.createElement('video'); video.muted = true; video.playsInline = true; video.preload = 'auto'; video.src = url;
await new Promise((resolve, reject) => {video.onloadeddata = resolve; video.onerror = () => reject(new Error('Native video decode failed')); setTimeout(() => reject(new Error('Native video decode timeout')), 15000);});
check(video.videoWidth === 1280 && video.duration > 2, 'video metadata');
video.currentTime = 1;
await new Promise((resolve, reject) => {video.onseeked = resolve; setTimeout(() => reject(new Error('Video seek timeout')), 10000);});
const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
const context = canvas.getContext('2d'); context.drawImage(video, 0, 0);
const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
check(pixels.some((value, index) => index % 4 !== 3 && value > 20), 'decoded frame is blank');
const module = await import('/assets/${detectorAsset}');
const create = Object.values(module).find(fn => typeof fn === 'function' && fn.length === 0 && String(fn).includes('webgpu') && String(fn).includes('wasm'));
const detect = Object.values(module).find(fn => typeof fn === 'function' && String(fn).includes('.session.run'));
const detector = await create(); const start = performance.now();
const boxes = await detect(detector, video, video.videoWidth, video.videoHeight);
const ms = performance.now() - start; await detector.session.release(); URL.revokeObjectURL(url);
check(Array.isArray(boxes), 'video inference output');
return {passed: true, videoDecoded: true, videoSeeked: true, nonBlankFrame: true, detections: boxes.length, frameMs: ms, provider: detector.executionProvider, serverRequestsBlocked: true};`,
  inference: `
const module = await import('/assets/${detectorAsset}');
const create = Object.values(module).find(fn => typeof fn === 'function' && fn.length === 0 && String(fn).includes('webgpu') && String(fn).includes('wasm'));
const detect = Object.values(module).find(fn => typeof fn === 'function' && String(fn).includes('.session.run'));
check(create && detect, 'detector exports not found');
const start = performance.now(); const detector = await create();
const canvas = document.createElement('canvas'); canvas.width = 1280; canvas.height = 720;
const context = canvas.getContext('2d'); context.fillStyle = '#000'; context.fillRect(0, 0, 1280, 720);
const frameStart = performance.now(); const boxes = await detect(detector, canvas, 1280, 720, {roi: {x: 0, y: 0, w: 1280, h: 352}});
const frameMs = performance.now() - frameStart;
await detector.session.release();
check(Array.isArray(boxes), 'invalid detector output');
return {passed: true, provider: detector.executionProvider, model: detector.modelVersion, detections: boxes.length, loadAndRunMs: performance.now() - start, frameMs};`,
  recorder: `
location.hash = '/scouting/record'; await pause(2000);
await until(() => document.body.innerText.includes('On-Device Match Breakdown'), 'recorder screen missing');
check(location.protocol === 'capacitor:' && !location.pathname.endsWith('record.html'), 'recorder document redirect');
check(document.documentElement.scrollWidth <= innerWidth + 2, 'recorder overflow');
return {passed: true, recorderInApp: true, overflow: false, webGPU: Boolean(navigator.gpu), isolated: crossOriginIsolated};`,
};
try { run('terminate', device, appId); } catch { /* app already stopped */ }
rmSync(output, { force: true });
const env = { ...process.env, SIMCTL_CHILD_FRCMOB_SIMULATOR_OFFLINE: process.env.IOS_PROBE_OFFLINE || '0', SIMCTL_CHILD_FRCMOB_SIMULATOR_PROBE: Buffer.from(common + scripts[stage]).toString('base64') };
execFileSync('xcrun', ['simctl', 'launch', device, appId], { env, stdio: 'inherit' });
for (let i = 0; i < (stage === 'event-prepare' ? 1200 : 180) && !existsSync(output); i++) await new Promise(resolve => setTimeout(resolve, 500));
assert.ok(existsSync(output), 'Simulator probe timed out');
const result = JSON.parse(readFileSync(output, 'utf8'));
assert.ok(!result.error, result.error);
assert.equal(result.result?.passed, true);
console.log(JSON.stringify({stage, ...result.result}));
