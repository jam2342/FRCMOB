import assert from 'node:assert/strict';
import { readFile, readdir, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadEnv } from 'vite';

const root = fileURLToPath(new URL('../', import.meta.url));
const dir = join(root, 'dist-native');
const required = ['index.html', 'record.html', 'models/frc_robot_detector_v4_s.onnx', 'models/frc_robot_detector_v4_n.onnx'];
for (const name of required) assert.ok((await stat(join(dir, name))).size > 0, `Missing native asset: ${name}`);
for (const name of required.filter(name => name.endsWith('.onnx'))) {
  const digest = data => createHash('sha256').update(data).digest('hex');
  assert.equal(digest(await readFile(join(dir, name))), digest(await readFile(join(root, 'public', name))), `Bundled model changed: ${name}`);
}
const assets = await readdir(join(dir, 'assets'));
assert.ok(assets.some(name => /^ort-wasm.*\.wasm$/.test(name)), 'Missing offline detector runtime');
const env = loadEnv('native', root, 'VITE_');
const useLocalOpticalFlow = String(process.env.VITE_ONDEVICE_LOCAL_OPTICAL_FLOW || env.VITE_ONDEVICE_LOCAL_OPTICAL_FLOW || 'true').toLowerCase() !== 'false';
if (!useLocalOpticalFlow) {
  // The dynamic URL import also emits a tiny shim; require the engine itself.
  const trackerSizes = await Promise.all(assets.filter(name => /^opencv-.*\.js$/.test(name)).map(async name => (await stat(join(dir, 'assets', name))).size));
  assert.ok(trackerSizes.some(size => size > 1024 * 1024), 'Missing offline field tracker');
}
assert.ok(assets.some(name => /^OnDeviceRunPage-.*\.js$/.test(name)), 'Missing recorder route');
const html = await readFile(join(dir, 'index.html'), 'utf8');
assert.ok(html.includes('viewport-fit=cover'), 'Missing safe-area viewport');
assert.ok(!html.includes('localhost:5173'), 'Native bundle points to a development server');
console.log(JSON.stringify({ passed: true, modelsMatchSource: true, detectorRuntimeInstalled: true, recorderInstalled: true, bundledInterface: true }));
