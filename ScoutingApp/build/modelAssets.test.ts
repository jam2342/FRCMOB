import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resolveConfig } from 'vite';
import { modelAssetExists, validateOnDeviceModel } from './modelAssets';
import { CPU_MODEL, GPU_MODEL, resolveModelUrl } from '../src/features/onDevice/modelArtifact';

describe('release model validation', () => {
  let root: string;
  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'frcmob-model-build-'));
    mkdirSync(join(root, 'public/models'), { recursive: true });
    vi.stubEnv('ONDEVICE_MODEL_REQUIRED', 'true');
    vi.stubEnv('VITE_ONDEVICE_MODEL_URL', '');
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    rmSync(root, { recursive: true, force: true });
  });
  const buildConfig = (envFile?: false) => resolveConfig({
    root, mode: 'production', configFile: false, envFile, plugins: [validateOnDeviceModel()], logLevel: 'silent',
  }, 'build');

  it('rejects absent and old-only models', async () => {
    await expect(buildConfig(false)).rejects.toThrow(GPU_MODEL.url);
    writeFileSync(join(root, 'public/models/frc_robot_detector_v2.onnx'), 'old fixture');
    await expect(buildConfig(false)).rejects.toThrow(GPU_MODEL.url);
  });
  it('requires both bundled models, the same ones the browser loads', async () => {
    writeFileSync(join(root, 'public', GPU_MODEL.url), 'selected fixture');
    // the CPU model is still missing
    await expect(buildConfig(false)).rejects.toThrow(CPU_MODEL.url);
    writeFileSync(join(root, 'public', CPU_MODEL.url), 'selected fixture');
    await expect(buildConfig(false)).resolves.toBeDefined();
    expect(resolveModelUrl('  ')).toBeNull();
  });
  it('validates a configured local file rather than accepting any nonempty URL', async () => {
    vi.stubEnv('VITE_ONDEVICE_MODEL_URL', '/models/custom.onnx');
    await expect(buildConfig(false)).rejects.toThrow('/models/custom.onnx');
    writeFileSync(join(root, 'public/models/custom.onnx'), 'custom fixture');
    await expect(buildConfig(false)).resolves.toBeDefined();
    expect(modelAssetExists(join(root, 'public'), '/../outside.onnx')).toBe(false);
  });
  it('uses Vite env-file settings and permits an explicit remote URL', async () => {
    vi.stubEnv('VITE_ONDEVICE_MODEL_URL', undefined);
    vi.stubEnv('ONDEVICE_MODEL_REQUIRED', undefined);
    writeFileSync(join(root, '.env.production'),
      'VITE_ONDEVICE_MODEL_URL=https://example.invalid/model.onnx\nONDEVICE_MODEL_REQUIRED=true\n');
    const config = await buildConfig();
    expect(config.env.VITE_ONDEVICE_MODEL_URL).toBe('https://example.invalid/model.onnx');
    writeFileSync(join(root, '.env.production'), 'ONDEVICE_MODEL_REQUIRED=true\n');
    await expect(buildConfig()).rejects.toThrow(GPU_MODEL.url);
  });
  it('requires the model on Vercel unless explicitly opted out', async () => {
    vi.stubEnv('ONDEVICE_MODEL_REQUIRED', undefined);
    vi.stubEnv('VERCEL', '');
    await expect(buildConfig(false)).resolves.toBeDefined();
    vi.stubEnv('VERCEL', '1');
    await expect(buildConfig(false)).rejects.toThrow(GPU_MODEL.url);
    vi.stubEnv('ONDEVICE_MODEL_REQUIRED', 'false');
    await expect(buildConfig(false)).resolves.toBeDefined();
  });
});
