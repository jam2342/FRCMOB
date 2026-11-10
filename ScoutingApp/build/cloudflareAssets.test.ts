import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { headersFile, toCloudflarePath } from '../scripts/cloudflare-assets.mjs';

const vercel = JSON.parse(readFileSync(resolve(process.cwd(), 'vercel.json'), 'utf8'));

describe('Cloudflare _headers from vercel.json', () => {
  it('maps the path patterns we use', () => {
    expect(toCloudflarePath('/(.*)')).toBe('/*');
    expect(toCloudflarePath('/assets/(.*)')).toBe('/assets/*');
    expect(toCloudflarePath('/record.html')).toBe('/record.html');
    expect(() => toCloudflarePath('/x/:id')).toThrow();
  });

  it('keeps the recorder isolation, asset caching and framing rules', () => {
    const file = headersFile(vercel);
    expect(file).toMatch(/^\/record\.html\n {2}Cross-Origin-Opener-Policy: same-origin\n {2}Cross-Origin-Embedder-Policy: require-corp$/m);
    expect(file).toMatch(/^\/assets\/\*\n {2}Cache-Control: public, max-age=31536000, immutable\n {2}Cross-Origin-Embedder-Policy: require-corp$/m);
    expect(file).toContain("  Content-Security-Policy: frame-ancestors 'none'");
    expect(file).toContain('  X-Frame-Options: DENY');
    expect(file).toContain('  Strict-Transport-Security: max-age=31536000');
  });

  it('leaves out paths the server answers and Vercel-only headers', () => {
    const file = headersFile(vercel);
    expect(file).not.toMatch(/^\/api\//m);
    expect(file).not.toMatch(/^\/models\//m);
    expect(file).not.toMatch(/x-vercel/i);
  });
});
