// After `vite build`: make dist/ deployable as Cloudflare static assets (wrangler.jsonc).
// - _headers comes from vercel.json, so both hosts send the same headers.
// - .assetsignore keeps files over Cloudflare's 25 MiB per-file limit out: the detector
//   weights (frcmob.com/models/*) and onnxruntime's WebGPU engine
//   (/assets/ort-wasm-simd-threaded.jsep-*.wasm). The API server serves both.
import { readFileSync, readdirSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const ASSET_LIMIT_BYTES = 25 * 1024 * 1024;
export const SERVER_ASSETS = /^ort-wasm-simd-threaded\.jsep-[A-Za-z0-9_-]+\.wasm$/;
// Paths that never reach the assets: Cloudflare sends them to the server.
const SERVER_PATHS = ['/api/', '/models/'];
const VERCEL_ONLY = /^x-vercel-/i;

// Vercel sources are path-to-regexp; the ones we use are exact paths or "/x/(.*)".
export function toCloudflarePath(source) {
  if (source === '/(.*)') return '/*';
  const prefix = source.match(/^(\/[^()*]*\/)\(\.\*\)$/);
  if (prefix) return `${prefix[1]}*`;
  if (/^\/[^()*:]*$/.test(source)) return source;
  throw new Error(`vercel.json header source ${source} has no _headers equivalent`);
}

export function headersFile(vercel) {
  const blocks = [];
  for (const rule of vercel.headers ?? []) {
    if (SERVER_PATHS.some((path) => rule.source.startsWith(path))) continue;
    const lines = rule.headers.filter((h) => !VERCEL_ONLY.test(h.key)).map((h) => `  ${h.key}: ${h.value}`);
    if (rule.source === '/(.*)') lines.push('  Strict-Transport-Security: max-age=31536000');
    if (lines.length) blocks.push(`${toCloudflarePath(rule.source)}\n${lines.join('\n')}`);
  }
  return `${blocks.join('\n\n')}\n`;
}

if (import.meta.url.startsWith('file:') && process.argv[1] === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const dist = `${root}dist`;
  if (!existsSync(`${dist}/index.html`)) throw new Error('Run vite build first: dist/index.html is missing');
  const vercel = JSON.parse(readFileSync(`${root}vercel.json`, 'utf8'));
  writeFileSync(`${dist}/_headers`, headersFile(vercel));
  writeFileSync(`${dist}/.assetsignore`, 'models/\nassets/ort-wasm-simd-threaded.jsep-*.wasm\n.server-assets\n');
  // Anything else this big would fail the upload; say which file and why.
  const tooBig = readdirSync(`${dist}/assets`)
    .filter((name) => !SERVER_ASSETS.test(name) && statSync(`${dist}/assets/${name}`).size > ASSET_LIMIT_BYTES);
  if (tooBig.length) throw new Error(`Over Cloudflare's 25 MiB asset limit: ${tooBig.join(', ')}`);
  const fromServer = readdirSync(`${dist}/assets`).filter((name) => SERVER_ASSETS.test(name));
  writeFileSync(`${dist}/.server-assets`, fromServer.map((name) => `/assets/${name}\n`).join(''));
  console.log(`dist/_headers and dist/.assetsignore written for Cloudflare; from the server: ${fromServer.join(', ') || 'none'}`);
}
