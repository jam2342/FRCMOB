import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { loadEnv, type Plugin } from 'vite'
import { EMBED_FRAME_ORIGINS } from '../src/config/embedHosts'

// The site's Content-Security-Policy, as a <meta> tag the build writes into each page.
// A meta tag rather than a host header because the boot script inlined by
// bootPrefetch changes with every build, and the policy allows it by its hash; it
// also keeps the policy with the app whichever host serves it. frame-ancestors
// can't live in a meta tag, so framing is refused by the host headers (vercel.json).

const INLINE_SCRIPT_RE = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi

export function inlineScriptHashes(html: string): string[] {
  const hashes: string[] = []
  for (const match of html.matchAll(INLINE_SCRIPT_RE)) {
    hashes.push(`'sha256-${createHash('sha256').update(match[1], 'utf8').digest('base64')}'`)
  }
  return hashes
}

function origin(url: string): string | null {
  try {
    const parsed = new URL(url)
    return /^(https?|wss?):$/.test(parsed.protocol) ? parsed.origin : null
  } catch {
    return null
  }
}

// The backend the browser talks to directly (the room socket can't go through
// the host's rewrite). One source: the constants the app itself uses.
export function backendOrigins(runtimePath?: string): string[] {
  const runtime = readFileSync(runtimePath ?? fileURLToPath(new URL('../src/platform/runtime.ts', import.meta.url)), 'utf8')
  return [...runtime.matchAll(/NATIVE_(?:API|WS)_URL = '([^']+)'/g)].map((m) => m[1])
}

export function buildPolicy(scriptHashes: string[], connectUrls: string[]): string {
  const connect = [...new Set(connectUrls.map(origin).filter((value): value is string => Boolean(value)))]
  const directives: Array<[string, string[]]> = [
    ['default-src', ["'self'"]],
    // wasm-unsafe-eval: the recorder's detector (onnxruntime) compiles WebAssembly.
    ['script-src', ["'self'", "'wasm-unsafe-eval'", ...scriptHashes, 'https://challenges.cloudflare.com']],
    ['worker-src', ["'self'", 'blob:']],
    // React style props and a few printed reports set inline styles.
    ['style-src', ["'self'", "'unsafe-inline'"]],
    // Team logos and robot photos come from wherever TBA lists them.
    ['img-src', ["'self'", 'data:', 'blob:', 'https:']],
    ['font-src', ["'self'", 'data:']],
    ['media-src', ["'self'", 'blob:']],
    ['connect-src', ["'self'", 'blob:', 'data:', ...connect, 'https://challenges.cloudflare.com']],
    ['frame-src', [...EMBED_FRAME_ORIGINS, 'https://challenges.cloudflare.com']],
    ['manifest-src', ["'self'"]],
    ['object-src', ["'none'"]],
    ['base-uri', ["'self'"]],
    ['form-action', ["'self'"]],
  ]
  return directives.map(([name, values]) => `${name} ${values.join(' ')}`).join('; ')
}

export function contentSecurityPolicy(): Plugin {
  let enabled = false
  let connectUrls: string[] = []
  return {
    name: 'frcmob-content-security-policy',
    configResolved(config) {
      // Web builds only: dev injects its own inline scripts, and the native shell
      // serves from capacitor:// with different rules.
      enabled = config.command === 'build' && config.mode !== 'native'
      const env = loadEnv(config.mode, config.envDir || process.cwd(), ['VITE_', 'NEXT_PUBLIC_'])
      connectUrls = [
        env.VITE_API_URL, env.NEXT_PUBLIC_API_URL, env.VITE_WS_URL, env.NEXT_PUBLIC_WS_URL,
        ...backendOrigins(),
      ].filter((value): value is string => Boolean(value && value.trim()))
    },
    transformIndexHtml: {
      // After bootPrefetch (also 'post', earlier in the plugin list) has inlined its scripts.
      order: 'post',
      handler(html) {
        if (!enabled) return html
        const policy = buildPolicy(inlineScriptHashes(html), connectUrls)
        const tag = `<meta http-equiv="Content-Security-Policy" content="${policy}" />`
        // Ahead of every script, or the browser runs them before it knows the policy.
        const charset = html.match(/<meta charset=[^>]*>/i)
        if (charset?.index === undefined) throw new Error('index.html needs <meta charset> for the CSP tag')
        const at = charset.index + charset[0].length
        return `${html.slice(0, at)}\n    ${tag}${html.slice(at)}`
      },
    },
  }
}
