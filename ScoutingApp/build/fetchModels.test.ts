import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchModels } from '../scripts/fetch-models.mjs';
import checksums from './modelChecksums.json';
import { BUNDLED_MODELS } from '../src/features/onDevice/modelArtifact';

const sha256 = (data: string) => createHash('sha256').update(data).digest('hex');
const answer = (body: string, status = 200) => ({
  ok: status < 400, status, arrayBuffer: async () => new TextEncoder().encode(body).buffer,
});

describe('detector download before a build', () => {
  let outDir: string;
  beforeEach(() => { outDir = mkdtempSync(join(tmpdir(), 'frcmob-fetch-models-')); });
  afterEach(() => rmSync(outDir, { recursive: true, force: true }));

  it('pins exactly the models the browser loads', () => {
    expect(Object.keys(checksums).sort()).toEqual(BUNDLED_MODELS.map((m) => m.url.replace('/models/', '')).sort());
  });
  it('downloads a missing model and checks it', async () => {
    const fetchImpl = vi.fn(async () => answer('weights'));
    const got = await fetchModels({ source: 'https://host/secret/', outDir, checksums: { 'a.onnx': sha256('weights') }, fetchImpl });
    expect(got).toEqual(['a.onnx']);
    expect(fetchImpl).toHaveBeenCalledWith('https://host/secret/a.onnx');
    expect(readFileSync(join(outDir, 'a.onnx'), 'utf8')).toBe('weights');
  });
  it('keeps a file that already matches, and does nothing without a source', async () => {
    writeFileSync(join(outDir, 'a.onnx'), 'weights');
    const fetchImpl = vi.fn();
    await fetchModels({ source: 'https://host/x', outDir, checksums: { 'a.onnx': sha256('weights') }, fetchImpl });
    await fetchModels({ source: '', outDir, checksums: { 'b.onnx': sha256('other') }, fetchImpl });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(existsSync(join(outDir, 'b.onnx'))).toBe(false);
  });
  it('refuses a wrong file or a failed answer without naming the source', async () => {
    const wrong = fetchModels({ source: 'https://host/secret', outDir, checksums: { 'a.onnx': sha256('weights') }, fetchImpl: async () => answer('tampered') });
    await expect(wrong).rejects.toThrow('a.onnx does not match');
    expect(existsSync(join(outDir, 'a.onnx'))).toBe(false);
    const missing = fetchModels({ source: 'https://host/secret', outDir, checksums: { 'a.onnx': 'x' }, fetchImpl: async () => answer('', 404) });
    await expect(missing).rejects.toThrow(/^fetch-models: a\.onnx answered HTTP 404$/);
  });
});
