import { createHash } from 'node:crypto'
import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { EMBED_FRAME_ORIGINS } from '../src/config/embedHosts'
import { backendOrigins, buildPolicy, inlineScriptHashes } from './contentSecurityPolicy'

const directive = (policy: string, name: string) =>
  policy.split('; ').find((part) => part.startsWith(`${name} `))?.split(' ').slice(1) ?? []

describe('content security policy', () => {
  it('hashes exactly the inline scripts, not external ones', () => {
    const body = 'window.x=1'
    const html = `<head><script>${body}</script><script type="module" src="/a.js"></script></head>`
    const expected = `'sha256-${createHash('sha256').update(body).digest('base64')}'`
    expect(inlineScriptHashes(html)).toEqual([expected])
  })

  it('allows own scripts, hashed scripts and the signup challenge, and no plugins or base changes', () => {
    const policy = buildPolicy(["'sha256-abc'"], [])
    expect(directive(policy, 'script-src')).toEqual(["'self'", "'wasm-unsafe-eval'", "'sha256-abc'", 'https://challenges.cloudflare.com'])
    expect(directive(policy, 'script-src')).not.toContain("'unsafe-inline'")
    expect(directive(policy, 'object-src')).toEqual(["'none'"])
    expect(directive(policy, 'base-uri')).toEqual(["'self'"])
  })

  it('reaches the API and room socket origins it is given, and nothing else', () => {
    const policy = buildPolicy([], ['/api', 'https://api.example.test/api', 'wss://ws.example.test', 'javascript:alert(1)'])
    expect(directive(policy, 'connect-src')).toEqual(["'self'", 'blob:', 'data:', 'https://api.example.test', 'wss://ws.example.test', 'https://challenges.cloudflare.com'])
  })

  it('frames only the stream players and signup challenge', () => {
    expect(directive(buildPolicy([], []), 'frame-src')).toEqual([
      'https://www.youtube.com', 'https://www.youtube-nocookie.com', 'https://player.twitch.tv', 'https://challenges.cloudflare.com',
    ])
  })

  it('is paired with host headers that refuse framing (a meta CSP cannot)', () => {
    const vercel = JSON.parse(readFileSync(resolve(process.cwd(), 'vercel.json'), 'utf8'))
    const all = vercel.headers.find((rule: { source: string }) => rule.source === '/(.*)')
    const headers = Object.fromEntries(all.headers.map((h: { key: string; value: string }) => [h.key, h.value]))
    expect(headers['Content-Security-Policy']).toBe("frame-ancestors 'none'")
    expect(headers['X-Frame-Options']).toBe('DENY')
    expect(headers['X-Content-Type-Options']).toBe('nosniff')
  })

  it('knows every outside origin the app hard-codes', () => {
    // A new service in the source must be a decision: allow it in the policy
    // (frames: src/config/embedHosts.ts; API/socket hosts: platform/runtime.ts) or,
    // if it is only ever a link or an image, list it here with the reason.
    const LINK_OR_IMAGE_ONLY: Record<string, string> = {
      'github.com': 'link on the legal pages',
      'www.cloudflare.com': 'privacy-policy navigation link; no scripts or frames loaded from this host',
      'www.firstinspires.org': 'trademark-notice link (legalContent.ts)',
      'frc-events.firstinspires.org': 'required "Event Data provided by FIRST" credit link',
      'www.thebluealliance.com': 'required "Powered by The Blue Alliance" credit link',
      'policies.google.com': 'Google Privacy Policy link required by YouTube API Services',
      'www.twitch.tv': 'Twitch terms and privacy links',
      'vercel.com': 'Vercel Web Analytics privacy link',
      'jam2342.github.io': "author's site, a link",
      'i.ytimg.com': 'YouTube thumbnail image (img-src allows https:)',
      'frcmob-api-cache.invalid': 'Cache Storage key prefix, never fetched',
    }
    const allowed = new Set([
      'challenges.cloudflare.com',
      ...EMBED_FRAME_ORIGINS.map((origin) => new URL(origin).host),
      ...backendOrigins(resolve(process.cwd(), 'src/platform/runtime.ts')).map((url) => new URL(url).host),
      ...Object.keys(LINK_OR_IMAGE_ONLY),
    ])
    const files: string[] = []
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, entry.name)
        if (entry.isDirectory()) { if (entry.name !== 'test') walk(path) }
        else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) files.push(path)
      }
    }
    walk(resolve(process.cwd(), 'src'))
    const unknown: string[] = []
    for (const file of files) {
      for (const match of readFileSync(file, 'utf8').matchAll(/\b(?:https?|wss?):\/\/([a-z0-9.-]+\.[a-z]{2,})/gi)) {
        if (!allowed.has(match[1].toLowerCase())) unknown.push(`${match[1]} (${file.replace(process.cwd() + '/', '')})`)
      }
    }
    expect(unknown).toEqual([])
  })
})
