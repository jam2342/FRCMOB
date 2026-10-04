import { existsSync, readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';

import { chromium, devices, webkit } from 'playwright';

const baseUrl = process.env.ONDEVICE_BASE_URL || 'http://127.0.0.1:4173';
const browserName = process.env.ONDEVICE_BROWSER || 'chromium';
const iterations = Math.max(1, Number(process.env.ONDEVICE_BENCH_ITERATIONS || 30));
const fullMatchTimeoutMs = Math.max(
  60_000,
  Number(process.env.ONDEVICE_FULL_MATCH_TIMEOUT_MS || 20 * 60_000),
);
const videoPath = process.env.ONDEVICE_MATCH_VIDEO
  ? resolve(process.env.ONDEVICE_MATCH_VIDEO)
  : null;
const calibrationImagePath = process.env.ONDEVICE_CALIBRATION_IMAGE
  ? resolve(process.env.ONDEVICE_CALIBRATION_IMAGE)
  : null;
const requireFullMatch = process.env.ONDEVICE_REQUIRE_FULL_MATCH === 'true';
// Optional reviewed mapping: {"track ID": "frcTEAM"}. Without it, assignments
// are synthetic and exercise only UI/sync shape, never identity accuracy.
const trackIdentities = process.env.ONDEVICE_TRACK_IDENTITIES
  ? JSON.parse(readFileSync(process.env.ONDEVICE_TRACK_IDENTITIES, 'utf8'))
  : null;
const screenshotDir = process.env.ONDEVICE_SCREENSHOT_DIR
  ? resolve(process.env.ONDEVICE_SCREENSHOT_DIR)
  : '/tmp';

if (requireFullMatch && (!videoPath || !calibrationImagePath)) {
  throw new Error('Full-match validation requires ONDEVICE_MATCH_VIDEO and ONDEVICE_CALIBRATION_IMAGE.');
}
if (!['chromium', 'webkit'].includes(browserName)) {
  throw new Error(`ONDEVICE_BROWSER must be chromium or webkit, got ${browserName}`);
}
if (Boolean(videoPath) !== Boolean(calibrationImagePath)) {
  throw new Error('Set both ONDEVICE_MATCH_VIDEO and ONDEVICE_CALIBRATION_IMAGE for a full-match run.');
}
if (videoPath && (!existsSync(videoPath) || !existsSync(calibrationImagePath))) {
  throw new Error('The requested match video or calibration image does not exist.');
}

const browserType = browserName === 'webkit' ? webkit : chromium;
// Headless Chromium resolves navigator.gpu to a software adapter, so a headless
// "WebGPU" timing measures SwiftShader, not a GPU -- it benchmarks slower than
// WASM and means nothing. Set ONDEVICE_HEADLESS=false to launch headed and get a
// real Metal adapter. That is still Mac silicon, not a phone, but it is the only
// way to establish that the WebGPU path works on actual GPU hardware.
const headless = String(process.env.ONDEVICE_HEADLESS ?? 'true') !== 'false';
const launchOptions = browserName === 'chromium'
  ? { args: ['--enable-unsafe-webgpu', '--ignore-gpu-blocklist'] }
  : {};
const browser = await browserType.launch({ headless, ...launchOptions });
const context = await browser.newContext({
  ...devices['iPhone 15'],
  reducedMotion: 'reduce',
});

await context.addInitScript(() => {
  localStorage.setItem('scouting_tutorial_autoplay', 'false');
  localStorage.setItem('scouting_tutorial_seen_v1', JSON.stringify({ scouting: 99 }));
});

const page = await context.newPage();
const browserErrors = [];
page.on('console', (message) => {
  if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
});
page.on('pageerror', (error) => browserErrors.push(`page: ${error.message}`));

let syncedPayload = null;
await page.route('http://localhost:8000/matches/event/**', async (route) => {
  await route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      matches: [{
        match_key: '2026cmptx_sf13m1',
        red: [{ team_key: 'frc868' }, { team_key: 'frc2910' }, { team_key: 'frc2046' }],
        blue: [{ team_key: 'frc5181' }, { team_key: 'frc7769' }, { team_key: 'frc1690' }],
      }],
    }),
  });
});
await page.route('http://localhost:8000/tracks/on-device-session', async (route) => {
  syncedPayload = route.request().postDataJSON();
  const pointsByTeam = syncedPayload?.payload?.pointsByTeam || {};
  const pointsByTeamCounts = Object.fromEntries(
    Object.entries(pointsByTeam).map(([teamKey, points]) => [teamKey, points.length]),
  );
  await route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      ok: true,
      match_key: '2026cmptx_sf13m1',
      event_key: '2026cmptx',
      run_id: 1,
      on_device_session_id: 1,
      session_key: 'browser-validation',
      reused_run: false,
      status: 'provisional',
      quality_score: 0.85,
      quality: { eligible_for_review: true },
      shift1_active_alliance: 'red',
      shift1_source: 'manual_scout_selection',
      team_count: Object.keys(pointsByTeam).length,
      points_persisted: Object.values(pointsByTeamCounts).reduce((sum, count) => sum + count, 0),
      points_by_team: pointsByTeamCounts,
      skipped_unknown_teams: [],
      shift_play: null,
      shift_play_missing_reason: null,
    }),
  });
});

try {
  await page.goto(`${baseUrl}/#/scouting/record`, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.getByRole('heading', { name: 'On-Device Match Breakdown' }).waitFor();
  await page.screenshot({ path: `${screenshotDir}/ondevice-${browserName}-iphone-setup.png`, fullPage: true });

  const benchmark = await page.evaluate(async ({ count }) => {
    const detectorModule = await import('/src/features/onDevice/detector.ts');
    const benchmarkModule = await import('/src/features/onDevice/benchmark.ts');
    const canvas = document.createElement('canvas');
    canvas.width = 1280;
    canvas.height = 720;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('2D canvas unavailable');
    const gradient = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
    gradient.addColorStop(0, '#2563eb');
    gradient.addColorStop(0.5, '#6b7280');
    gradient.addColorStop(1, '#dc2626');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const detector = await detectorModule.createDetector(detectorModule.ON_DEVICE_MODEL_URL);
    const run = () => detectorModule.detectRobots(detector, canvas, canvas.width, canvas.height);
    const summary = await benchmarkModule.benchmark(run, { iterations: count, warmup: 3 });
    let adapterInfo = null;
    try {
      const adapter = navigator.gpu ? await navigator.gpu.requestAdapter() : null;
      if (adapter) {
        const info = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null);
        adapterInfo = info
          ? { vendor: info.vendor, architecture: info.architecture, description: info.description }
          : { vendor: 'unknown', architecture: 'unknown', description: 'unknown' };
      }
    } catch (error) {
      adapterInfo = { error: String(error) };
    }
    return {
      ...summary,
      browserWebGpuExposed: Boolean(navigator.gpu),
      gpuAdapter: adapterInfo,
      executionProvider: detector.executionProvider,
      modelVersion: detectorModule.ON_DEVICE_MODEL_VERSION,
    };
  }, { count: iterations });

  let fullMatch = null;
  if (videoPath && calibrationImagePath) {
    await page.getByLabel('Match key').fill('2026cmptx_sf13m1');
    await page.getByRole('button', { name: 'Load match teams' }).click();
    await page.getByLabel('Alliance with the active hub in Shift 1').selectOption('red');
    await page.getByRole('button', { name: 'Continue to calibration' }).click();

    await page.locator('input[type=file][accept="image/*"]').setInputFiles(calibrationImagePath);
    const canvas = page.locator('.field-calibration canvas');
    await canvas.waitFor();
    const box = await canvas.boundingBox();
    if (!box) throw new Error('Calibration canvas has no layout box');
    const naturalPoints = [
      [78, 430],
      [1202, 430],
      [1262, 632],
      [18, 632],
    ];
    for (const [x, y] of naturalPoints) {
      await canvas.click({ position: { x: (x / 1280) * box.width, y: (y / 720) * box.height } });
    }
    await page.getByRole('button', { name: 'Use this calibration' }).waitFor();
    await page.screenshot({ path: `${screenshotDir}/ondevice-${browserName}-calibration-overlay.png`, fullPage: true });
    await page.getByRole('button', { name: 'Use this calibration' }).click();

    await page.getByRole('tab', { name: 'Upload video' }).click();
    await page.getByLabel('Match starts at video timestamp (seconds)').fill('8');
    const startedAt = Date.now();
    await page.locator('input[type=file][accept="video/*"]').setInputFiles(videoPath);
    try {
      await page.getByText(/tracks? produced\./).waitFor({ timeout: fullMatchTimeoutMs });
    } catch (error) {
      const progress = await page.locator('.video-file-processor').innerText().catch(() => 'progress unavailable');
      await page.screenshot({
        path: `${screenshotDir}/ondevice-${browserName}-full-match-timeout.png`,
        fullPage: true,
        timeout: 5_000,
      }).catch(() => {});
      throw new Error(
        `Full-match processing did not finish in ${fullMatchTimeoutMs}ms. Last UI state: ${progress}`,
        { cause: error },
      );
    }
    const elapsedSec = (Date.now() - startedAt) / 1000;
    const trackSummary = await page.getByText(/tracks? produced\./).innerText();
    const identitySelects = page.locator('select[aria-label^="Assign track"]');
    const trackCount = await identitySelects.count();
    assert(trackCount > 0, 'Full-match workflow produced no tracks');
    for (let index = 0; index < trackCount; index += 1) {
      const select = identitySelects.nth(index);
      const optionValues = await select.locator('option').evaluateAll((options) =>
        options.slice(1).map((option) => option.value),
      );
      assert(optionValues.length > 0, 'Track has no team choices');
      const trackId = (await select.getAttribute('aria-label')).replace(/^Assign track (.+) to a team$/, '$1');
      const team = trackIdentities ? trackIdentities[trackId] : optionValues[index % optionValues.length];
      assert(team && optionValues.includes(team), `Missing or invalid reviewed identity for track ${trackId}`);
      await select.selectOption(team);
    }
    await page.getByRole('button', { name: /Finish & sync/ }).click();
    await page.getByText(/Session provisional · quality 85%/).waitFor();
    assert(syncedPayload, 'Full-match workflow did not submit a session');
    const pointCount = Object.values(syncedPayload.payload.pointsByTeam || {})
      .reduce((sum, points) => sum + points.length, 0);
    assert(pointCount > 0, 'Full-match workflow submitted no track points');
    await page.screenshot({ path: `${screenshotDir}/ondevice-${browserName}-full-match-result.png`, fullPage: true });
    fullMatch = {
      elapsedSec,
      trackSummary,
      trackCount,
      synced: Boolean(syncedPayload),
      payloadMetadata: syncedPayload
        ? Object.fromEntries(Object.entries(syncedPayload.payload).filter(([key]) => key !== 'pointsByTeam'))
        : null,
      pointsByTeam: syncedPayload
        ? Object.fromEntries(Object.entries(syncedPayload.payload.pointsByTeam).map(([key, points]) => [key, points.length]))
        : null,
    };
  }

  const fatalErrors = browserErrors.filter((error) =>
    !error.includes('ERR_CONNECTION_REFUSED')
      && !error.includes('Could not connect to the server')
      && !error.includes('Failed to fetch'),
  );
  console.log(JSON.stringify({
    ok: fatalErrors.length === 0,
    scope: videoPath ? 'workflow-smoke' : 'inference-smoke',
    identitySource: videoPath ? (trackIdentities ? 'provided-annotations' : 'synthetic-assignments') : null,
    serverPersistenceVerified: false,
    scoutingAccuracyVerified: false,
    browser: browserName,
    headless,
    deviceProfile: 'iPhone 15',
    userAgent: await page.evaluate(() => navigator.userAgent),
    benchmark,
    fullMatch,
    browserErrors: fatalErrors,
  }, null, 2));
  if (fatalErrors.length > 0) process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
