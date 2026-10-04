import { existsSync, statSync } from 'node:fs';
import { relative, resolve, sep } from 'node:path';
import { loadEnv, type Plugin } from 'vite';
import { BUNDLED_MODELS, resolveModelUrl } from '../src/features/onDevice/modelArtifact';

export function modelAssetExists(publicDir: string | false, url: string): boolean {
  // Remote availability is checked by release smoke tests, not a network build step.
  if (/^https?:\/\//i.test(url)) return true;
  if (!publicDir || !url.startsWith('/') || url.startsWith('//')) return false;
  const filename = resolve(publicDir, url.slice(1));
  const within = relative(publicDir, filename);
  return within !== '..' && !within.startsWith(`..${sep}`)
    && existsSync(filename) && statSync(filename).isFile();
}

export function validateOnDeviceModel(): Plugin {
  return {
    name: 'validate-on-device-model',
    apply: 'build',
    configResolved(config) {
      // a configured URL stands in for both bundled models; otherwise both must ship
      const configured = resolveModelUrl(config.env.VITE_ONDEVICE_MODEL_URL);
      const missing = (configured ? [configured] : BUNDLED_MODELS.map((m) => m.url))
        .filter((u) => !modelAssetExists(config.publicDir, u));
      if (missing.length === 0) return;
      const url = missing.join(' and ');
      const fileEnv = config.inlineConfig.envFile === false ? {} : loadEnv(config.mode, config.envDir, 'ONDEVICE_MODEL_REQUIRED');
      // A Vercel build is a release: warning there is how production went without
      // on-device detection for a month unnoticed. Opt out with ONDEVICE_MODEL_REQUIRED=false.
      const required = process.env.ONDEVICE_MODEL_REQUIRED ?? fileEnv.ONDEVICE_MODEL_REQUIRED
        ?? (process.env.VERCEL ? 'true' : undefined);
      const message = `Missing on-device detector at ${url}. Provide that public asset or set VITE_ONDEVICE_MODEL_URL to a public CORS-enabled URL.`;
      if (required?.trim() === 'true') throw new Error(message);
      config.logger.warn(`${message} Building with on-device detection unavailable.`);
    },
  };
}
