import fs from 'node:fs'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ResolvedConfig } from 'vite'
import { ROUTE_PAGES, bootPrefetch, routeAssetMap } from './bootPrefetch'
import { bootSuggestedEventsPath } from '../src/bootPath'

type Hook = (html: string, ctx: { filename: string; bundle?: unknown }) => string

function run(mode: string, command: 'build' | 'serve', filename: string, bundle?: unknown, html = '<html><head></head><body></body></html>'): string {
  const plugin = bootPrefetch()
  const configResolved = plugin.configResolved as (config: ResolvedConfig) => void
  configResolved({ mode, command, envDir: '/nonexistent', base: '/' } as unknown as ResolvedConfig)
  const transform = plugin.transformIndexHtml as { handler: Hook }
  return transform.handler(html, { filename, bundle })
}

const chunk = (fileName: string, facade: string | null, imports: string[] = [], css: string[] = [], isEntry = false) => ({
  type: 'chunk', fileName, facadeModuleId: facade, imports, isEntry, viteMetadata: { importedCss: new Set(css) },
})
const BUNDLE = {
  main: chunk('assets/main.js', '/app/src/main.tsx', ['assets/react.js'], ['assets/main.css'], true),
  react: chunk('assets/react.js', null),
  tc: chunk('assets/TeamCenterPage-1.js', '/app/src/pages/TeamCenterPage.tsx', ['assets/centerUtils.js', 'assets/react.js'], ['assets/tc.css']),
  utils: chunk('assets/centerUtils.js', null, [], ['assets/utils.css']),
  home: chunk('assets/HomePage-1.js', '/app/src/pages/HomePage.tsx', ['assets/centerUtils.js']),
}

describe('bootPrefetch', () => {
  // A developer's .env may point at an absolute local API; production builds use /api.
  afterEach(() => vi.unstubAllEnvs())

  it('starts the shared suggested-events request in a production web build', () => {
    vi.stubEnv('VITE_API_URL', '')
    const html = run('production', 'build', '/app/index.html')
    expect(html).toContain(JSON.stringify(`/api${bootSuggestedEventsPath()}`))
    expect(html.indexOf('<script>')).toBeLessThan(html.indexOf('</head>'))

  })

  it('puts its scripts ahead of the stylesheet, so CSS loading does not hold them', () => {
    vi.stubEnv('VITE_API_URL', '')
    const page = '<html><head><meta charset="UTF-8" /><link rel="stylesheet" href="/assets/main.css"><script type="module" src="/assets/main.js"></script></head></html>'
    const html = run('production', 'build', '/app/index.html', BUNDLE, page)
    expect(html.indexOf('<meta charset')).toBe(html.indexOf('<html><head>') + 12)
    expect(html.indexOf('__frcmobBoot')).toBeLessThan(html.indexOf('rel="stylesheet"'))
    expect(html.indexOf('modulepreload')).toBeLessThan(html.indexOf('rel="stylesheet"'))
  })

  it('leaves the recorder page, native builds and the dev server alone', () => {
    expect(run('production', 'build', '/app/record.html', BUNDLE)).not.toContain('<script>')
    expect(run('native', 'build', '/app/index.html', BUNDLE)).not.toContain('<script>')
    expect(run('development', 'serve', '/app/index.html')).not.toContain('<script>')
    vi.stubEnv('VITE_API_URL', 'https://api.example.com')
    expect(run('production', 'build', '/app/index.html')).not.toContain('__frcmobBoot')
  })

  it("preloads each route's own chunk, its imports and CSS, but nothing the entry loads", () => {
    const map = routeAssetMap(BUNDLE as never)
    expect(map['/team-center']).toEqual({
      js: ['/assets/TeamCenterPage-1.js', '/assets/centerUtils.js'],
      css: ['/assets/tc.css', '/assets/utils.css'],
    })
    expect(map['/home'].js).toEqual(['/assets/HomePage-1.js', '/assets/centerUtils.js'])
    expect(run('production', 'build', '/app/index.html', BUNDLE)).toContain('modulepreload')
  })

  it('knows every page route in RootApp', () => {
    const source = fs.readFileSync(path.resolve(__dirname, '../src/RootApp.tsx'), 'utf8')
    const lazyPages = new Map<string, string>()
    for (const match of source.matchAll(/const (\w+) = (?:import\.meta\.env\.DEV\s*\?\s*)?lazy\(\(\) =>\s*import\('\.\/pages\/(\w+)'\)/g)) {
      lazyPages.set(match[1], match[2])
    }
    const routes = [...source.matchAll(/<Route path="([^"]+)" element=\{withPageSuspense\(<(\w+) \/>/g)]
    expect(routes.length).toBeGreaterThan(15)
    for (const [, route, component] of routes) {
      if (route === '/primitives') continue // dev-only gallery
      expect(ROUTE_PAGES[route], route).toBe(lazyPages.get(component))
    }
  })
})
