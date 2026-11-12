// Two people pit scouting the same event against a REAL backend (several workers,
// Postgres, Redis), not mocks. It creates its own throwaway workspace ("(delete me)").
//
//   MULTI_APP=http://127.0.0.1:5213 MULTI_API=http://127.0.0.1:8100 node e2e/multiuser/pit-two-phones.mjs
//
// MULTI_APP serves the app with its /api going to MULTI_API. Never point this at
// production: it writes. A backend that sends X-Worker-Pid lets it report which
// worker answered each phone.
import { launch, phone, sleep } from '../regression/lib.mjs';

const APP = process.env.MULTI_APP;
const API = process.env.MULTI_API;
const EVENT = process.env.MULTI_EVENT || '2026azrl';
if (!APP || !API) throw new Error('Set MULTI_APP and MULTI_API (see the top of this file).');
if (/frcmob\.com|vercel\.app/.test(APP + API)) throw new Error('Refusing to write to production.');

const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
const results = [];
const workers = new Map();

async function api(path, { token, body, method = body ? 'POST' : 'GET' } = {}) {
  const response = await fetch(API + path, {
    method,
    headers: { 'content-type': 'application/json', ...(token ? { 'X-Workspace-Access': token } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  return { status: response.status, worker: response.headers.get('x-worker-pid'), json: text ? JSON.parse(text) : null };
}

function session(joined) {
  return {
    token: joined.access.token,
    expiresAt: joined.access.expires_at_unix,
    workspace: { id: joined.workspace.id, name: joined.workspace.name, frc_team_number: null },
    me: { id: joined.me.id, display_name: joined.me.display_name, role: joined.me.role },
  };
}

async function openPhone(browser, who, joined, size) {
  const ctx = await phone(browser, { ...size, storage: { frcmob_workspace_session_v1: JSON.stringify(session(joined)) } });
  const page = await ctx.newPage();
  page.on('dialog', (dialog) => dialog.accept().catch(() => {}));
  page.on('response', (response) => {
    const pid = response.headers()['x-worker-pid'];
    if (pid) workers.set(`${who} ${pid}`, (workers.get(`${who} ${pid}`) ?? 0) + 1);
    // Every pit-list poll that isn't a 200 slows the next one down (backoff), so name it.
    if (/\/pit-scouting\?/.test(response.url()) && response.status() !== 200) console.log(`      ${who}: pit list poll answered ${response.status()}`);
  });
  page.on('requestfailed', (request) => {
    if (/\/pit-scouting\?/.test(request.url())) console.log(`      ${who}: pit list poll failed: ${request.failure()?.errorText}`);
  });
  return { ctx, page, who };
}

async function waitFor(what, fn, timeout = 45_000) {
  const start = Date.now();
  let last;
  while (Date.now() - start < timeout) {
    try { last = await fn(); if (last) return { value: last, ms: Date.now() - start }; } catch (err) { last = err.message; }
    await sleep(500);
  }
  throw new Error(`Timed out after ${timeout / 1000}s waiting for ${what} (last: ${String(last).slice(0, 160)})`);
}

async function check(name, fn) {
  const started = Date.now();
  try {
    const detail = await fn();
    results.push({ name, ok: true, detail, s: ((Date.now() - started) / 1000).toFixed(1) });
    console.log(`pass  ${name}${detail ? ` (${detail})` : ''}`);
  } catch (err) {
    results.push({ name, ok: false, detail: err.message });
    console.log(`FAIL  ${name}\n      ${err.message}`);
  }
}

const statusOf = (page) => page.getByRole('group', { name: 'Save pit entry' }).getByRole('status').innerText().catch(() => '');
const leftCount = async (page) => Number(/Left, (\d+)/.exec((await page.getByRole('tab', { name: /^Left/ }).getAttribute('aria-label').catch(() => null)) ?? (await page.getByRole('tab', { name: /^Left/ }).innerText().catch(() => '')))?.[1] ?? NaN);

async function openTeam(page, number) {
  await page.goto(`${APP}/#/scouting/pit?event=${EVENT}&team=frc${number}`);
  await page.getByRole('heading', { name: `Team ${number}` }).waitFor({ timeout: 30_000 });
}

async function typeNumber(page, value) {
  const box = page.getByRole('spinbutton').first();
  await box.fill(String(value));
  return box;
}

const browser = await launch();
try {
  // Setup: one team workspace, a leader on a laptop and a scout on a phone.
  const suffix = Date.now().toString(36);
  const lead = (await api('/workspaces', { body: { name: `Pit 2W ${suffix} (delete me)`, display_name: 'Lead' } })).json;
  const sam = (await api('/workspaces/join', { body: { join_code: lead.join_code, display_name: 'Sam' } })).json;
  const alex = (await api('/workspaces/join', { body: { join_code: lead.join_code, display_name: 'Alex' } })).json;
  const teams = (await api(`/teams/event/${EVENT}/intel?include_tba=false&include_season_fallback=false`, { token: lead.access.token })).json;
  const numbers = (teams?.teams ?? []).map((t) => Number(t.team_number)).filter(Boolean).sort((a, b) => a - b);
  if (numbers.length < 20) throw new Error(`Event ${EVENT} has ${numbers.length} teams; need 20.`);
  console.log(`workspace ${lead.workspace.id}, event ${EVENT}, ${numbers.length} teams`);

  const A = await openPhone(browser, 'Lead', lead, { width: 1366, height: 900, mobile: false });
  const B = await openPhone(browser, 'Sam', sam, { width: 390, height: 844 });
  for (const { page } of [A, B]) {
    await page.goto(`${APP}/#/scouting/pit?event=${EVENT}`);
    await page.getByRole('tab', { name: /^Left/ }).waitFor({ timeout: 30_000 });
  }

  await check("a team the leader scouts leaves the scout's Left list", async () => {
    await B.page.getByRole('tab', { name: /^Left/ }).click();
    const before = await leftCount(B.page);
    await openTeam(A.page, numbers[0]);
    await typeNumber(A.page, 111);
    await A.page.getByRole('button', { name: 'Save', exact: true }).click();
    await waitFor('the save', async () => /Saved pit entry/.test(await statusOf(A.page)));
    const { ms } = await waitFor('Left to drop by one', async () => (await leftCount(B.page)) === before - 1);
    return `Left ${before} -> ${before - 1} after ${(ms / 1000).toFixed(1)}s`;
  });

  await check("the scout's open, untouched team shows the leader's notes", async () => {
    await openTeam(B.page, numbers[1]);
    await openTeam(A.page, numbers[1]);
    await typeNumber(A.page, 222);
    await A.page.getByRole('button', { name: 'Save', exact: true }).click();
    const { ms } = await waitFor("the leader's value on the scout's form", async () =>
      (await B.page.getByRole('spinbutton').first().inputValue()) === '222' && /teammate just saved notes/.test(await statusOf(B.page)));
    return `after ${(ms / 1000).toFixed(1)}s`;
  });

  await check('with edits in progress the scout keeps them and is warned instead', async () => {
    await typeNumber(B.page, 333);
    await typeNumber(A.page, 444);
    await A.page.getByRole('button', { name: 'Save', exact: true }).click();
    await waitFor('the warning', async () => /Saving yours replaces theirs/.test(await statusOf(B.page)));
    const kept = await B.page.getByRole('spinbutton').first().inputValue();
    if (kept !== '333') throw new Error(`scout's typing replaced: ${kept}`);
  });

  await check('Save & next skips a team the leader finished meanwhile', async () => {
    // Save & next is on the laptop's one-page form; the phone finishes the team in between.
    const [mine, theirs, after] = [numbers[5], numbers[6], numbers[7]];
    await openTeam(A.page, mine);
    await openTeam(B.page, theirs);
    await typeNumber(B.page, 555);
    await B.page.getByRole('button', { name: 'Save', exact: true }).click();
    await waitFor('the save', async () => /Saved pit entry/.test(await statusOf(B.page)));
    // One poll on the laptop, then Save & next.
    await sleep(22_000);
    await typeNumber(A.page, 666);
    await A.page.getByRole('button', { name: 'Save & next' }).click();
    const { value } = await waitFor('the next team to open', async () => {
      const heading = await A.page.getByRole('heading', { name: /^Team \d+$/ }).first().innerText().catch(() => '');
      return heading && heading !== `Team ${mine}` ? heading : '';
    });
    if (value !== `Team ${after}`) throw new Error(`opened ${value}, expected Team ${after} (Team ${theirs} is done)`);
    return `${mine} -> ${after}, skipped ${theirs}`;
  });

  await check("the leader's reassign choice survives an assignments poll", async () => {
    const target = numbers[10];
    await A.page.goto(`${APP}/#/scouting/pit?event=${EVENT}`);
    await A.page.getByRole('tab', { name: /^Left/ }).click();
    await A.page.getByRole('button', { name: `Change who has ${target}` }).click();
    const dialog = A.page.getByRole('dialog', { name: `Team ${target}` });
    await dialog.getByLabel('Assigned to').selectOption({ label: 'Sam' });
    await sleep(25_000);
    const value = await dialog.getByLabel('Assigned to').inputValue();
    if (value !== String(sam.me.id)) throw new Error(`choice reverted to "${value}"`);
    await dialog.getByRole('button', { name: 'Save' }).click();
    const rows = (await api(`/pit-scouting/assignments?event_key=${EVENT}`, { token: lead.access.token })).json.assignments;
    if (rows.find((row) => row.team_key === `frc${target}`)?.member_id !== sam.me.id) throw new Error('assignment not saved');
    const { ms } = await waitFor("the team on the scout's Mine tab", async () => {
      await B.page.goto(`${APP}/#/scouting/pit?event=${EVENT}`);
      // The phone reopens its last team with the list folded away.
      const change = B.page.getByRole('button', { name: 'Change team' });
      if (await change.isVisible({ timeout: 3000 }).catch(() => false)) await change.click();
      await B.page.getByRole('tab', { name: /^Mine/ }).click();
      return B.page.getByRole('button', { name: new RegExp(`^Team ${target}\\b`) }).count();
    });
    return `on Sam's phone after ${(ms / 1000).toFixed(1)}s`;
  });

  await check('simultaneous first note + first photo for the same team: all 200, one row each', async () => {
    const fresh = numbers.slice(12, 20);
    const calls = fresh.flatMap((n) => [
      () => api('/pit-scouting', { token: alex.access.token, body: { event_key: EVENT, team_key: `frc${n}`, payload: { notes: `Alex ${n}` } } }),
      () => api('/pit-scouting/photo', { token: sam.access.token, body: { event_key: EVENT, team_key: `frc${n}`, image_base64: PNG } }),
      () => api('/pit-scouting', { token: lead.access.token, body: { event_key: EVENT, team_key: `frc${n}`, payload: { notes: `Lead ${n}` } } }),
    ]);
    const answers = await Promise.all(calls.map((call) => call()));
    const bad = answers.filter((a) => a.status !== 200);
    if (bad.length) throw new Error(`${bad.length} of ${answers.length} failed: ${bad.map((a) => `${a.status} ${JSON.stringify(a.json).slice(0, 80)}`).join('; ')}`);
    const entries = (await api(`/pit-scouting?event_key=${EVENT}`, { token: lead.access.token })).json.entries;
    for (const n of fresh) {
      const rows = entries.filter((e) => e.team_key === `frc${n}`);
      if (rows.length !== 1) throw new Error(`frc${n}: ${rows.length} rows`);
      if (rows[0].photos.length !== 1 || !/^(Alex|Lead) /.test(rows[0].payload.notes ?? '')) throw new Error(`frc${n}: ${JSON.stringify(rows[0]).slice(0, 160)}`);
    }
    const spread = new Set(answers.map((a) => a.worker));
    return `${answers.length} requests, workers ${[...spread].sort().join(', ')}`;
  });

  await A.ctx.close();
  await B.ctx.close();
} finally {
  await browser.close();
}

console.log('\nWho answered each phone:');
for (const [key, count] of [...workers].sort()) console.log(`  ${key}: ${count} responses`);
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
