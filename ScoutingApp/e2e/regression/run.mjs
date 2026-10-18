// Runs the regression checks: builds the app, serves it against production's public
// data (read-only, vite.regression.config.ts), and drives it in a browser.
//
//   npm run e2e:regression                    build, serve, run everything
//   npm run e2e:regression -- --only search   checks whose name contains "search"
//   REGRESSION_BASE=http://127.0.0.1:5199 npm run e2e:regression   use a running server
//   PW_CHANNEL=chrome npm run e2e:regression  real Chrome instead of Playwright's Chromium
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../..', import.meta.url));
const OUT = fileURLToPath(new URL('./out', import.meta.url));
const PORT = 5210;
const CHECK_TIMEOUT_MS = 180_000;
const args = process.argv.slice(2);
const only = args.includes('--only') ? args[args.indexOf('--only') + 1]?.toLowerCase() : '';

let server = null;
if (!process.env.REGRESSION_BASE) {
  process.env.REGRESSION_BASE = `http://127.0.0.1:${PORT}`;
  const build = spawnSync('npx', ['vite', 'build', '--config', 'vite.regression.config.ts', '--outDir', '.regression-dist', '--emptyOutDir'], {
    cwd: ROOT,
    stdio: 'inherit',
    env: { ...process.env, VITE_API_URL: '/api' },
  });
  if (build.status !== 0) process.exit(build.status ?? 1);
  server = spawn('npx', ['vite', 'preview', '--config', 'vite.regression.config.ts', '--outDir', '.regression-dist', '--host', '127.0.0.1', '--port', String(PORT), '--strictPort'], {
    cwd: ROOT,
    stdio: 'ignore',
  });
  const ready = Date.now() + 30_000;
  for (;;) {
    try {
      if ((await fetch(process.env.REGRESSION_BASE)).ok) break;
    } catch {
      // not up yet
    }
    if (Date.now() > ready) {
      console.error('Preview server did not start');
      server.kill();
      process.exit(1);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

// Imported after REGRESSION_BASE is settled: the checks read it when they load.
const { launch } = await import('./lib.mjs');
const suites = {
  scouting: (await import('./scouting.mjs')).default,
  navigation: (await import('./navigation.mjs')).default,
  network: (await import('./network.mjs')).default,
};
const checks = Object.entries(suites)
  .flatMap(([area, list]) => list.map((item) => ({ area, ...item })))
  .filter((item) => !only || item.name.toLowerCase().includes(only) || item.area === only);

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });
const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 60);

async function attempt(browser, item) {
  let timer;
  try {
    await Promise.race([
      item.run(browser),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`took over ${CHECK_TIMEOUT_MS / 1000}s`)), CHECK_TIMEOUT_MS); }),
    ]);
    return null;
  } catch (error) {
    let index = 0;
    for (const ctx of browser.contexts()) {
      for (const page of ctx.pages()) {
        await page.screenshot({ path: `${OUT}/${slug(item.name)}-${index += 1}.png` }).catch(() => {});
      }
    }
    return error;
  } finally {
    clearTimeout(timer);
    for (const ctx of browser.contexts()) await ctx.close().catch(() => {});
  }
}

const browser = await launch();
const results = [];
for (const item of checks) {
  const started = Date.now();
  let error = await attempt(browser, item);
  let retried = false;
  // Live data and timing can blip once; a real regression fails twice.
  if (error) {
    retried = true;
    error = await attempt(browser, item);
  }
  const seconds = Math.round((Date.now() - started) / 1000);
  results.push({ ...item, error, retried, seconds });
  const mark = error ? 'FAIL' : retried ? 'pass (on retry)' : 'pass';
  console.log(`${mark.padEnd(16)} [${item.area}] ${item.name} (${seconds}s)${error ? `\n                 ${error.message}` : ''}`);
}
await browser.close();
if (server) server.kill();

const failed = results.filter((result) => result.error);
console.log(`\n${results.length - failed.length}/${results.length} passed${failed.length ? `; screenshots of failures in ${OUT}` : ''}`);
process.exit(failed.length ? 1 : 0);
