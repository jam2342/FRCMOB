// Puts the on-device detector weights in public/models before a build. They aren't in
// git (docs/THIRD_PARTY_ASSETS.md): ONDEVICE_MODEL_SOURCE names the folder that holds
// them and is set only where FRCMOB's own app is built. Without it this does nothing,
// and the build's model check (build/modelAssets.ts) decides whether a missing one matters.
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const sha256 = (data) => createHash('sha256').update(data).digest('hex');

export async function fetchModels({ source, outDir, checksums, fetchImpl = fetch }) {
  const base = source?.trim().replace(/\/+$/, '');
  const fetched = [];
  for (const [name, expected] of Object.entries(checksums)) {
    const target = join(outDir, name);
    if (existsSync(target) && sha256(readFileSync(target)) === expected) continue;
    if (!base) continue;
    // the source URL is a secret, so errors name the file only
    const res = await fetchImpl(`${base}/${name}`);
    if (!res.ok) throw new Error(`fetch-models: ${name} answered HTTP ${res.status}`);
    const body = Buffer.from(await res.arrayBuffer());
    if (sha256(body) !== expected) throw new Error(`fetch-models: ${name} does not match build/modelChecksums.json`);
    mkdirSync(outDir, { recursive: true });
    writeFileSync(`${target}.part`, body);
    renameSync(`${target}.part`, target);
    fetched.push(name);
  }
  return fetched;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const checksums = JSON.parse(readFileSync(join(root, 'build/modelChecksums.json'), 'utf8'));
  const fetched = await fetchModels({
    source: process.env.ONDEVICE_MODEL_SOURCE,
    outDir: join(root, 'public/models'),
    checksums,
  });
  if (fetched.length) console.log(`fetch-models: downloaded ${fetched.join(', ')}`);
}
